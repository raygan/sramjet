"""HTTP endpoints for the MiSTer sync client.

A deliberately small protocol (we control both ends, unlike RetroArch's
WebDAV client):

  GET  /mister/{device}/manifest     — sync begin; MiSTer-view of canonical
  GET  /mister/{device}/files/{path} — download, converted to MiSTer form
  PUT  /mister/{device}/files/{path} — upload, converted to canonical form
  DELETE /mister/{device}/files/{path}
  POST /mister/{device}/complete     — sync end; promotes pending versions

Manifest entries carry the CANONICAL hash (not the hash of converted bytes):
the client tracks (local_hash, canonical_hash) pairs, so conversion never
needs to round-trip exactly — a MiSTer save with an RTC tail is never
clobbered by its RTC-less canonical twin. Entries also carry the canonical
version's mtime so the client can resolve first-contact conflicts by
recency.

All uploads/downloads flow through the same sync engine as RetroArch
devices — the MiSTer appears in the dashboard as a normal device.
"""

import json
import logging

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.config
from app import manifest as mf
from app.database import get_db
from app.mister.convert import to_canonical, to_mister
from app.mister.mapping import SYSTEMS, canonical_to_mister, mister_to_canonical
from app.models import DeviceFileFetch, Version
from app.store import read_blob
from app.sync.engine import (
    complete_sync,
    handle_file_delete,
    handle_file_upload,
    record_file_as_fetched,
    save_last_fetched_manifest,
)
from app.sync.events import get_open_event, get_or_create_device, open_sync_event

log = logging.getLogger(__name__)

router = APIRouter(prefix="/mister/{device_name}")
client_router = APIRouter()


async def _mister_view(db: AsyncSession) -> list[dict]:
    """Translate the canonical manifest into MiSTer paths with canonical hashes."""
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)

    result = await db.execute(
        select(Version.file_path, Version.received_at).where(Version.is_canonical == True)  # noqa: E712
    )
    mtimes = {path: received_at for path, received_at in result.all()}

    entries = []
    for entry in canonical:
        mapped = canonical_to_mister(entry["path"])
        if mapped is None:
            continue
        mister_paths, _system = mapped
        received_at = mtimes.get(entry["path"])
        for mister_path in mister_paths:
            entries.append({
                "path": mister_path,
                "hash": entry["hash"],
                "mtime": received_at.timestamp() if received_at else 0,
            })
    return entries


@router.get("/manifest")
async def mister_manifest(
    device_name: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    device, _ = await get_or_create_device(db, device_name)
    await open_sync_event(db, device)

    entries = await _mister_view(db)

    # Seed fetch tracking on first contact so uploads made after the initial
    # sync are treated as clean advances, not conflicts (same as WebDAV).
    count_result = await db.execute(
        select(func.count()).where(DeviceFileFetch.device_id == device.id)
    )
    if count_result.scalar() == 0:
        canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
        await save_last_fetched_manifest(db, device.id, canonical)

    await db.commit()
    return Response(content=json.dumps(entries), media_type="application/json")


@router.get("/files/{path:path}")
async def mister_download(
    device_name: str,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    mapped = mister_to_canonical(path)
    if mapped is None:
        return Response(status_code=404)
    canonical_path, system = mapped

    canonical_dict = mf.to_dict(mf.load_canonical(app.config.CANONICAL_MANIFEST))
    file_hash = canonical_dict.get(canonical_path)
    if file_hash is None or mf.is_deleted(file_hash):
        return Response(status_code=404)

    data = await read_blob(file_hash)
    if data is None:
        return Response(status_code=404)

    device, _ = await get_or_create_device(db, device_name)
    sync_event = await get_open_event(db, device.id)
    if sync_event is None:
        sync_event = await open_sync_event(db, device)
    await record_file_as_fetched(db, device.id, canonical_path, file_hash)
    sync_event.files_downloaded += 1
    await db.commit()

    return Response(
        content=to_mister(system.converter, data),
        media_type="application/octet-stream",
        headers={"X-Canonical-Hash": file_hash},
    )


@router.put("/files/{path:path}")
async def mister_upload(
    device_name: str,
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    mapped = mister_to_canonical(path)
    if mapped is None:
        return Response(status_code=404)
    canonical_path, system = mapped

    if app.config.MAX_UPLOAD_BYTES > 0:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > app.config.MAX_UPLOAD_BYTES:
            return Response(status_code=413)
    data = await request.body()
    if app.config.MAX_UPLOAD_BYTES > 0 and len(data) > app.config.MAX_UPLOAD_BYTES:
        return Response(status_code=413)

    device, _ = await get_or_create_device(db, device_name)
    sync_event = await get_open_event(db, device.id)
    if sync_event is None:
        sync_event = await open_sync_event(db, device)

    canonical_data = to_canonical(system.converter, data)
    canonical_hash = await handle_file_upload(db, device, canonical_path, canonical_data, sync_event)
    await db.commit()

    return Response(
        content=json.dumps({"canonical_hash": canonical_hash}),
        media_type="application/json",
        status_code=201,
    )


@router.delete("/files/{path:path}")
async def mister_delete(
    device_name: str,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    mapped = mister_to_canonical(path)
    if mapped is None:
        return Response(status_code=404)
    canonical_path, _system = mapped

    device, _ = await get_or_create_device(db, device_name)
    sync_event = await get_open_event(db, device.id)
    if sync_event is None:
        sync_event = await open_sync_event(db, device)

    await handle_file_delete(db, device, canonical_path, sync_event)
    await db.commit()
    return Response(status_code=204)


@router.post("/complete")
async def mister_complete(
    device_name: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    device, _ = await get_or_create_device(db, device_name)
    sync_event = await get_open_event(db, device.id)
    if sync_event is None:
        sync_event = await open_sync_event(db, device)

    await complete_sync(db, device, sync_event)
    entries = await _mister_view(db)
    await db.commit()
    return Response(content=json.dumps(entries), media_type="application/json")


# ─── Client script download (UI auth surface) ────────────────────────────────

CLIENT_SCRIPT_PATH = "clients/mister_sync.py"


@client_router.get("/mister-client")
async def download_client(request: Request, device: str = Query("mister")) -> Response:
    with open(CLIENT_SCRIPT_PATH, encoding="utf-8") as f:
        script = f.read()
    server_url = str(request.base_url).rstrip("/")
    dirs = [d for system in SYSTEMS for d in system.mister_dirs]
    script = (
        script.replace("{{SERVER_URL}}", server_url)
        .replace("{{DEVICE_NAME}}", device)
        .replace("{{MISTER_DIRS}}", json.dumps(dirs).replace('"', '\\"'))
    )
    return Response(
        content=script,
        media_type="text/x-python",
        headers={"Content-Disposition": 'attachment; filename="sramjet_sync.py"'},
    )
