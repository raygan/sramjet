"""Dashboard files browser, file detail, revert, and remove routes."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.config
from app import manifest as mf
from app.database import get_db
from app.models import Device, SyncEventFile, Version
from app.dashboard.templates import templates

router = APIRouter()


@router.get("/files", response_class=HTMLResponse)
async def dashboard_files(request: Request):
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)

    raw: dict[str, dict[str, list]] = {}
    for entry in canonical:
        parts = entry["path"].split("/")
        top = parts[0] if len(parts) >= 2 else ""
        sub = parts[1] if len(parts) >= 3 else ""
        display = "/".join(parts[2:]) if len(parts) >= 3 else (parts[1] if len(parts) == 2 else parts[0])
        raw.setdefault(top, {}).setdefault(sub, []).append({**entry, "display": display})

    dirs = []
    for top, subdirs in raw.items():
        total = sum(len(e) for e in subdirs.values())
        dirs.append({
            "name": top,
            "total": total,
            "subdirs": [{"name": sub, "entries": entries} for sub, entries in subdirs.items()],
        })

    return templates.TemplateResponse(request, "files.html", context={"dirs": dirs})


@router.post("/files/remove")
async def dashboard_remove_file(path: str = Form(...)):
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)
    canonical_dict.pop(path, None)
    mf.save_canonical(app.config.CANONICAL_MANIFEST, mf.from_dict(canonical_dict))
    return RedirectResponse(url="/files", status_code=303)


@router.get("/files/{path:path}", response_class=HTMLResponse)
async def dashboard_file_detail(path: str, request: Request, db: AsyncSession = Depends(get_db)):
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

    # Find PNG thumbnails paired with each version of this state file
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
