"""Minimal Phase 1 dashboard — functional, not polished."""

import re
from urllib.parse import quote

import app.config
from app import manifest as mf
from app.database import get_db
from app.models import Device, SyncEvent, SyncEventFile, Version
from app.sync.engine import clear_force_accept, is_force_accept, set_force_accept
from app.sync.quarantine import get_quarantine, set_quarantine
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Jinja2 helpers for path display
templates.env.filters["basename"] = lambda p: p.split("/")[-1]
templates.env.filters["url_encode"] = lambda s: quote(str(s), safe="")
templates.env.filters["dirname"] = lambda p: "/".join(p.split("/")[:-1])
templates.env.filters["is_save_file"] = lambda p: bool(_SAVE_EXT.search(p))
templates.env.filters["is_rom_file"] = lambda p: bool(_ROM_EXT.search(p))

def _state_slot(path: str) -> str | None:
    """Return a human-readable slot label for a state file path, or None."""
    name = path.split("/")[-1]
    if re.search(r"\.state\.auto$", name, re.IGNORECASE):
        return "Auto"
    m = re.search(r"\.state(\d*)$", name, re.IGNORECASE)
    if m:
        return f"Slot {m.group(1) or '0'}"
    return None

templates.env.filters["state_slot"] = _state_slot


def _state_slot_sort_key(entry: dict) -> tuple:
    slot = _state_slot(entry["path"])
    if slot == "Auto":
        return (0, 0)
    if slot is not None:
        try:
            return (1, int(slot.split()[-1]))
        except ValueError:
            return (1, 0)
    return (2, 0)

_DEVICE_COLORS = ["blue", "violet", "emerald", "orange", "pink", "teal", "red", "indigo"]

def _device_color(name: str) -> str:
    idx = sum(ord(c) for c in (name or "")) % len(_DEVICE_COLORS)
    return _DEVICE_COLORS[idx]


# ─── Game name helpers ────────────────────────────────────────────────────────

_SAVE_EXT = re.compile(r'\.(srm|sav|mcr|fla|rtc)$', re.IGNORECASE)
_STATE_EXT = re.compile(r'\.state.*$', re.IGNORECASE)
_ROM_EXT = re.compile(r'\.(zip|sfc|smc|gba|gb|gbc|nds|nes|md|gen|bin|z64|v64|n64|iso|pce|gg|smd|rom)$', re.IGNORECASE)
_THUMB_EXT = re.compile(r'\.png$', re.IGNORECASE)
_GAME_DIRS = {'saves', 'states', 'system', 'thumbnails'}


def _extract_game_name(path: str) -> str | None:
    """Return the game name from a canonical path, or None if not a game file."""
    parts = path.split('/')
    if len(parts) < 2:
        return None
    top = parts[0]
    filename = parts[-1]
    if top == 'saves':
        name = _SAVE_EXT.sub('', filename)
    elif top == 'states':
        name = _STATE_EXT.sub('', filename)
    elif top == 'system':
        name = _ROM_EXT.sub('', filename)
    elif top == 'thumbnails':
        name = _THUMB_EXT.sub('', filename)
    else:
        return None
    return name if name != filename else None


def _format_game_name(name: str) -> tuple[str, str]:
    """Split 'Foo (Bar) [Baz]' into ('Foo', '(Bar) [Baz]') for display."""
    m = re.search(r'[\(\[]', name)
    if m:
        return name[:m.start()].rstrip(), name[m.start():]
    return name, ''


def _names_match(a: str, b: str) -> bool:
    """Return True if two game names refer to the same game.

    'Mother 3 (Japan)' matches 'Mother 3 (Japan) [T-En...]' because
    the shorter name is a word-boundary prefix of the longer one.
    Exact equality always matches.
    """
    a, b = a.strip(), b.strip()
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if longer.startswith(shorter):
        # Only accept if what follows in the longer name starts with a space
        # (i.e. we're at a word boundary, not mid-token)
        return longer[len(shorter):len(shorter) + 1] == ' '
    return False


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request, db: AsyncSession = Depends(get_db)):
    devices_result = await db.execute(select(Device).order_by(Device.last_sync.desc()))
    devices = devices_result.scalars().all()

    return templates.TemplateResponse(
        request, "index.html",
        context={"devices": devices},
    )


@router.post("/files/remove")
async def dashboard_remove_file(path: str = Form(...)):
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)
    canonical_dict.pop(path, None)
    mf.save_canonical(app.config.CANONICAL_MANIFEST, mf.from_dict(canonical_dict))
    return RedirectResponse(url="/files", status_code=303)


@router.get("/timeline", response_class=HTMLResponse)
async def dashboard_timeline(request: Request, db: AsyncSession = Depends(get_db)):
    from datetime import datetime, timedelta, timezone
    tz = app.config.DISPLAY_TZ
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)

    # Section cutoffs (all in UTC for comparison against stored datetimes)
    just_now_cutoff = now_utc - timedelta(minutes=15)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    week_start = (now_local - timedelta(days=now_local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    # Auto-close stale open events (download-only syncs never get a manifest PUT)
    stale_cutoff = now_utc - timedelta(seconds=app.config.SYNC_EVENT_WINDOW_SECONDS)
    stale_result = await db.execute(
        select(SyncEvent).where(
            SyncEvent.finished_at.is_(None),
            SyncEvent.started_at < stale_cutoff.replace(tzinfo=None),
        )
    )
    for stale in stale_result.scalars().all():
        stale.finished_at = stale.started_at  # close at start time (no-op duration)
    await db.commit()

    result = await db.execute(
        select(SyncEvent).order_by(SyncEvent.started_at.desc()).limit(200)
    )
    events = result.scalars().all()

    # Build enriched event list with timezone-converted times and PNG pairings
    items = []
    for event in events:
        device = await db.get(Device, event.device_id)
        files_result = await db.execute(
            select(SyncEventFile)
            .where(SyncEventFile.sync_event_id == event.id)
            .order_by(SyncEventFile.file_path)
        )
        files = files_result.scalars().all()

        png_map = {}
        paired_png_paths = set()
        file_paths_in_event = {f.file_path for f in files}
        for f in files:
            if f.file_path.endswith(".png") and f.action == "uploaded":
                state_path = f.file_path[:-4]
                if state_path in file_paths_in_event:
                    png_map[state_path] = f.hash
                    paired_png_paths.add(f.file_path)

        event_utc = event.started_at.replace(tzinfo=timezone.utc)
        event_local = event_utc.astimezone(tz)
        finished_utc = event.finished_at.replace(tzinfo=timezone.utc) if event.finished_at else None

        # Exclude PNG files from the display list — they're shown as thumbnails
        display_files = [f for f in files if not f.file_path.endswith(".png")]

        items.append({
            "event": event,
            "device": device,
            "files": files,
            "display_files": display_files,
            "png_map": png_map,
            "started_at_fmt": event_local.strftime("%-m/%-d/%y at %-I:%M:%S %p"),
            "started_at_iso": event_utc.isoformat(),
            "event_utc": event_utc,
            "finished_utc": finished_utc,
            "device_color": _device_color(device.name if device else ""),
        })

    # Merge events from the same device that started within 10 seconds of each
    # other — RetroArch's concurrent connections often split one logical sync
    # across multiple DB events.
    merged = []
    for item in items:
        if (
            merged
            and merged[-1]["device"] and item["device"]
            and merged[-1]["device"].id == item["device"].id
            and abs((merged[-1]["event_utc"] - item["event_utc"]).total_seconds()) <= 10
        ):
            prev = merged[-1]
            # Merge file lists and PNG maps
            combined_files = list(prev["files"]) + list(item["files"])
            combined_file_paths = {f.file_path for f in combined_files}
            combined_png_map = {}
            combined_paired = set()
            for f in combined_files:
                if f.file_path.endswith(".png") and f.action == "uploaded":
                    state_path = f.file_path[:-4]
                    if state_path in combined_file_paths:
                        combined_png_map[state_path] = f.hash
                        combined_paired.add(f.file_path)
            combined_display = [f for f in combined_files if not f.file_path.endswith(".png")]
            prev["files"] = combined_files
            prev["display_files"] = combined_display
            prev["png_map"] = combined_png_map
            prev["files_uploaded"] = sum(1 for f in combined_display if f.action == "uploaded")
            prev["files_downloaded"] = prev.get("files_downloaded", 0) + item.get("files_downloaded", 0)
            if item["finished_utc"] and not prev["finished_utc"]:
                prev["finished_utc"] = item["finished_utc"]
        else:
            item = dict(item)
            item["files_uploaded"] = item["event"].files_uploaded
            item["files_downloaded"] = item["event"].files_downloaded
            merged.append(item)

    # Group into timeline sections
    sections = []
    def add_to_section(title, item):
        if not sections or sections[-1]["title"] != title:
            sections.append({"title": title, "events": []})
        sections[-1]["events"].append(item)

    for item in merged:
        t = item["event_utc"]
        if t >= just_now_cutoff:
            add_to_section("Just Now", item)
        elif t >= today_start:
            add_to_section("Today", item)
        elif t >= week_start:
            add_to_section("This Week", item)
        else:
            local_t = t.astimezone(tz)
            add_to_section(local_t.strftime("%B %Y"), item)

    return templates.TemplateResponse(
        request, "timeline.html",
        context={"sections": sections, "now_utc": now_utc},
    )


@router.get("/devices", response_class=HTMLResponse)
async def dashboard_devices(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).order_by(Device.last_sync.desc()))
    devices = result.scalars().all()
    force_accept_flags = {d.name: is_force_accept(d.name) for d in devices}
    quarantine_flags = {d.name: get_quarantine(d.name) for d in devices}

    return templates.TemplateResponse(
        request, "devices.html",
        context={
            "devices": devices,
            "force_accept_flags": force_accept_flags,
            "quarantine_flags": quarantine_flags,
        },
    )


@router.post("/devices/{name}/force-accept")
async def dashboard_force_accept(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.name == name))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404)
    set_force_accept(name)
    return RedirectResponse(url="/devices", status_code=303)


@router.post("/devices/{name}/quarantine")
async def dashboard_set_quarantine(name: str, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.name == name))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404)
    form = await request.form()
    set_quarantine(name, saves="saves" in form, states="states" in form)
    return RedirectResponse(url="/devices", status_code=303)


@router.post("/devices/{name}/cancel-force-accept")
async def dashboard_cancel_force_accept(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.name == name))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404)
    clear_force_accept(name)
    return RedirectResponse(url="/devices", status_code=303)


@router.get("/games", response_class=HTMLResponse)
async def dashboard_games(
    request: Request,
    sort: str = "recent",
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)

    # Collect game names from saves and states only (source of truth)
    game_files: dict[str, list] = {}
    for path, hash_val in canonical_dict.items():
        top = path.split('/')[0]
        if top not in ('saves', 'states'):
            continue
        name = _extract_game_name(path)
        if name:
            game_files.setdefault(name, []).append(path)

    # Get activity stats from version history
    result = await db.execute(
        select(Version.file_path, Version.received_at).where(
            or_(
                Version.file_path.like('saves/%'),
                Version.file_path.like('states/%'),
            )
        )
    )
    rows = result.all()

    game_stats: dict[str, dict] = {}
    epoch = datetime(1970, 1, 1)
    for file_path, received_at in rows:
        name = _extract_game_name(file_path)
        if not name or name not in game_files:
            continue
        stats = game_stats.setdefault(name, {'last_activity': epoch, 'activity_count': 0})
        stats['activity_count'] += 1
        if received_at > stats['last_activity']:
            stats['last_activity'] = received_at

    # Look up boxart thumbnail hash for each game using fuzzy name matching
    thumb_boxarts: dict[str, str] = {}  # thumbnail_name → hash
    for path, hash_val in canonical_dict.items():
        if '/Named_Boxarts/' not in path or mf.is_deleted(hash_val):
            continue
        tname = _extract_game_name(path)
        if tname:
            thumb_boxarts[tname] = hash_val

    boxart: dict[str, str] = {}
    for game_name in game_files:
        for tname, hash_val in thumb_boxarts.items():
            if _names_match(game_name, tname):
                boxart[game_name] = hash_val
                break

    games = []
    for name, files in game_files.items():
        base, meta = _format_game_name(name)
        stats = game_stats.get(name, {'last_activity': epoch, 'activity_count': 0})
        games.append({
            'name': name,
            'base': base,
            'meta': meta,
            'file_count': len(files),
            'last_activity': stats['last_activity'],
            'activity_count': stats['activity_count'],
            'boxart_hash': boxart.get(name),
        })

    if sort == 'alpha':
        games.sort(key=lambda g: g['name'].lower())
    elif sort == 'activity':
        games.sort(key=lambda g: g['activity_count'], reverse=True)
    else:
        games.sort(key=lambda g: g['last_activity'], reverse=True)

    return templates.TemplateResponse(
        request, "games.html",
        context={"games": games, "sort": sort},
    )


@router.get("/games/{name:path}", response_class=HTMLResponse)
async def dashboard_game_detail(name: str, request: Request):
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)

    saves, states, roms = [], [], []
    boxarts, snaps, titles = [], [], []

    for path, hash_val in canonical_dict.items():
        extracted = _extract_game_name(path)
        if not extracted or not _names_match(extracted, name):
            continue
        entry = {'path': path, 'hash': hash_val, 'display': path.split('/')[-1]}
        top = path.split('/')[0]
        if top == 'saves':
            saves.append(entry)
        elif top == 'states' and not mf.is_deleted(hash_val):
            states.append(entry)
        elif top == 'system':
            roms.append(entry)
        elif top == 'thumbnails' and not mf.is_deleted(hash_val):
            if '/Named_Boxarts/' in path:
                boxarts.append(entry)
            elif '/Named_Snaps/' in path:
                snaps.append(entry)
            elif '/Named_Titles/' in path:
                titles.append(entry)

    # Pair PNG screenshots with their matching state files, then exclude PNGs from the list.
    state_paths = {e['path'] for e in states if not e['path'].endswith('.png')}
    state_png_map = {}
    states_no_png = []
    for e in states:
        if e['path'].endswith('.png'):
            base_path = e['path'][:-4]
            if base_path in state_paths:
                state_png_map[base_path] = e['hash']
        else:
            states_no_png.append(e)
    states_no_png.sort(key=_state_slot_sort_key)

    base, meta = _format_game_name(name)
    return templates.TemplateResponse(
        request, "game_detail.html",
        context={
            "name": name, "base": base, "meta": meta,
            "saves": saves, "states": states_no_png, "state_png_map": state_png_map,
            "roms": roms, "boxarts": boxarts, "snaps": snaps, "titles": titles,
        },
    )


@router.get("/files", response_class=HTMLResponse)
async def dashboard_files(request: Request):
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)

    # Build two-level hierarchy: top_dir → sub_dir → [entries]
    raw: dict[str, dict[str, list]] = {}
    for entry in canonical:
        parts = entry["path"].split("/")
        top = parts[0] if len(parts) >= 2 else ""
        sub = parts[1] if len(parts) >= 3 else ""
        display = "/".join(parts[2:]) if len(parts) >= 3 else (parts[1] if len(parts) == 2 else parts[0])
        raw.setdefault(top, {}).setdefault(sub, []).append({**entry, "display": display})

    # Convert to a list structure with pre-computed totals for the template
    dirs = []
    for top, subdirs in raw.items():
        total = sum(len(e) for e in subdirs.values())
        sub_list = [
            {"name": sub, "entries": entries}
            for sub, entries in subdirs.items()
        ]
        dirs.append({"name": top, "total": total, "subdirs": sub_list})

    return templates.TemplateResponse(
        request, "files.html",
        context={"dirs": dirs},
    )


@router.get("/files/{path:path}", response_class=HTMLResponse)
async def dashboard_file_detail(path: str, request: Request, db: AsyncSession = Depends(get_db)):
    from datetime import datetime, timedelta, timezone
    tz = app.config.DISPLAY_TZ
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)

    just_now_cutoff = now_utc - timedelta(minutes=15)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    week_start = (now_local - timedelta(days=now_local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    result = await db.execute(
        select(Version)
        .where(Version.file_path == path)
        .order_by(Version.received_at.desc())
    )
    versions = result.scalars().all()
    if not versions:
        raise HTTPException(status_code=404)

    enriched = []
    for v in versions:
        device = await db.get(Device, v.device_id)
        enriched.append({"version": v, "device": device})

    # Find PNG thumbnails paired with each version of this state file.
    version_pngs = {}
    png_path = path + ".png"

    state_sefs_result = await db.execute(
        select(SyncEventFile).where(
            SyncEventFile.file_path == path,
            SyncEventFile.action == "uploaded",
        )
    )
    state_sefs = state_sefs_result.scalars().all()
    event_to_state_hash = {sef.sync_event_id: sef.hash for sef in state_sefs}

    if event_to_state_hash:
        png_sefs_result = await db.execute(
            select(SyncEventFile).where(
                SyncEventFile.file_path == png_path,
                SyncEventFile.sync_event_id.in_(list(event_to_state_hash.keys())),
            )
        )
        for psef in png_sefs_result.scalars().all():
            state_hash = event_to_state_hash.get(psef.sync_event_id)
            if state_hash:
                version_pngs[state_hash] = psef.hash

    # Group versions into time sections
    sections: list[dict] = []
    def _add(title: str, item: dict) -> None:
        if not sections or sections[-1]["title"] != title:
            sections.append({"title": title, "versions": []})
        sections[-1]["versions"].append(item)

    for item in enriched:
        t = item["version"].received_at.replace(tzinfo=timezone.utc)
        if t >= just_now_cutoff:
            _add("Just Now", item)
        elif t >= today_start:
            _add("Today", item)
        elif t >= week_start:
            _add("This Week", item)
        else:
            _add(t.astimezone(tz).strftime("%B %Y"), item)

    return templates.TemplateResponse(
        request, "file_detail.html",
        context={"path": path, "sections": sections, "version_pngs": version_pngs},
    )


@router.post("/files/{path:path}/revert/{version_id}")
async def dashboard_revert_file(path: str, version_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Version).where(Version.id == version_id, Version.file_path == path)
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404)

    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)
    if version.hash == "":
        canonical_dict.pop(path, None)
    else:
        canonical_dict[path] = version.hash

    mf.save_canonical(app.config.CANONICAL_MANIFEST, mf.from_dict(canonical_dict))

    all_versions = await db.execute(select(Version).where(Version.file_path == path))
    for v in all_versions.scalars().all():
        v.is_canonical = v.id == version_id

    await db.commit()
    return RedirectResponse(url=f"/files/{path}", status_code=303)


