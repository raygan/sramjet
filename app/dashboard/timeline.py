"""Dashboard timeline page."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.config
from app.database import get_db
from app.models import Device, SyncEvent, SyncEventFile
from app.dashboard.templates import templates
from app.dashboard.utils import device_color

router = APIRouter()


@router.get("/timeline", response_class=HTMLResponse)
async def dashboard_timeline(request: Request, db: AsyncSession = Depends(get_db)):
    tz = app.config.DISPLAY_TZ
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)

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
        stale.finished_at = stale.started_at
    await db.commit()

    result = await db.execute(
        select(SyncEvent).order_by(SyncEvent.started_at.desc()).limit(200)
    )
    events = result.scalars().all()

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
        for f in files:
            if f.file_path.endswith(".png") and f.action == "uploaded":
                state_path = f.file_path[:-4]
                if state_path.startswith("states/"):
                    png_map[state_path] = f.hash
                    paired_png_paths.add(f.file_path)

        event_utc = event.started_at.replace(tzinfo=timezone.utc)
        event_local = event_utc.astimezone(tz)
        finished_utc = event.finished_at.replace(tzinfo=timezone.utc) if event.finished_at else None
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
            "device_color": device_color(device.name if device else ""),
        })

    # Merge events from the same device within 10 seconds of each other —
    # RetroArch's concurrent connections often split one logical sync.
    merged = []
    for item in items:
        if (
            merged
            and merged[-1]["device"] and item["device"]
            and merged[-1]["device"].id == item["device"].id
            and abs((merged[-1]["event_utc"] - item["event_utc"]).total_seconds()) <= 10
        ):
            prev = merged[-1]
            combined_files = list(prev["files"]) + list(item["files"])
            combined_png_map = {}
            combined_paired = set()
            for f in combined_files:
                if f.file_path.endswith(".png") and f.action == "uploaded":
                    state_path = f.file_path[:-4]
                    if state_path.startswith("states/"):
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
            add_to_section(t.astimezone(tz).strftime("%B %Y"), item)

    return templates.TemplateResponse(
        request, "timeline.html",
        context={"sections": sections, "now_utc": now_utc},
    )
