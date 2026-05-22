# SRAMjet

<img src="static/icons/jet.png" alt="SRAMjet" width="80">

A self-hosted WebDAV server built specifically for RetroArch's Cloud Sync feature. Instead of pointing RetroArch at a generic WebDAV server, SRAMjet understands the RetroArch manifest format and acts as a smart sync backend — tracking what changed, detecting conflicts between devices, maintaining version history, and surfacing everything in a web dashboard.

---

## Features

**Sync backend**
- Drop-in WebDAV target for RetroArch Cloud Sync — no client modification needed
- Per-device routing (`/sync/{device-name}/`) with automatic device registration on first sync
- Last-write-wins conflict resolution — uploads are always accepted, with full version history kept for revert
- Content-addressable storage — identical files are stored exactly once regardless of how many devices upload them
- Full version history with configurable retention limits per file category (saves, states, system, thumbnails)
- File-level revert to any previous version from the dashboard
- Per-device quarantine — isolate saves or states to a single device (useful for incompatible emulator cores)

**Dashboard**
- **Home**: overview stats (total uploads, games played, storage used), gaming streak tracker, most recent sync, recently played games
- **Timeline**: chronological feed of every sync event with screenshot previews, slot badges, upload/download pills, and relative timestamps grouped by time period
- **Games**: browse all games with boxart thumbnails; drill into each game to see saves, states (with slot badges and screenshot previews), pinned versions, and ROMs
- **Devices**: manage registered devices; re-upload all files to bypass conflict detection once; per-directory quarantine
- **Files**: browse the canonical file store; click any file for time-sectioned version history with one-click revert and download
- **Help**: built-in setup guide and sync troubleshooting reference

**Pinned saves**
- Pin any version of a save or state with an optional note (e.g. "Before final boss")
- Pinned versions are kept indefinitely regardless of retention limits
- Shown in a dedicated Pinned section on the game detail page with screenshots, download, and details links

**Security**
- Optional HTTP Basic auth for the web UI and WebDAV sync endpoints, configured independently via environment variables
- Auth is disabled by default — safe for trusted private networks or VPN-only deployments

**Visual design**
- Dark mode by default (respects system light preference)
- Responsive layout with hamburger nav — works well on phones
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
| `DISPLAY_TZ` | `UTC` | Timezone for dashboard timestamps |
| `SYSTEM_VERSION_LIMIT` | `5` | Versions to keep for `system/` files |
| `THUMBNAIL_VERSION_LIMIT` | `3` | Versions to keep for `thumbnails/` files |
| `SAVES_VERSION_LIMIT` | `0` | Versions to keep for `saves/` files — `0` means unlimited |
| `STATES_VERSION_LIMIT` | `0` | Versions to keep for `states/` files — `0` means unlimited |
| `SYNC_EVENT_WINDOW_SECONDS` | `30` | Events within this window from the same device are merged |
| `MAX_UPLOAD_BYTES` | `268435456` | Maximum file upload size in bytes (256 MB); `0` disables the limit |
| `AUTH_UI_USERNAME` | *(unset)* | Username for web UI and API — both vars must be set to enable |
| `AUTH_UI_PASSWORD` | *(unset)* | Password for web UI and API |
| `AUTH_WEBDAV_USERNAME` | *(unset)* | Username for WebDAV sync — both vars must be set to enable |
| `AUTH_WEBDAV_PASSWORD` | *(unset)* | Password for WebDAV sync |

---

## RetroArch Setup

In RetroArch, go to **Settings → Saving → Cloud Sync** and set:

| Setting | Value |
|---|---|
| Cloud Sync Backend | WebDAV |
| Cloud Sync URL | `http://your-server:8080/sync/my-device-name/` |

Use a different device name for each device (e.g. `iphone`, `ipad`, `mac`). SRAMjet registers new devices automatically on first sync — no server-side setup needed.

For a full setup walkthrough and conflict resolution tips, see the **Help** page in the dashboard.

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

See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture notes, the schema migration workflow, and dashboard conventions.

---

## Attribution

Icons by [Freepik](https://www.flaticon.com/authors/kawaii/lineal-color) on Flaticon.
