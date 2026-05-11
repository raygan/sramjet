# RetroArch Sync Server — Project Specification

## Overview

A self-hosted WebDAV server that acts as a smart sync backend for RetroArch's Cloud Sync feature. Unlike a generic WebDAV server, this app understands the RetroArch manifest format and provides per-device routing, intelligent conflict detection, file versioning, a conflict resolution UI, and a timeline of changes — all accessible from a mobile-friendly web dashboard.

---

## Goals

- Drop-in replacement for a generic WebDAV server as a RetroArch cloud sync backend
- Per-device endpoints so the server can distinguish which device is syncing
- Automatic device registration on first sync — no manual setup
- Conflict detection with dashboard UI for resolution
- File versioning for saves and states; limited versioning for system and thumbnail files
- Content-addressable storage — identical files are never stored twice
- Mobile-friendly web dashboard
- Deployable as a Docker container on Unraid

---

## Non-Goals

- Not a general-purpose WebDAV server
- Not a RetroArch replacement or modification
- No changes required to RetroArch client configuration beyond the URL

---

## Pre-Implementation Research

**Before writing any WebDAV or sync code**, clone the RetroArch GitHub repository and read the cloud sync source code. Locate the relevant file(s) — likely named something like `cloud_sync.c`, `webdav.c`, or similar. Determine:

- Exactly which WebDAV verbs RetroArch uses (e.g. GET, PUT, DELETE, PROPFIND, MKCOL)
- How it constructs requests and parses responses
- Which HTTP status codes it expects for success, failure, and conflict
- Any unusual headers or request structures
- How it handles the manifest file specifically (filename, path, upload/download behavior)

This research should directly inform the WebDAV implementation. Do not guess or rely on general WebDAV spec knowledge alone.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI |
| Frontend | Jinja2 templates, Tailwind CSS |
| Database | SQLite (via SQLAlchemy) |
| Storage | Local filesystem |
| Deployment | Docker, Docker Compose |

---

## Device Routing

Each RetroArch device is configured to sync to a unique URL path:

```
https://yourserver.com/sync/{device_name}/
```

Examples:
- `/sync/iphone/`
- `/sync/ipad/`
- `/sync/appletv/`
- `/sync/mac/`

The `{device_name}` segment is used as the device identifier. No configuration is needed on the server — if a sync request arrives for an unknown device name, it is automatically registered as a new device.

### First Sync (New Device)

1. Server detects an unknown device name
2. Registers the device in the database with a timestamp
3. Accepts all uploaded files as canonical state
4. No conflicts are possible at this stage

### Subsequent Syncs

The server acts as the intermediary for RetroArch's three-way merge. See Sync Flow below.

---

## Sync Flow

RetroArch's sync process involves:
1. Fetching the server manifest
2. Comparing it against local manifest and local disk state
3. Uploading changed files
4. Uploading an updated manifest

The server intercepts this process at each step:

### Manifest Fetch (GET manifest)

The server returns the **canonical manifest** — the agreed-upon state across all devices — not a device-specific one. This tells RetroArch what the "server" currently has.

### File Upload (PUT)

When a device uploads a file:

1. Compute the hash of the incoming file
2. Check if a file with this hash already exists in the content store — if so, link to it rather than storing a duplicate
3. Compare against canonical state:
   - If the file is new or changed cleanly (no other device has a conflicting version): update canonical, store a new version, record the change in the timeline
   - If a conflict is detected: park the incoming file in the conflict store, reject the manifest update with an appropriate HTTP error, record the conflict

### Manifest Upload (PUT manifest)

- If no conflicts: accept the manifest, update canonical manifest, update the device's last-known manifest snapshot
- If conflicts exist: reject with an HTTP error so RetroArch reports "finished with failures" — this signals the user to check the dashboard

### File Download (GET)

Serve files from the canonical store.

---

## Conflict Detection

A conflict occurs when:

- Device A uploads a version of `file.state` that differs from canonical, AND
- Device B has previously uploaded a different version of the same file that also differs from canonical

Or when:
- One device has deleted a file while another has modified it

### During a Conflict

- Each device is served the frozen canonical version of the conflicted file
- Uploads of the conflicted file are rejected with an HTTP error
- RetroArch will report sync failures, prompting the user to check the dashboard
- The conflict is listed in the dashboard with both versions available

### Resolving a Conflict

From the dashboard, the user selects which version to keep. The server then:

1. Promotes the chosen version to canonical
2. Updates the canonical manifest
3. Clears the conflict record
4. On next sync, all devices receive the resolved version

---

## Storage

### Content-Addressable File Store

Files are stored by their MD5 hash (matching RetroArch's manifest format):

```
/data/store/{aa}/{aabbcc...}.bin
```

No file is stored more than once. If two devices upload identical content, only one copy exists on disk. File paths and metadata are tracked in the database.

### Directory Structure

```
/data
  /store/               # content-addressable file store (by hash)
  /manifests/
    canonical.json      # current agreed-upon manifest
    /snapshots/         # canonical manifest snapshot per sync, for revert
      {timestamp}.json
  /devices/
    /{device_name}/
      manifest.json     # last manifest received from this device
  /conflicts/
    /{conflict_id}/
      meta.json         # file path, device names, hashes, timestamps
```

### Versioning Policy

| Directory | Versioning |
|---|---|
| saves/ | Full history |
| states/ | Full history |
| config/ | Full history |
| system/ | Last 5 versions (configurable) |
| thumbnails/ | Last 3 versions (configurable) |

Retention policy for system and thumbnails is configurable via environment variable. Old versions are pruned on each sync after the limit is exceeded.

---

## Manifest Format

RetroArch manifests are JSON arrays of path/hash pairs:

```json
[
  {
    "path": "states/mGBA/Mother 3 (Japan).state",
    "hash": "7ac55fcd180680d9a80e1d28b3d6c6ef"
  }
]
```

The server reads, writes, and serves this format exactly. The canonical manifest follows the same structure.

---

## Revert

### File-Level Revert

Any previous version of a save or state file can be restored to canonical from the dashboard. This takes effect on the next sync for all devices.

### Full Library Revert

The server snapshots the canonical manifest on every sync. From the dashboard, a previous snapshot can be selected and promoted to canonical. All devices will receive the reverted state on next sync.

A prominent warning is shown: **"This will revert all devices on next sync. Files newer than the selected snapshot will be lost unless they exist in version history."**

---

## Web Dashboard

Mobile-first, responsive design using Tailwind CSS. Accessible from any browser.

### Pages

#### Home / Overview
- List of registered devices with last sync time and sync status
- Conflict badge / banner if any unresolved conflicts exist
- Quick link to conflict queue

#### Timeline
- Chronological feed of sync events, newest first
- Each event shows: device name, timestamp, files changed
- Save and state file changes are always shown individually
- Config, system, and thumbnail changes are batched and collapsed if there are more than 5 in a single sync, with an expand option
- State file changes show an accompanying PNG preview thumbnail if a matching `.state.png` file was received in the same sync batch
- Clicking a file in the timeline shows its version history

#### Conflict Queue
- List of all unresolved conflicts
- Each conflict shows:
  - File path
  - Which devices have conflicting versions
  - When each version was received by the server
  - PNG preview if the file is a state and a matching PNG exists
  - "Keep [device A]" / "Keep [device B]" buttons
- Resolving a conflict updates canonical immediately

#### File Browser
- Browse the canonical file store by directory
- Click any file to see version history
- Revert a file to any previous version

#### Revert
- List of canonical manifest snapshots (one per sync)
- Shows timestamp, device that triggered the sync, and number of files changed
- Select a snapshot to preview what would change
- Confirm to promote to canonical

#### Devices
- List of all registered devices
- Last sync time, number of files, sync history
- Option to rename a device (display name only, URL path unchanged)
- Option to remove a device (does not affect canonical state)

---

## API

Internal REST API used by the dashboard frontend.

```
GET    /api/devices                        # list devices
GET    /api/devices/{name}                 # device detail
GET    /api/timeline                       # paginated sync event feed
GET    /api/conflicts                      # list unresolved conflicts
POST   /api/conflicts/{id}/resolve         # resolve a conflict
GET    /api/files                          # browse canonical file store
GET    /api/files/{path}/history           # version history for a file
POST   /api/files/{path}/revert/{version}  # revert file to version
GET    /api/snapshots                      # list canonical manifest snapshots
POST   /api/snapshots/{id}/revert          # revert entire library to snapshot
```

---

## WebDAV Endpoints

```
GET    /sync/{device}/...         # download a file
PUT    /sync/{device}/...         # upload a file
DELETE /sync/{device}/...         # delete a file
PROPFIND /sync/{device}/...       # directory listing
MKCOL  /sync/{device}/...         # create directory (if used by RetroArch)
```

Exact verb support to be confirmed by reading RetroArch source before implementation.

---

## Database Schema (SQLite)

### devices
| column | type |
|---|---|
| id | integer PK |
| name | text unique |
| display_name | text |
| first_seen | datetime |
| last_sync | datetime |

### files
| column | type |
|---|---|
| id | integer PK |
| path | text |
| hash | text |
| size | integer |
| stored_at | datetime |

### versions
| column | type |
|---|---|
| id | integer PK |
| file_path | text |
| hash | text |
| device_id | integer FK |
| received_at | datetime |
| is_canonical | boolean |

### conflicts
| column | type |
|---|---|
| id | integer PK |
| file_path | text |
| device_a_id | integer FK |
| device_b_id | integer FK |
| hash_a | text |
| hash_b | text |
| detected_at | datetime |
| resolved_at | datetime nullable |
| resolved_by_hash | text nullable |

### sync_events
| column | type |
|---|---|
| id | integer PK |
| device_id | integer FK |
| started_at | datetime |
| finished_at | datetime |
| files_uploaded | integer |
| files_downloaded | integer |
| had_conflicts | boolean |

### sync_event_files
| column | type |
|---|---|
| id | integer PK |
| sync_event_id | integer FK |
| file_path | text |
| action | text (uploaded/downloaded/deleted/conflicted) |
| hash | text |

### manifest_snapshots
| column | type |
|---|---|
| id | integer PK |
| sync_event_id | integer FK |
| created_at | datetime |
| manifest_json | text |

---

## Docker Deployment

```yaml
services:
  retroarch-sync:
    image: retroarch-sync
    ports:
      - "8080:8080"
    volumes:
      - /mnt/user/appdata/retroarch-sync:/data
    environment:
      - SECRET_KEY=changeme
      - SYSTEM_VERSION_LIMIT=5
      - THUMBNAIL_VERSION_LIMIT=3
```

---

## Development Phases

### Phase 1 — Sync Core (build and validate first)

The goal of Phase 1 is a fully working, reliable sync backend. The web UI in this phase is functional but not polished — it just needs to work well enough to support testing and conflict resolution. Do not invest time in UI aesthetics, animations, or visual polish during Phase 1.

Phase 1 is complete when all tests in the Test Plan below pass reliably.

Deliverables:
- WebDAV endpoints fully compatible with RetroArch cloud sync
- Per-device routing and automatic device registration
- Canonical manifest management
- Conflict detection and rejection
- Content-addressable file storage
- File versioning
- Minimal functional dashboard: device list, conflict queue, timeline (unstyled is fine)
- Docker deployment working on Unraid

### Phase 2 — UI Polish (only after Phase 1 is validated)

Once sync behavior is confirmed solid, revisit the dashboard with a focus on making it genuinely nice and usable from a phone.

Phase 2 deliverables:
- Mobile-first responsive layout
- Timeline with batching, expand/collapse, and PNG previews
- Polished conflict resolution UI
- File browser and revert UI
- General visual polish throughout

---

## Test Plan

Run these tests in sequence after initial deployment. Each test should be performed with real RetroArch clients on real devices. Use a GBA game for testing — Mother 3 or Minish Cap are good candidates since save and state files are already present.

All tests assume two primary devices (Mac and iPhone) and a third device (iPad) introduced later. Before starting, ensure all devices are pointed at their respective `/sync/{device}/` endpoints and cloud sync is enabled.

---

### Phase 1: Basic Two-Device Sync

**Test 1.1 — Initial sync from first device**
1. On Mac: load a game, create an in-game save, create a save state
2. Sync from Mac
3. Confirm server received files and canonical manifest was created
4. Confirm dashboard shows Mac as a registered device with correct file count

**Test 1.2 — Second device receives files**
1. On iPhone: trigger sync (no prior state)
2. Confirm iPhone receives all files from canonical state
3. Load the game on iPhone, confirm in-game save loads correctly
4. Load the save state, confirm it loads correctly

**Test 1.3 — Change propagates from second device back to first**
1. On iPhone: play further, overwrite the save, create a new save state, sync
2. On Mac: sync
3. Confirm Mac received the newer save and state
4. Load on Mac and confirm it reflects iPhone's progress

---

### Phase 2: Three-Device Sync

**Test 2.1 — New device receives full canonical state**
1. On iPad: trigger sync cold (never synced before)
2. Confirm iPad receives all files currently in canonical state
3. Confirm dashboard registers iPad as a new device automatically

**Test 2.2 — Change from third device propagates to all**
1. On iPad: make a save, sync
2. On Mac: sync — confirm it receives iPad's save
3. On iPhone: sync — confirm it also receives iPad's save

---

### Phase 3: Conflict Scenarios

**Test 3.1 — Basic conflict between two devices**
1. Disconnect both Mac and iPhone from the network (or disable sync temporarily)
2. On Mac: play and save
3. On iPhone: play and save (different progress — same file, different content)
4. Sync Mac first — confirm it uploads cleanly
5. Sync iPhone — confirm the server rejects the manifest upload
6. Confirm RetroArch on iPhone reports a sync failure
7. Confirm dashboard shows the conflict clearly with both versions listed

**Test 3.2 — Conflict resolution in favor of one device**
1. From the dashboard: resolve the conflict in favor of iPhone
2. Sync iPhone — confirm it completes cleanly
3. Sync Mac — confirm it downloads the iPhone version
4. Load the game on Mac and confirm it has iPhone's progress

**Test 3.3 — Conflict resolution in favor of the other device**
1. Repeat Test 3.1 to create a fresh conflict
2. This time resolve in favor of Mac
3. Confirm both devices converge on Mac's version after syncing

---

### Phase 4: Edge Cases

**Test 4.1 — Delete propagation**
1. On Mac: delete a save file, sync
2. Confirm the file is removed from the server
3. On iPhone: sync — confirm the file is removed from iPhone

**Test 4.2 — New device joins mid-session**
1. After Mac and iPhone have been syncing for a while with several files
2. Introduce iPad as a new device (first sync)
3. Confirm iPad receives the complete current canonical state, not just recent changes

**Test 4.3 — Deduplication**
1. Copy an identical save file to two devices under different game names (or copy manually)
2. Sync both devices
3. Confirm the server's content store only contains one copy of the file data

**Test 4.4 — PNG preview pairing**
1. On Mac: create a save state (ensure RetroArch is configured to save screenshots with states)
2. Sync
3. Confirm the timeline entry for the state file shows the PNG preview thumbnail
4. Create a state without a PNG (disable screenshots temporarily) and sync
5. Confirm no broken image appears in the timeline for that entry

**Test 4.5 — Rapid sync with no changes**
1. Sync a device twice in quick succession with no file changes between syncs
2. Confirm the second sync completes cleanly with zero files uploaded/downloaded
3. Confirm no phantom entries appear in the timeline

**Test 4.6 — Mixed batch sync**
1. Add a ROM to the system directory and create a new save state at the same time
2. Sync
3. Confirm the timeline surfaces the save state individually
4. Confirm the ROM change is batched/collapsed in the timeline

---

### Phase 5: Revert

**Test 5.1 — Single file revert**
1. Note the current hash of a save file
2. On Mac: overwrite the save with new progress, sync
3. From the dashboard: revert that save file to the previous version
4. Sync Mac and iPhone
5. Confirm both devices received the reverted version
6. Load the game and confirm it reflects the reverted save

**Test 5.2 — Full library revert**
1. Note the current canonical state (file list and hashes)
2. Make several changes across devices and sync
3. From the dashboard: select the snapshot from step 1 and revert the full library
4. Confirm the dashboard shows a warning before confirming
5. Sync all devices
6. Confirm all devices converge back on the state from step 1

---

## Open Questions / Future Considerations

- **Authentication**: Currently assumes trusted local network. Future: per-device API keys or basic auth
- **Retention policy UI**: Currently environment variable only; could be exposed in dashboard settings
- **Notifications**: Push notification or email when conflicts are detected
- **RetroArch WebDAV behavior**: Must be confirmed by reading source before implementation — this is the highest-risk unknown in the project
