# Contributing to SRAMjet

## What it is

SRAMjet is a self-hosted WebDAV server designed specifically as a RetroArch Cloud Sync backend. Rather than a generic WebDAV server, it understands the RetroArch manifest format and acts as a smart sync backend — tracking what changed across devices, maintaining version history, and surfacing everything in a web dashboard.

**Stack:** FastAPI + SQLite (SQLAlchemy async) + Jinja2 + Tailwind CSS (Play CDN) + Docker

---

## Getting started

```bash
git clone https://github.com/raygan/sramjet.git
cd sramjet
pip install -e ".[dev]"
DATA_DIR=./data uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

The dashboard is at `http://localhost:8080`. Use `--host 0.0.0.0` if you want real RetroArch devices to reach the server — without it uvicorn binds to localhost only and devices fail silently.

### Running tests

```bash
pytest
```

Run before committing. The test suite covers the WebDAV sync backend. Dashboard route bugs require code review — there are no automated tests for the Jinja2 pages yet.

---

## Project structure

```
app/
  config.py          — env vars, paths, constants
  database.py        — async SQLAlchemy engine, session, Alembic startup
  models.py          — ORM models
  main.py            — FastAPI app, lifespan, router registration
  auth.py            — optional HTTP Basic auth dependency
  store.py           — content-addressable blob store
  manifest.py        — manifest parse/serialize/diff utilities
  sync/
    engine.py        — core sync logic: versioning, conflict auto-resolution
    events.py        — sync event lifecycle (open/close)
    quarantine.py    — per-device file type quarantine
  webdav/
    router.py        — WebDAV endpoints (OPTIONS, GET, PUT, DELETE, MKCOL, MOVE)
  api/
    router.py        — REST API (blob serving, ZIP download, health check)
  dashboard/
    router.py        — aggregates sub-routers
    templates.py     — Jinja2Templates instance and filter registration
    utils.py         — shared helpers (game names, formatting, streaks)
    home.py          — GET /
    timeline.py      — GET /timeline
    devices.py       — GET /devices and device action routes
    games.py         — GET /games, GET /games/{name}
    files.py         — GET /files, file detail, revert
    help.py          — GET /help
alembic/             — database migration scripts
  versions/          — one file per migration
static/
  icons/             — app icons
templates/           — Jinja2 HTML templates (all extend base.html)
tests/               — pytest suite
docker/
  Dockerfile
  docker-compose.yml
  docker-compose.override.yml  — gitignored, your local settings
```

> **Dockerfile note:** If you add a new top-level directory that's referenced at startup, add a `COPY <dir>/ <dir>/` line to `docker/Dockerfile` in the same commit or the container will crash on deploy.

---

## How the sync works

RetroArch's Cloud Sync performs these HTTP requests in order on every sync:

1. **OPTIONS** `/sync/{device}/` — health check; SRAMjet uses this to open a sync event
2. **GET** `manifest.server` — fetch the canonical file manifest (404 = first sync, fine)
3. RetroArch diffs local files against the manifest
4. **MKCOL** for any missing directories (405 = already exists, fine)
5. **GET / PUT / DELETE / MOVE** individual files (up to 4 concurrent connections)
6. **PUT** `manifest.server` — final step; confirms all uploads succeeded

**PROPFIND is never used.** RetroArch builds its file list from disk and the manifest, not from directory listing.

**The manifest format** is a JSON array of `{"path": str, "hash": str}` objects sorted by path. An empty string hash (`""`) means the file was deleted.

### Conflict resolution

SRAMjet uses **last-write-wins**: whichever device uploads a file last becomes the canonical version, and the previous version is kept in history for revert. The server never gets into a stuck state.

RetroArch also performs its own **client-side** conflict detection before uploading. If it detects that both local and server have changed since the last sync, it may refuse to upload and report "cloud sync completed with conflicts." The **Trust Next Sync** feature on the Devices page works around this by serving an empty manifest, causing RetroArch to treat the server as a clean slate and re-upload everything.

---

## Database schema changes

Schema changes must be accompanied by an Alembic migration:

**1. Edit `app/models.py`**

**2. Generate a migration:**
```bash
DATA_DIR=./data alembic revision --autogenerate -m "describe the change"
```

**3. Review the generated file** in `alembic/versions/`. Autogenerate is good but not perfect — always read the upgrade/downgrade functions before committing.

**4. Commit the migration alongside the model change** — they should always be in the same commit.

Migrations run automatically on startup. Fresh installs run all migrations; existing installs only run the new ones.

---

## Dashboard conventions

### Jinja2 filters (defined in `app/dashboard/templates.py`)

| Filter | Purpose |
|---|---|
| `basename` | Last path segment |
| `dirname` | Everything before the last `/` |
| `url_encode` | URL-safe encoding for game names in hrefs |
| `state_slot` | Returns `"Auto"`, `"Slot 0"`, `"Slot 1"`… for state files; `None` otherwise |
| `is_save_file` | True for `.srm/.sav/.mcr/.fla/.rtc` |
| `is_rom_file` | True for common ROM extensions |

### Tailwind CSS

Uses the Play CDN with the `class` dark mode strategy. A script in `<head>` adds `dark` to `<html>` before the page renders (avoiding flash). Dark mode defaults to on when device preference is unknown.

### Pill components

`slot_badge` and `action_pill` are defined as Jinja2 macros **locally in each template** that needs them — they are not shared via a macro file.

### Pitfall: Jinja2 dict key `items`

Never use `"items"` as a key in a dict passed to a Jinja2 template. `section.items` resolves to Python's built-in `dict.items()` method, not the key value. Use `"versions"`, `"entries"`, etc. instead.

---

## Docker

```bash
# Build and start
docker compose -f docker/docker-compose.yml up -d --build

# View logs
docker compose -f docker/docker-compose.yml logs -f
```

Copy `docker/docker-compose.override.yml.example` (if present) or create `docker/docker-compose.override.yml` to set your local port, volume path, and timezone — this file is gitignored and merged automatically by Docker Compose.
