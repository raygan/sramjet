import os
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

STORE_DIR = DATA_DIR / "store"
MANIFESTS_DIR = DATA_DIR / "manifests"
SNAPSHOTS_DIR = MANIFESTS_DIR / "snapshots"
DEVICES_DIR = DATA_DIR / "devices"

CANONICAL_MANIFEST = MANIFESTS_DIR / "canonical.json"

DATABASE_URL = f"sqlite+aiosqlite:///{DATA_DIR}/sramjet.db"

# Versioning retention limits (number of old versions kept; 0 = unlimited)
SYSTEM_VERSION_LIMIT = int(os.environ.get("SYSTEM_VERSION_LIMIT", "5"))
THUMBNAIL_VERSION_LIMIT = int(os.environ.get("THUMBNAIL_VERSION_LIMIT", "3"))
SAVES_VERSION_LIMIT = int(os.environ.get("SAVES_VERSION_LIMIT", "0"))
STATES_VERSION_LIMIT = int(os.environ.get("STATES_VERSION_LIMIT", "0"))

# Sync event grouping — requests within this many seconds of each other
# from the same device are grouped into one sync event.
SYNC_EVENT_WINDOW_SECONDS = int(os.environ.get("SYNC_EVENT_WINDOW_SECONDS", "30"))

# Directories with limited history (limit=0 means unlimited — not added to dict)
LIMITED_HISTORY_DIRS: dict[str, int] = {
    "system": SYSTEM_VERSION_LIMIT,
    "thumbnails": THUMBNAIL_VERSION_LIMIT,
}
if SAVES_VERSION_LIMIT > 0:
    LIMITED_HISTORY_DIRS["saves"] = SAVES_VERSION_LIMIT
if STATES_VERSION_LIMIT > 0:
    LIMITED_HISTORY_DIRS["states"] = STATES_VERSION_LIMIT


DISPLAY_TZ = ZoneInfo(os.environ.get("DISPLAY_TZ", "America/Chicago"))

# Maximum upload size in bytes (0 = unlimited). Protects against OOM from
# huge state files. Default: 256 MB, which covers even the largest emulator states.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(256 * 1024 * 1024)))

# Optional HTTP Basic auth. Each pair is independent — set both vars in a pair
# to enable auth for that surface. If unset, the surface remains open.
UI_USERNAME = os.environ.get("AUTH_UI_USERNAME")
UI_PASSWORD = os.environ.get("AUTH_UI_PASSWORD")
UI_AUTH_ENABLED = bool(UI_USERNAME and UI_PASSWORD)

WEBDAV_USERNAME = os.environ.get("AUTH_WEBDAV_USERNAME")
WEBDAV_PASSWORD = os.environ.get("AUTH_WEBDAV_PASSWORD")
WEBDAV_AUTH_ENABLED = bool(WEBDAV_USERNAME and WEBDAV_PASSWORD)


def _parse_mister_cores(raw: str) -> dict[str, str]:
    """Parse MISTER_CORES ('snes=bsnes, gba=mGBA') into {system: core dir}."""
    out: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" in pair:
            system, core = pair.split("=", 1)
            out[system.strip().lower()] = core.strip()
    return out


# Per-system RetroArch core folder overrides for MiSTer sync path mapping.
MISTER_CORES = _parse_mister_cores(os.environ.get("MISTER_CORES", ""))


def ensure_dirs() -> None:
    for d in (STORE_DIR, SNAPSHOTS_DIR, DEVICES_DIR):
        d.mkdir(parents=True, exist_ok=True)
