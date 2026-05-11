# SRAMjet — Development Guide

## Project Summary

A self-hosted WebDAV server acting as a smart RetroArch cloud sync backend. Supports per-device routing, conflict detection, file versioning, content-addressable storage, and a minimal web dashboard.

**Spec**: `sramjet-spec.md`  
**Stack**: FastAPI + SQLite (SQLAlchemy async) + Jinja2 + Tailwind CSS + Docker

## Development Phases

- **Phase 1**: Working sync backend + minimal dashboard. Complete when test plan in spec passes.
- **Phase 2**: UI polish (only after Phase 1 is validated with real devices).

Do NOT polish the UI or add features beyond what's needed for the current phase.

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
| 401 | Triggers digest auth re-negotiation (retry with credentials) |
| 409 | **Our choice for conflict rejection** — manifest PUT rejected |

### Sync Flow (in order)

1. **OPTIONS** `/sync/{device}/` — begin; we use this to open a new sync event
2. **GET** `manifest.server` — fetch canonical manifest (404 = first sync, fine)
3. *(RetroArch diffs local vs server vs disk)*
4. **MKCOL** for any missing directories (405 = already exists, fine)
5. **GET/PUT/DELETE/MOVE** individual files (up to 4 concurrent)
6. **PUT** `manifest.server` — final step; **return 409 if any conflicts exist**

### Auth

RetroArch supports HTTP Basic and Digest auth. For Phase 1, no auth is needed (trusted local network per spec). Keep the door open for adding it later.

### Ignored Files

RetroArch never uploads: `config/retroarch.cfg`, `config/content_*.lpl`, `.DS_Store`. We don't need to handle these specially, but don't be surprised if they're absent.

---

## Architecture Decisions (design gaps resolved)

### Gap 1 — Multi-device conflicts

If a file already has an unresolved conflict, any further uploads of that file are rejected with 409. The conflicts table tracks exactly two competing versions (device_a vs device_b). The first two conflicting uploads define the conflict; subsequent attempts are blocked until resolved. This keeps the schema simple without losing correctness.

### Gap 2 — Sync event boundaries

- **Start**: The OPTIONS request opens a new sync event for that device.
- **End**: The manifest PUT (`manifest.server`) closes the sync event and records final stats.
- Events older than `SYNC_EVENT_WINDOW_SECONDS` (default 30s) with no activity are also auto-closed by the next OPTIONS.

### Gap 3 — First upload of a path (no prior canonical)

First-upload-wins. The first PUT for any path immediately becomes canonical. If a second device then PUTs a different version before the first device's manifest is accepted, that triggers a conflict. There is no "canonical doesn't exist yet" ambiguity — the first write establishes canonical.

### Gap 4 — Manifest rejection idempotency

Before creating a conflict record, the engine checks for an existing unresolved conflict for the same `file_path`. If one exists, it updates rather than inserts, preventing duplicate rows from RetroArch retries.

---

## File Structure

```
app/
  config.py          — env vars, paths, constants
  database.py        — async SQLAlchemy engine + session
  models.py          — ORM models
  main.py            — FastAPI app, startup, router registration
  store.py           — content-addressable file store (hash-based)
  manifest.py        — manifest parse/serialize/diff utilities
  sync/
    engine.py        — core sync logic: conflict detection, versioning, canonical updates
    events.py        — sync event lifecycle (open/close/record)
  webdav/
    router.py        — FastAPI router: OPTIONS, GET, PUT, DELETE, MKCOL, MOVE
  api/
    router.py        — FastAPI router: REST API for dashboard
  dashboard/
    router.py        — FastAPI router: Jinja2 pages
templates/           — Jinja2 HTML templates
tests/               — pytest test suite
docker/              — Dockerfile, docker-compose.yml
```

## Running Locally (development)

```bash
pip install -e ".[dev]"
DATA_DIR=./data uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

`--host 0.0.0.0` is required to accept connections from other devices (phone, iPad, etc.).
Without it, uvicorn defaults to `127.0.0.1` (localhost only) and RetroArch on other devices
will fail silently with "completed with failures".

## Running Tests

```bash
pytest
```

## Docker

```bash
docker compose -f docker/docker-compose.yml up
```
