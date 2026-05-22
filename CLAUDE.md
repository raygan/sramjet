# SRAMjet — AI Development Notes

This file is context for AI assistants working on SRAMjet. For human contributors, see [CONTRIBUTING.md](CONTRIBUTING.md).

**Stack**: FastAPI + SQLite (SQLAlchemy async) + Jinja2 + Tailwind CSS (CDN) + Docker

---

## RetroArch WebDAV Behavior (researched from source)

Source files read: `network/cloud_sync/webdav.c`, `tasks/task_cloudsync.c`, `network/cloud_sync_driver.c`

### HTTP Verbs Used

| Verb | Purpose |
|---|---|
| OPTIONS | Sync begin health check (first request in every sync) |
| GET | Download files and manifest |
| PUT | Upload files and manifest |
| DELETE | Delete files (if destructive mode enabled) |
| MKCOL | Create directories recursively before upload |
| MOVE | Move deleted files to `deleted/` (if non-destructive mode) |

**PROPFIND is NOT used.** RetroArch does not do directory listing via WebDAV. It builds the file list from local disk and the manifest. No XML parsing needed.

### Manifest

- **Filename**: `manifest.server` (hardcoded)
- **URL path**: `/sync/{device}/manifest.server`
- **Format**: JSON array of `{path, hash}` objects, sorted by path
- **Empty string hash (`""`)** means deleted file — distinct from file not present in manifest
- The manifest is the LAST thing uploaded in a sync; it confirms all file uploads succeeded

### Status Codes

| Code | Meaning |
|---|---|
| 200–299 | Success for all verbs |
| 404 | Success for GET (file doesn't exist on server) |
| 405 | Success for MKCOL (directory already exists) |
| 401 | Triggers auth re-negotiation |

### Sync Flow (in order)

1. **OPTIONS** `/sync/{device}/` — begin; opens a new sync event
2. **GET** `manifest.server` — fetch canonical manifest (404 = first sync, fine)
3. *(RetroArch diffs local vs server vs disk)*
4. **MKCOL** for any missing directories (405 = already exists, fine)
5. **GET/PUT/DELETE/MOVE** individual files (up to 4 concurrent)
6. **PUT** `manifest.server` — final step

### Ignored Files

RetroArch never uploads: `config/retroarch.cfg`, `config/content_*.lpl`, `.DS_Store`.

---

## Architecture Decisions

### Conflict resolution

SRAMjet uses **last-write-wins**: whichever device uploads a file last becomes canonical, and the previous version is kept in history. Conflicts are auto-resolved with a `log.warning()` rather than blocking the upload.

RetroArch performs its own client-side conflict detection before uploading. If it sees that both local and server have changed since last sync, it refuses to upload. The **Re-upload All Files** feature serves an empty manifest to the affected device — RetroArch interprets this as a clean-slate server (the same as a brand-new device's first sync) and re-uploads everything unconditionally.

### Sync event boundaries

- **Start**: The OPTIONS request opens a new sync event for that device.
- **End**: The manifest PUT closes the sync event and records final stats.
- Events older than `SYNC_EVENT_WINDOW_SECONDS` (default 30s) with no activity are auto-closed by the next OPTIONS.
- Events from the same device within 10 seconds of each other are merged in the dashboard — RetroArch's concurrent connections often split one logical sync across multiple DB events.

### Per-device last-fetched tracking

Each device has a `last_fetched_manifest.json` in `devices/{name}/` tracking the hash the device last saw for each file. This is used in `handle_file_upload` to distinguish:
- Clean advance: device fetched current canonical and is uploading new progress → accept
- Stale re-upload: device is uploading what it already had → ignore silently
- Conflict: both device and another device made independent changes → last-write-wins

### Per-device quarantine

Saves or states can be quarantined per device (e.g. for incompatible emulator cores). Quarantined files go to a device-specific `quarantine_canonical.json` and are invisible to other devices. The main canonical is untouched. See `app/sync/quarantine.py`.

---

## Dashboard Conventions

### Jinja2 filters (defined in `app/dashboard/templates.py`)

| Filter | Purpose |
|---|---|
| `basename` | Last path segment |
| `dirname` | Everything before the last `/` |
| `url_encode` | URL-safe encoding for game names in hrefs |
| `state_slot` | Returns "Auto", "Slot 0", "Slot 1"… for state file paths; None for non-states |
| `is_save_file` | True for .srm/.sav/.mcr/.fla/.rtc |
| `is_rom_file` | True for ROM extensions |

### Dark mode

Uses Tailwind `class` strategy. A `<script>` in `<head>` adds `dark` to `<html>` unless the device explicitly prefers light. Defaults to dark when preference is unknown. Config after CDN: `tailwind.config = { darkMode: 'class' }`.

### Icons (all in `static/icons/`)

| File | Used for |
|---|---|
| `jet.png` | App icon, favicon, navbar |
| `diskette.png` | Save files in lists |
| `game-cartridge.png` | ROM files in lists |
| `videogame.png` | State files without a screenshot |
| `pin.png` | Pin indicator (filled color) — shown on pinned versions |
| `streak-0.png` | Broken / zero-day streak |
| `streak-1.png` | 1-day streak |
| `streak-2.png` | 2–4 day streak |
| `streak-3.png` | 5–10 day streak |
| `streak-4.png` | 11–21 day streak |
| `streak-sword.png` | 22+ day streak |

### Pill components

Defined as Jinja2 macros **locally in each template** (not shared across templates):
- `slot_badge(path)` — amber for "Auto", blue for numbered slots
- `action_pill(action)` — green ↑ upload, purple ↓ download, red ✕ delete, amber ⚠ conflict

### Pitfall: Jinja2 dict key `items`

Never use `"items"` as a key in a dict passed to a template. Jinja2 resolves `section.items` as Python's built-in `dict.items()` method, not the key. Use `"versions"`, `"entries"`, etc. instead.

---

## File Structure

```
app/
  config.py          — env vars, paths, constants (DATA_DIR, STORE_DIR, etc.)
  database.py        — async SQLAlchemy engine + session; runs Alembic on startup
  models.py          — ORM models
  main.py            — FastAPI app, startup, router registration, static mount
  auth.py            — HTTP Basic auth dependencies (UI and WebDAV, independent)
  store.py           — content-addressable blob store (STORE_DIR/{hash[:2]}/{hash}.bin)
  manifest.py        — manifest parse/serialize/diff utilities
  sync/
    engine.py        — core sync logic: last-write-wins, versioning, canonical updates
    events.py        — sync event lifecycle (open/close/record)
    quarantine.py    — per-device directory quarantine
  webdav/
    router.py        — FastAPI router: OPTIONS, GET, PUT, DELETE, MKCOL, MOVE
  api/
    router.py        — FastAPI router: REST API (blob serving, ZIP download, health)
  dashboard/
    router.py        — thin aggregator — includes all sub-routers
    templates.py     — Jinja2Templates instance and filter registration
    utils.py         — shared helpers (game names, formatting, streaks, device color)
    home.py          — GET /
    timeline.py      — GET /timeline
    devices.py       — GET /devices, device action POSTs
    games.py         — GET /games, GET /games/{name}
    files.py         — GET /files, file detail, revert, remove
    help.py          — GET /help
alembic/             — Alembic migration environment
  versions/          — migration scripts (one per schema change)
static/
  icons/             — app icons
  site.webmanifest   — PWA manifest
templates/           — Jinja2 HTML templates (all extend base.html)
tests/               — pytest suite (covers WebDAV/sync backend)
docker/
  Dockerfile
  docker-compose.yml
```

> **Dockerfile note**: If a new top-level directory is added and referenced at startup, add a `COPY <dir>/ <dir>/` line to `docker/Dockerfile` in the same commit or the container will crash.

---

## Running Locally

```bash
pip install -e ".[dev]"
DATA_DIR=./data uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

`--host 0.0.0.0` is required to accept connections from other devices. Without it RetroArch on other devices fails silently with "completed with failures".

## Running Tests

```bash
pytest
```

Run before committing. The test suite covers the sync backend; dashboard route bugs require code review.

## Database Migrations (Alembic)

Schema changes must be accompanied by an Alembic migration. The workflow:

**1. Edit `app/models.py`** — add columns, tables, indexes, etc.

**2. Generate a migration:**
```bash
DATA_DIR=./data alembic revision --autogenerate -m "describe the change"
```
This compares `models.py` against the live database and generates a migration script in `alembic/versions/`.

**3. Review the generated file.** Autogenerate is good but not perfect — check that the upgrade/downgrade functions look correct before committing.

**4. Commit the migration alongside the model change.** They should always be in the same commit so the codebase is never in an inconsistent state.

Migrations run automatically on startup via `init_db()`. Fresh installs run all migrations; existing installs only run new ones.

**Existing installs without a recorded revision** are detected automatically at startup: if the `devices` table exists but `alembic_version` has no rows, the database is stamped to head before upgrading (so no migrations run against already-existing tables).

## Docker

```bash
docker compose -f docker/docker-compose.yml up -d --build
```
