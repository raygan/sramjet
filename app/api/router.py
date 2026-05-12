"""Internal REST API consumed by the dashboard frontend."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import manifest as mf
from app.config import CANONICAL_MANIFEST
from app.database import get_db
from app.models import Conflict, Device, ManifestSnapshot, SyncEvent, SyncEventFile, Version
from app.store import read_blob
from app.sync.engine import handle_conflict_resolution

router = APIRouter(prefix="/api")


# ─── Devices ──────────────────────────────────────────────────────────────────


@router.get("/devices")
async def list_devices(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).order_by(Device.last_sync.desc()))
    devices = result.scalars().all()
    return [_device_out(d) for d in devices]


@router.get("/devices/{name}")
async def get_device(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.name == name))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404)
    return _device_out(device)


class DeviceUpdate(BaseModel):
    display_name: str


@router.patch("/devices/{name}")
async def rename_device(name: str, body: DeviceUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.name == name))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404)
    device.display_name = body.display_name
    await db.commit()
    return _device_out(device)


@router.delete("/devices/{name}", status_code=204)
async def remove_device(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.name == name))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404)
    await db.delete(device)
    await db.commit()


# ─── Timeline ─────────────────────────────────────────────────────────────────


@router.get("/timeline")
async def get_timeline(
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * page_size
    result = await db.execute(
        select(SyncEvent)
        .order_by(SyncEvent.started_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    events = result.scalars().all()

    out = []
    for event in events:
        device_result = await db.execute(select(Device).where(Device.id == event.device_id))
        device = device_result.scalar_one_or_none()
        files_result = await db.execute(
            select(SyncEventFile).where(SyncEventFile.sync_event_id == event.id)
        )
        files = files_result.scalars().all()
        out.append({
            "id": event.id,
            "device": device.name if device else "unknown",
            "device_display": device.display_name if device else "unknown",
            "started_at": _dt(event.started_at),
            "finished_at": _dt(event.finished_at),
            "files_uploaded": event.files_uploaded,
            "files_downloaded": event.files_downloaded,
            "had_conflicts": event.had_conflicts,
            "files": [{"path": f.file_path, "action": f.action, "hash": f.hash} for f in files],
        })
    return out


# ─── Conflicts ────────────────────────────────────────────────────────────────


@router.get("/conflicts")
async def list_conflicts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conflict)
        .where(Conflict.resolved_at.is_(None))
        .order_by(Conflict.detected_at.desc())
    )
    conflicts = result.scalars().all()

    out = []
    for c in conflicts:
        da = await db.get(Device, c.device_a_id)
        db_ = await db.get(Device, c.device_b_id)
        out.append({
            "id": c.id,
            "file_path": c.file_path,
            "canonical_hash": c.canonical_hash,
            "device_a": {"name": da.name, "display_name": da.display_name} if da else None,
            "device_b": {"name": db_.name, "display_name": db_.display_name} if db_ else None,
            "hash_a": c.hash_a,
            "hash_b": c.hash_b,
            "detected_at": _dt(c.detected_at),
        })
    return out


class ResolveBody(BaseModel):
    winning_hash: str  # hash_a, hash_b, or canonical_hash


@router.post("/conflicts/{conflict_id}/resolve", status_code=204)
async def resolve_conflict(
    conflict_id: int,
    body: ResolveBody,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Conflict).where(Conflict.id == conflict_id))
    conflict = result.scalar_one_or_none()
    if conflict is None:
        raise HTTPException(status_code=404)
    if conflict.resolved_at is not None:
        raise HTTPException(status_code=409, detail="Already resolved")
    if body.winning_hash not in (conflict.hash_a, conflict.hash_b, conflict.canonical_hash):
        raise HTTPException(status_code=400, detail="winning_hash must be hash_a, hash_b, or canonical_hash")

    await handle_conflict_resolution(db, conflict, body.winning_hash)
    await db.commit()


# ─── Files ────────────────────────────────────────────────────────────────────


@router.get("/files")
async def browse_files(db: AsyncSession = Depends(get_db)):
    canonical = mf.load_canonical(CANONICAL_MANIFEST)
    return canonical


@router.get("/blobs/{hash}")
async def serve_blob(hash: str):
    data = await read_blob(hash)
    if data is None:
        raise HTTPException(status_code=404)
    return Response(content=data, media_type="image/png")


@router.delete("/files/{path:path}", status_code=204)
async def remove_file_from_canonical(path: str):
    canonical = mf.load_canonical(CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)
    if path not in canonical_dict:
        raise HTTPException(status_code=404)
    del canonical_dict[path]
    mf.save_canonical(CANONICAL_MANIFEST, mf.from_dict(canonical_dict))


@router.get("/files/{path:path}/history")
async def file_history(path: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Version)
        .where(Version.file_path == path)
        .order_by(Version.received_at.desc())
    )
    versions = result.scalars().all()
    out = []
    for v in versions:
        device = await db.get(Device, v.device_id)
        out.append({
            "id": v.id,
            "hash": v.hash,
            "is_canonical": v.is_canonical,
            "received_at": _dt(v.received_at),
            "device": device.name if device else "unknown",
        })
    return out


@router.post("/files/{path:path}/revert/{version_id}", status_code=204)
async def revert_file(path: str, version_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Version).where(Version.id == version_id, Version.file_path == path)
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404)

    canonical = mf.load_canonical(CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)
    canonical_dict[path] = version.hash
    mf.save_canonical(CANONICAL_MANIFEST, mf.from_dict(canonical_dict))

    # Mark this version as canonical, decanonize others for same path
    all_versions_result = await db.execute(
        select(Version).where(Version.file_path == path)
    )
    for v in all_versions_result.scalars().all():
        v.is_canonical = v.id == version_id

    await db.commit()


# ─── Snapshots ────────────────────────────────────────────────────────────────


@router.get("/snapshots")
async def list_snapshots(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ManifestSnapshot).order_by(ManifestSnapshot.created_at.desc())
    )
    snapshots = result.scalars().all()
    out = []
    for s in snapshots:
        event = await db.get(SyncEvent, s.sync_event_id)
        device = await db.get(Device, event.device_id) if event else None
        manifest = mf.parse(s.manifest_json)
        out.append({
            "id": s.id,
            "created_at": _dt(s.created_at),
            "device": device.name if device else "unknown",
            "file_count": len(manifest),
        })
    return out


@router.post("/snapshots/{snapshot_id}/revert", status_code=204)
async def revert_snapshot(snapshot_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ManifestSnapshot).where(ManifestSnapshot.id == snapshot_id))
    snapshot = result.scalar_one_or_none()
    if snapshot is None:
        raise HTTPException(status_code=404)

    restored = mf.parse(snapshot.manifest_json)
    mf.save_canonical(CANONICAL_MANIFEST, restored)
    await db.commit()


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _device_out(d: Device) -> dict:
    return {
        "name": d.name,
        "display_name": d.display_name,
        "first_seen": _dt(d.first_seen),
        "last_sync": _dt(d.last_sync),
    }


def _dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
