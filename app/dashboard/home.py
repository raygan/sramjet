"""Dashboard home page — stats, streaks, recent sync, recently played."""

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.config
from app import manifest as mf
from app.database import get_db
from app.models import Device, SyncEvent, SyncEventFile, Version
from app.dashboard.templates import templates
from app.dashboard.utils import (
    compute_streaks,
    device_color,
    extract_game_name,
    fmt_date,
    fmt_date_long,
    fmt_size,
    names_match,
    format_game_name,
    streak_icon,
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request, db: AsyncSession = Depends(get_db)):
    tz = app.config.DISPLAY_TZ
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)
    today = now_local.date()

    # ── Overview stats ─────────────────────────────────────────────────────────
    total_uploads_result = await db.execute(
        select(func.count(Version.id)).where(
            Version.hash != "",
            or_(Version.file_path.like("saves/%"), Version.file_path.like("states/%")),
        )
    )
    total_uploads = total_uploads_result.scalar() or 0

    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)
    game_names: set[str] = set()
    for path in canonical_dict:
        if path.split("/")[0] in ("saves", "states"):
            name = extract_game_name(path)
            if name:
                game_names.add(name)
    games_played = len(game_names)

    total_size = 0
    if app.config.STORE_DIR.is_dir():
        for dirpath, _, filenames in os.walk(app.config.STORE_DIR):
            for fn in filenames:
                try:
                    total_size += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass

    # ── Streaks ────────────────────────────────────────────────────────────────
    rows = await db.execute(
        select(Version.received_at).where(
            Version.hash != "",
            or_(Version.file_path.like("saves/%"), Version.file_path.like("states/%")),
        )
    )
    upload_dates = sorted(set(
        t.replace(tzinfo=timezone.utc).astimezone(tz).date()
        for t in rows.scalars().all()
    ))
    streaks = compute_streaks(upload_dates, today)

    # ── Most recent sync ───────────────────────────────────────────────────────
    recent_event_result = await db.execute(
        select(SyncEvent).order_by(SyncEvent.started_at.desc()).limit(1)
    )
    recent_event = recent_event_result.scalar_one_or_none()
    recent_sync = None
    if recent_event:
        device = await db.get(Device, recent_event.device_id)
        event_utc = recent_event.started_at.replace(tzinfo=timezone.utc)
        files_result = await db.execute(
            select(SyncEventFile)
            .where(SyncEventFile.sync_event_id == recent_event.id)
            .order_by(SyncEventFile.file_path)
        )
        files = files_result.scalars().all()
        png_map: dict[str, str] = {
            path[:-4]: hash_val
            for path, hash_val in canonical_dict.items()
            if path.endswith(".png") and path.startswith("states/") and hash_val
        }
        display_files = [f for f in files if not f.file_path.endswith(".png")]
        recent_sync = {
            "event": recent_event,
            "device": device,
            "started_at_iso": event_utc.isoformat(),
            "started_at_fmt": event_utc.astimezone(tz).strftime("%-m/%-d/%y at %-I:%M %p"),
            "device_color": device_color(device.name if device else ""),
            "files_uploaded": recent_event.files_uploaded,
            "files_downloaded": recent_event.files_downloaded,
            "display_files": display_files,
            "png_map": png_map,
        }

    # ── Recently played games ─────────────────────────────────────────────────
    recent_rows = await db.execute(
        select(Version.file_path, Version.received_at).where(
            Version.hash != "",
            or_(Version.file_path.like("saves/%"), Version.file_path.like("states/%")),
        ).order_by(Version.received_at.desc()).limit(500)
    )
    seen_games: dict[str, object] = {}
    for file_path, received_at in recent_rows.all():
        name = extract_game_name(file_path)
        if name and name not in seen_games:
            seen_games[name] = received_at
        if len(seen_games) >= 3:
            break

    recent_games = []
    for name, last_activity in seen_games.items():
        base, meta = format_game_name(name)
        boxart_hash = None
        for path, hash_val in canonical_dict.items():
            if "/Named_Boxarts/" not in path or mf.is_deleted(hash_val):
                continue
            tname = extract_game_name(path)
            if tname and names_match(name, tname):
                boxart_hash = hash_val
                break
        t_utc = last_activity.replace(tzinfo=timezone.utc)
        recent_games.append({
            "name": name,
            "base": base,
            "meta": meta,
            "boxart_hash": boxart_hash,
            "last_activity_iso": t_utc.isoformat(),
            "last_activity_fmt": fmt_date_long(t_utc.astimezone(tz).date()),
        })

    return templates.TemplateResponse(
        request, "index.html",
        context={
            "total_uploads": f"{total_uploads:,}",
            "games_played": f"{games_played:,}",
            "total_size_value": fmt_size(total_size)[0],
            "total_size_unit": fmt_size(total_size)[1],
            "total_gaming_days": f"{len(upload_dates):,}",
            "first_gaming_day": fmt_date_long(upload_dates[0]) if upload_dates else "",
            "streak_icon": streak_icon(streaks["current"]),
            "current_streak": streaks["current"],
            "current_streak_start": fmt_date(streaks["current_start"]),
            "current_streak_end": fmt_date(streaks["current_end"]),
            "longest_streak": streaks["longest"],
            "longest_streak_start": fmt_date(streaks["longest_start"]),
            "longest_streak_end": fmt_date(streaks["longest_end"]),
            "recent_sync": recent_sync,
            "recent_games": recent_games,
        },
    )
