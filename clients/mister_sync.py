#!/usr/bin/env python3
"""SRAMjet sync client for MiSTer FPGA.

Syncs /media/fat/saves with a SRAMjet server. Install to /media/fat/Scripts/
and run from the MiSTer Scripts menu, or add a cron entry for periodic sync.

Python 3 standard library only — no dependencies. Download a pre-configured
copy from your SRAMjet dashboard (Help page), or edit the constants below.
"""

import base64
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SERVER_URL = "{{SERVER_URL}}"
DEVICE_NAME = "{{DEVICE_NAME}}"
SAVES_ROOT = Path("/media/fat/saves")

# Set these if your SRAMjet server has WebDAV auth enabled
# (AUTH_WEBDAV_USERNAME / AUTH_WEBDAV_PASSWORD).
AUTH_USER = ""
AUTH_PASS = ""

# MiSTer save directories the server knows how to map. The download endpoint
# substitutes the server's actual mapping; the fallback covers a raw copy.
_SYNC_DIRS_JSON = "{{MISTER_DIRS}}"
try:
    SYNC_DIRS = json.loads(_SYNC_DIRS_JSON)
except ValueError:
    SYNC_DIRS = ["NES", "SNES", "GAMEBOY", "GBC", "SGB", "GBA",
                 "Genesis", "MegaDrive", "SMS", "GameGear", "PSX"]

STATE_FILE = SAVES_ROOT / ".sramjet_state.json"


# ─── Sync planning (pure logic, unit-testable) ────────────────────────────────


def plan_sync(manifest: list[dict], local: dict[str, str], state: dict[str, dict],
              local_mtimes: dict[str, float]) -> dict[str, list[str]]:
    """Decide what to do for every path.

    manifest:     server entries [{path, hash, mtime}] (hash "" = deleted)
    local:        {path: md5} of local files
    state:        {path: {"local": md5, "canonical": hash}} from last sync
    local_mtimes: {path: unix mtime} for first-contact conflict resolution

    Returns {"upload": [...], "download": [...], "delete_remote": [...],
             "delete_local": [...]}
    """
    server = {e["path"]: e for e in manifest}
    plan = {"upload": [], "download": [], "delete_remote": [], "delete_local": []}

    for path in sorted(set(server) | set(local) | set(state)):
        server_hash = server[path]["hash"] if path in server else None
        local_hash = local.get(path)
        st = state.get(path, {})
        local_changed = local_hash != st.get("local")
        server_changed = server_hash != st.get("canonical")
        server_gone = server_hash in (None, "")

        if not local_changed and not server_changed:
            continue
        if local_changed and not server_changed:
            if local_hash is None:
                if not server_gone:
                    plan["delete_remote"].append(path)
            else:
                plan["upload"].append(path)
        elif server_changed and not local_changed:
            if server_gone:
                if local_hash is not None:
                    plan["delete_local"].append(path)
            else:
                plan["download"].append(path)
        else:
            # Both sides changed since last sync (or first contact, no state).
            if local_hash is None:
                if not server_gone:
                    plan["download"].append(path)
            elif server_gone:
                plan["upload"].append(path)  # re-create: local progress wins
            else:
                # True conflict — most recently modified side wins.
                if local_mtimes.get(path, 0) >= server[path].get("mtime", 0):
                    plan["upload"].append(path)
                else:
                    plan["download"].append(path)
    return plan


# ─── HTTP ─────────────────────────────────────────────────────────────────────


def _request(method: str, url: str, data: bytes | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, method=method)
    if AUTH_USER or AUTH_PASS:
        token = base64.b64encode(f"{AUTH_USER}:{AUTH_PASS}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    if data is not None:
        req.add_header("Content-Type", "application/octet-stream")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _file_url(path: str) -> str:
    return f"{SERVER_URL}/mister/{urllib.parse.quote(DEVICE_NAME)}/files/{urllib.parse.quote(path)}"


# ─── Local scanning and state ─────────────────────────────────────────────────


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def scan_local() -> tuple[dict[str, str], dict[str, float]]:
    hashes: dict[str, str] = {}
    mtimes: dict[str, float] = {}
    for dir_name in SYNC_DIRS:
        directory = SAVES_ROOT / dir_name
        if not directory.is_dir():
            continue
        for f in directory.iterdir():
            if f.is_file() and f.suffix == ".sav":
                path = f"{dir_name}/{f.name}"
                hashes[path] = md5(f.read_bytes())
                mtimes[path] = f.stat().st_mtime
    return hashes, mtimes


def load_state() -> dict[str, dict]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict[str, dict]) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(STATE_FILE)


# ─── Sync execution ───────────────────────────────────────────────────────────


def run_sync(dry_run: bool = False) -> int:
    manifest = json.loads(_request("GET", f"{SERVER_URL}/mister/{urllib.parse.quote(DEVICE_NAME)}/manifest"))
    server = {e["path"]: e for e in manifest}
    local, mtimes = scan_local()
    state = load_state()

    plan = plan_sync(manifest, local, state, mtimes)
    changes = sum(len(v) for v in plan.values())

    # Drop state entries for paths that vanished on both sides.
    stale = [p for p in state if p not in server and p not in local]
    for p in stale:
        state.pop(p)

    if changes == 0:
        if stale and not dry_run:
            save_state(state)
        print("Already in sync — nothing to do.")
        return 0

    for kind in ("upload", "download", "delete_remote", "delete_local"):
        for path in plan[kind]:
            print(f"{kind:>13}  {path}")
    if dry_run:
        print(f"\nDry run — {changes} change(s) not applied.")
        return 0

    for path in plan["upload"]:
        data = (SAVES_ROOT / path).read_bytes()
        resp = json.loads(_request("PUT", _file_url(path), data))
        state[path] = {"local": md5(data), "canonical": resp["canonical_hash"]}

    for path in plan["delete_remote"]:
        _request("DELETE", _file_url(path))
        state.pop(path, None)

    for path in plan["download"]:
        data = _request("GET", _file_url(path))
        target = SAVES_ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        state[path] = {"local": md5(data), "canonical": server[path]["hash"]}

    for path in plan["delete_local"]:
        (SAVES_ROOT / path).unlink(missing_ok=True)
        state.pop(path, None)

    # Close the sync so uploads/deletes are promoted to canonical.
    if plan["upload"] or plan["delete_remote"]:
        _request("POST", f"{SERVER_URL}/mister/{urllib.parse.quote(DEVICE_NAME)}/complete")

    save_state(state)
    print(f"\nDone — {changes} change(s) applied.")
    return 0


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    try:
        return run_sync(dry_run=dry_run)
    except urllib.error.URLError as e:
        print(f"ERROR: cannot reach SRAMjet server at {SERVER_URL}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
