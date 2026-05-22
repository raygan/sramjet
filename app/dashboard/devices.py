"""Dashboard devices page and device action routes."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Device
from app.sync.engine import clear_force_accept, is_force_accept, set_force_accept
from app.sync.quarantine import get_quarantine, set_quarantine
from app.dashboard.templates import templates

router = APIRouter()


@router.get("/devices", response_class=HTMLResponse)
async def dashboard_devices(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).order_by(Device.last_sync.desc()))
    devices = result.scalars().all()
    force_accept_flags = {d.name: is_force_accept(d) for d in devices}
    quarantine_flags = {d.name: get_quarantine(d) for d in devices}

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
    set_force_accept(device)
    await db.commit()
    return RedirectResponse(url="/devices", status_code=303)


@router.post("/devices/{name}/cancel-force-accept")
async def dashboard_cancel_force_accept(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.name == name))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404)
    clear_force_accept(device)
    await db.commit()
    return RedirectResponse(url="/devices", status_code=303)


@router.post("/devices/{name}/quarantine")
async def dashboard_set_quarantine(name: str, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.name == name))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404)
    form = await request.form()
    set_quarantine(device, saves="saves" in form, states="states" in form)
    await db.commit()
    return RedirectResponse(url="/devices", status_code=303)
