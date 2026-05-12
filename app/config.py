import os
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

STORE_DIR = DATA_DIR / "store"
MANIFESTS_DIR = DATA_DIR / "manifests"
SNAPSHOTS_DIR = MANIFESTS_DIR / "snapshots"
DEVICES_DIR = DATA_DIR / "devices"
CONFLICTS_DIR = DATA_DIR / "conflicts"

CANONICAL_MANIFEST = MANIFESTS_DIR / "canonical.json"

DATABASE_URL = f"sqlite+aiosqlite:///{DATA_DIR}/sramjet.db"

# Versioning retention limits (number of old versions kept)
SYSTEM_VERSION_LIMIT = int(os.environ.get("SYSTEM_VERSION_LIMIT", "5"))
THUMBNAIL_VERSION_LIMIT = int(os.environ.get("THUMBNAIL_VERSION_LIMIT", "3"))

# Sync event grouping — requests within this many seconds of each other
# from the same device are grouped into one sync event.
SYNC_EVENT_WINDOW_SECONDS = int(os.environ.get("SYNC_EVENT_WINDOW_SECONDS", "30"))

# Directories that get full version history
FULL_HISTORY_DIRS = {"saves", "states", "config"}

# Directories with limited history
LIMITED_HISTORY_DIRS = {
    "system": SYSTEM_VERSION_LIMIT,
    "thumbnails": THUMBNAIL_VERSION_LIMIT,
}


DISPLAY_TZ = ZoneInfo(os.environ.get("DISPLAY_TZ", "America/Chicago"))


def ensure_dirs() -> None:
    for d in (STORE_DIR, SNAPSHOTS_DIR, DEVICES_DIR, CONFLICTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
