"""Minimal Phase 1 dashboard — functional, not polished."""

import app.config
from app import manifest as mf
from app.database import get_db
from app.models import Conflict, Device, SyncEvent, SyncEventFile, Version
from app.sync.engine import clear_force_accept, handle_conflict_resolution, is_force_accept, set_force_accept
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Jinja2 helpers for path display
templates.env.filters["basename"] = lambda p: p.split("/")[-1]
templates.env.filters["dirname"] = lambda p: "/".join(p.split("/")[:-1])

_DEVICE_COLORS = ["blue", "violet", "emerald", "orange", "pink", "teal", "red", "indigo"]

def _device_color(name: str) -> str:
    idx = sum(ord(c) for c in (name or "")) % len(_DEVICE_COLORS)
    return _DEVICE_COLORS[idx]


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request, db: AsyncSession = Depends(get_db)):
    devices_result = await db.execute(select(Device).order_by(Device.last_sync.desc()))
    devices = devices_result.scalars().all()

    conflicts_result = await db.execute(
        select(Conflict).where(Conflict.resolved_at.is_(None))
    )
    conflict_count = len(conflicts_result.scalars().all())

    return templates.TemplateResponse(
        request, "index.html",
        context={"devices": devices, "conflict_count": conflict_count},
    )


@router.get("/conflicts", response_class=HTMLResponse)
async def dashboard_conflicts(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conflict)
        .where(Conflict.resolved_at.is_(None))
        .order_by(Conflict.detected_at.desc())
    )
    conflicts = result.scalars().all()

    enriched = []
    for c in conflicts:
        da = await db.get(Device, c.device_a_id)
        db_ = await db.get(Device, c.device_b_id)
        enriched.append({"conflict": c, "device_a": da, "device_b": db_})

    return templates.TemplateResponse(
        request, "conflicts.html",
        context={"conflicts": enriched},
    )


@router.post("/files/remove")
async def dashboard_remove_file(path: str = Form(...)):
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)
    canonical_dict.pop(path, None)
    mf.save_canonical(app.config.CANONICAL_MANIFEST, mf.from_dict(canonical_dict))
    return RedirectResponse(url="/conflicts", status_code=303)


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

    return templates.TemplateResponse(
        request, "devices.html",
        context={"devices": devices, "force_accept_flags": force_accept_flags},
    )


@router.post("/devices/{name}/force-accept")
async def dashboard_force_accept(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.name == name))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404)
    set_force_accept(name)
    return RedirectResponse(url="/devices", status_code=303)


@router.post("/devices/{name}/cancel-force-accept")
async def dashboard_cancel_force_accept(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.name == name))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404)
    clear_force_accept(name)
    return RedirectResponse(url="/devices", status_code=303)


@router.get("/files", response_class=HTMLResponse)
async def dashboard_files(request: Request):
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    return templates.TemplateResponse(
        request, "files.html",
        context={"files": canonical},
    )


@router.get("/files/{path:path}", response_class=HTMLResponse)
async def dashboard_file_detail(path: str, request: Request, db: AsyncSession = Depends(get_db)):
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
    # A PNG is paired when it was uploaded in the same sync event as the state.
    version_pngs = {}  # {version.hash: png_hash}
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

    return templates.TemplateResponse(
        request, "file_detail.html",
        context={"path": path, "versions": enriched, "version_pngs": version_pngs},
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


@router.post("/conflicts/{conflict_id}/resolve")
async def dashboard_resolve_conflict(
    conflict_id: int,
    winning_hash: str = Form(...),
    db=Depends(get_db),
):
    result = await db.execute(select(Conflict).where(Conflict.id == conflict_id))
    conflict = result.scalar_one_or_none()
    if conflict is None:
        raise HTTPException(status_code=404)
    if conflict.resolved_at is not None:
        raise HTTPException(status_code=409, detail="Already resolved")
    if winning_hash not in (conflict.hash_a, conflict.hash_b, conflict.canonical_hash):
        raise HTTPException(status_code=400, detail="Invalid winning_hash")
    await handle_conflict_resolution(db, conflict, winning_hash)
    await db.commit()
    return RedirectResponse(url="/conflicts", status_code=303)
