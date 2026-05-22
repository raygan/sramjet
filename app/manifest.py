"""Manifest parse, serialize, and diff utilities.

RetroArch manifest format:
  JSON array of {"path": str, "hash": str} objects, sorted by path.
  An empty-string hash ("") means the file was deleted.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

ManifestEntry = dict[str, str]  # {"path": str, "hash": str}
Manifest = list[ManifestEntry]


def parse(raw: bytes | str) -> Manifest:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("Manifest must be a JSON array")
    if not all(isinstance(e, dict) and "path" in e and "hash" in e for e in data):
        raise ValueError("Manifest entries must have 'path' and 'hash' keys")
    return sorted(data, key=lambda e: e["path"])


def serialize(manifest: Manifest) -> bytes:
    sorted_manifest = sorted(manifest, key=lambda e: e["path"])
    return json.dumps(sorted_manifest, indent=2).encode()


def to_dict(manifest: Manifest) -> dict[str, str]:
    """Convert manifest list to {path: hash} dict for fast lookup."""
    return {e["path"]: e["hash"] for e in manifest}


def from_dict(d: dict[str, str]) -> Manifest:
    return sorted([{"path": p, "hash": h} for p, h in d.items()], key=lambda e: e["path"])


def is_deleted(hash: str) -> bool:
    """Empty string hash means the file was deleted in RetroArch's convention."""
    return hash == ""


def load_canonical(canonical_path: Path) -> Manifest:
    if not canonical_path.exists():
        return []
    return parse(canonical_path.read_bytes())


def save_canonical(canonical_path: Path, manifest: Manifest) -> None:
    """Write manifest atomically using a temp file + os.replace().

    Prevents partial writes from corrupting the canonical manifest if the
    process crashes mid-write, and eliminates the read-modify-write race
    condition when two syncs run concurrently.
    """
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    data = serialize(manifest)
    fd, tmp = tempfile.mkstemp(dir=canonical_path.parent, prefix=".tmp-")
    try:
        os.write(fd, data)
        os.close(fd)
        os.replace(tmp, canonical_path)
    except Exception:
        os.close(fd)
        os.unlink(tmp)
        raise


def diff(
    server: Manifest,
    incoming: Manifest,
) -> dict[str, Any]:
    """
    Compare incoming manifest against server (canonical) manifest.

    Returns a dict with:
      - added:    paths in incoming but not in server
      - modified: paths in both but with different hashes
      - deleted:  paths where incoming has empty-string hash
      - unchanged: paths where hashes match
    """
    server_dict = to_dict(server)
    incoming_dict = to_dict(incoming)

    added = []
    modified = []
    deleted = []
    unchanged = []

    for path, hash in incoming_dict.items():
        if is_deleted(hash):
            deleted.append(path)
        elif path not in server_dict:
            added.append(path)
        elif server_dict[path] != hash:
            modified.append(path)
        else:
            unchanged.append(path)

    return {
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "unchanged": unchanged,
    }
