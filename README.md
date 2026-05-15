# SRAMjet

<img src="static/icons/jet.png" alt="SRAMjet" width="80">

A self-hosted WebDAV server built specifically for RetroArch's Cloud Sync feature. Instead of pointing RetroArch at a generic WebDAV server, SRAMjet understands the RetroArch manifest format and acts as a smart sync backend — tracking what changed, detecting conflicts between devices, maintaining version history, and surfacing everything in a web dashboard.

---

## Features

**Sync backend**
- Drop-in WebDAV target for RetroArch Cloud Sync — no client modification needed
- Per-device routing (`/sync/{device-name}/`) with automatic device registration on first sync
- Conflict detection: when two devices upload diverging versions of the same file, the manifest is rejected (HTTP 409) and the conflict is flagged in the dashboard
- Content-addressable storage — identical files are stored exactly once regardless of how many devices upload them
- Full version history with configurable retention limits per file category (saves, states, system, thumbnails)
- File-level revert to any previous version from the dashboard

**Dashboard**
- **Home**: overview stats (total uploads, games played, storage used), gaming streak tracker, most recent sync, recently played games
- **Timeline**: chronological feed of every sync event with screenshot previews, slot badges, upload/download pills, and relative timestamps grouped by time period
- **Games**: browse all games with boxart thumbnails; drill into each game to see saves, states (with slot badges and screenshot previews), and ROMs
- **Devices**: manage registered devices; trust-next-sync to bypass conflict detection once; per-directory quarantine
- **Files**: browse the canonical file store; click any file for time-sectioned version history with one-click revert

**Visual design**
- Dark mode by default (respects system light preference)
- Responsive layout — works well on phones
- Kawaii icons: diskette for saves, game cartridge for ROMs, escalating flame icons for gaming streaks

---

## Quick Start (Docker)

```bash
git clone https://github.com/raygan/sramjet.git
cd sramjet/docker
docker compose up -d
```

The dashboard is available at `http://your-server:8080`.

Data is persisted in `/data` inside the container. Mount a volume to keep it across restarts:

```yaml
# docker/docker-compose.yml — edit the volume path to suit your setup
volumes:
  - /path/to/your/data:/data
```

**Environment variables**

| Variable | Default | Description |
|---|---|---|
| `DATA_DIR` | `/data` | Where the database, blobs, and manifests are stored |
| `DISPLAY_TZ` | `America/Chicago` | Timezone for dashboard timestamps |
| `SYSTEM_VERSION_LIMIT` | `5` | Versions to keep for `system/` files |
| `THUMBNAIL_VERSION_LIMIT` | `3` | Versions to keep for `thumbnails/` files |
| `SAVES_VERSION_LIMIT` | `0` | Versions to keep for `saves/` files — `0` means unlimited |
| `STATES_VERSION_LIMIT` | `0` | Versions to keep for `states/` files — `0` means unlimited |
| `SYNC_EVENT_WINDOW_SECONDS` | `30` | Events within this window from the same device are merged |

---

## RetroArch Setup

In RetroArch, go to **Settings → Saving → Cloud Sync** and set:

| Setting | Value |
|---|---|
| Cloud Sync Backend | WebDAV |
| Cloud Sync URL | `http://your-server:8080/sync/my-device-name/` |

Use a different device name for each device (e.g. `iphone`, `ipad`, `mac`). SRAMjet registers new devices automatically on first sync — no server-side setup needed.

---

## Development

```bash
pip install -e ".[dev]"
DATA_DIR=./data uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

> `--host 0.0.0.0` is required if you want real devices to reach the dev server. Without it uvicorn binds to localhost only and RetroArch on other devices will fail silently.

```bash
pytest
```

---

## Attribution

Icons by [Freepik](https://www.flaticon.com/authors/kawaii/lineal-color) on Flaticon.
