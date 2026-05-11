"""Minimal Phase 1 dashboard — functional, not polished."""

import app.config
from app import manifest as mf
from app.database import get_db
from app.models import Conflict, Device, SyncEvent
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
templates = Jinja2Templates(directory="templates")


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


@router.get("/timeline", response_class=HTMLResponse)
async def dashboard_timeline(request: Request, db: AsyncSession = Depends(get_db)):
    from datetime import datetime, timezone
    result = await db.execute(
        select(SyncEvent).order_by(SyncEvent.started_at.desc()).limit(100)
    )
    events = result.scalars().all()

    enriched = []
    for event in events:
        device = await db.get(Device, event.device_id)
        enriched.append({"event": event, "device": device})

    return templates.TemplateResponse(
        request, "timeline.html",
        context={"events": enriched, "now": datetime.now(timezone.utc).replace(tzinfo=None)},
    )


@router.get("/devices", response_class=HTMLResponse)
async def dashboard_devices(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).order_by(Device.last_sync.desc()))
    devices = result.scalars().all()

    return templates.TemplateResponse(
        request, "devices.html",
        context={"devices": devices},
    )


@router.get("/files", response_class=HTMLResponse)
async def dashboard_files(request: Request):
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    return templates.TemplateResponse(
        request, "files.html",
        context={"files": canonical},
    )
