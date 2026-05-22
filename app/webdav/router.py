"""WebDAV endpoints for RetroArch cloud sync.

Based on RetroArch source analysis (network/cloud_sync/webdav.c,
tasks/task_cloudsync.c):

  - Verbs used: OPTIONS, GET, PUT, DELETE, MKCOL, MOVE
  - PROPFIND is NOT used — no XML/directory listing needed
  - Manifest filename: manifest.server
  - 404 on GET = success (file not found)
  - 405 on MKCOL = success (directory exists)
  - OPTIONS on /sync/{device}/ = sync begin — opens a new SyncEvent
  - PUT manifest.server = sync end — closes the SyncEvent
  - GET manifest.server records what canonical the device has seen,
    enabling clean-advance vs. conflict detection on subsequent uploads.
"""

import logging

import app.config
from app import manifest as mf
from app.database import get_db
from app.models import Device, DeviceFileFetch
from app.store import read_blob
from app.sync.engine import (
    clear_force_accept,
    handle_file_delete,
    handle_file_upload,
    handle_manifest_upload,
    is_force_accept,
    record_file_as_fetched,
    save_last_fetched_manifest,
)
from app.sync.quarantine import (
    build_hybrid_manifest,
    get_quarantine,
    handle_quarantined_delete,
    handle_quarantined_upload,
    is_quarantined,
)
from app.sync.events import get_open_event, get_or_create_device, open_sync_event
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sync/{device_name}")

MANIFEST_FILENAME = "manifest.server"


def _is_manifest(path: str) -> bool:
    return path.strip("/") == MANIFEST_FILENAME


# ─── OPTIONS — sync begin ─────────────────────────────────────────────────────


@router.options("/{path:path}")
@router.options("/")
async def webdav_options(
    device_name: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    device, _ = await get_or_create_device(db, device_name)
    await open_sync_event(db, device)
    await db.commit()
    return Response(
        status_code=200,
        headers={"DAV": "1", "Allow": "OPTIONS, GET, PUT, DELETE, MKCOL, MOVE"},
    )


# ─── GET — download file or manifest ─────────────────────────────────────────


@router.get("/{path:path}")
async def webdav_get(
    device_name: str,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    if _is_manifest(path):
        return await _serve_manifest(device_name, db)

    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)

    if path not in canonical_dict:
        return Response(status_code=404)

    file_hash = canonical_dict[path]
    if mf.is_deleted(file_hash):
        return Response(status_code=404)

    data = await read_blob(file_hash)
    if data is None:
        return Response(status_code=404)

    device, _ = await get_or_create_device(db, device_name)
    sync_event = await get_open_event(db, device.id)
    if sync_event is None:
        sync_event = await open_sync_event(db, device)
    log.info("GET %s — device=%s open_event=%s", path, device_name, sync_event.id)
    await record_file_as_fetched(db, device.id, path, file_hash)
    sync_event.files_downloaded += 1
    await db.commit()

    return Response(content=data, media_type="application/octet-stream")


async def _serve_manifest(device_name: str, db: AsyncSession) -> Response:
    device, _ = await get_or_create_device(db, device_name)
    await db.commit()

    # Re-upload All Files: serve empty manifest so RetroArch re-uploads everything.
    if is_force_accept(device):
        return Response(content=b"[]", media_type="application/json")

    # Quarantined device: serve hybrid manifest (main canonical minus quarantined
    # types, plus the device's own quarantine canonical for those types).
    q = get_quarantine(device)
    if any(q.values()):
        canonical = build_hybrid_manifest(device)
    else:
        canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)

    if not canonical:
        return Response(status_code=404)

    # Seed fetch tracking on first contact so uploads made after the initial
    # download are treated as clean advances, not conflicts.
    count_result = await db.execute(
        select(func.count()).where(DeviceFileFetch.device_id == device.id)
    )
    if count_result.scalar() == 0:
        await save_last_fetched_manifest(db, device.id, canonical)
        await db.commit()

    return Response(content=mf.serialize(canonical), media_type="application/json")


# ─── PUT — upload file or manifest ───────────────────────────────────────────


@router.put("/{path:path}")
async def webdav_put(
    device_name: str,
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    data = await request.body()
    device, _ = await get_or_create_device(db, device_name)

    if _is_manifest(path):
        return await _put_manifest(db, device, data)

    sync_event = await get_open_event(db, device.id)
    if sync_event is None:
        sync_event = await open_sync_event(db, device)

    if is_quarantined(device, path):
        await handle_quarantined_upload(device, path, data, sync_event)
    else:
        await handle_file_upload(db, device, path, data, sync_event)
    await db.commit()
    return Response(status_code=201)


async def _put_manifest(db: AsyncSession, device: Device, data: bytes) -> Response:
    sync_event = await get_open_event(db, device.id)
    if sync_event is None:
        sync_event = await open_sync_event(db, device)

    await handle_manifest_upload(db, device, data, sync_event)
    await db.commit()
    return Response(status_code=201)


# ─── DELETE ───────────────────────────────────────────────────────────────────


@router.delete("/{path:path}")
async def webdav_delete(
    device_name: str,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    if _is_manifest(path):
        return Response(status_code=403)

    device, _ = await get_or_create_device(db, device_name)
    sync_event = await get_open_event(db, device.id)
    if sync_event is None:
        sync_event = await open_sync_event(db, device)

    if is_quarantined(device, path):
        handle_quarantined_delete(device, path, sync_event)
    else:
        await handle_file_delete(db, device, path, sync_event)
    await db.commit()
    return Response(status_code=204)


# ─── MKCOL — always succeeds (405 = already exists, which RetroArch accepts) ─


@router.api_route("/{path:path}", methods=["MKCOL"])
async def webdav_mkcol(device_name: str, path: str) -> Response:
    return Response(status_code=405)


# ─── MOVE — soft-delete (RetroArch's non-destructive delete mode) ─────────────


@router.api_route("/{path:path}", methods=["MOVE"])
async def webdav_move(
    device_name: str,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    device, _ = await get_or_create_device(db, device_name)
    sync_event = await get_open_event(db, device.id)
    if sync_event is None:
        sync_event = await open_sync_event(db, device)

    if is_quarantined(device, path):
        handle_quarantined_delete(device, path, sync_event)
    else:
        await handle_file_delete(db, device, path, sync_event)
    await db.commit()
    return Response(status_code=201)
