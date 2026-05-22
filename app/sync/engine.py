"""Core sync engine: file upload handling, versioning, canonical updates."""

import logging
from datetime import datetime, timezone

import app.config
from app import manifest as mf
from app.models import Device, DeviceFileFetch, ManifestSnapshot, StoredFile, SyncEvent, SyncEventFile, Version
from app.store import compute_md5, store_blob
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


async def handle_file_upload(
    db: AsyncSession,
    device: Device,
    file_path: str,
    data: bytes,
    sync_event: SyncEvent,
) -> str:
    now = datetime.now(timezone.utc)
    incoming_hash = compute_md5(data)

    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)
    canonical_hash = canonical_dict.get(file_path)

    # Force-accept: treat this sync like a fresh first sync, bypassing all
    # conflict detection. Flag is set by the user via the dashboard and cleared
    # automatically when the device's manifest PUT completes.
    if is_force_accept(device):
        if canonical_hash != incoming_hash:
            await _accept_as_canonical(db, device, file_path, incoming_hash, data, now, sync_event)
        return incoming_hash

    if canonical_hash is None or mf.is_deleted(canonical_hash):
        # No prior canonical, or file was previously deleted — treat as re-creation
        await _accept_as_canonical(db, device, file_path, incoming_hash, data, now, sync_event)
        return incoming_hash

    if canonical_hash == incoming_hash:
        # Identical to canonical — nothing to store
        return incoming_hash

    # File differs from canonical. Check if this device knew about the current canonical
    # by looking at what they last fetched from us.
    device_last_known_hash = await _get_device_last_known_hash(db, device.id, file_path)

    if device_last_known_hash == canonical_hash:
        # Device fetched the current canonical and is advancing it — clean update
        await _accept_as_canonical(db, device, file_path, incoming_hash, data, now, sync_event)
        return incoming_hash

    if incoming_hash == device_last_known_hash:
        # Device is re-uploading the same version it had at its last successful sync.
        # Canonical has since moved forward (another device made progress). This is a
        # stale upload, not new progress — silently ignore it so the device can download
        # the canonical version on this sync without a conflict.
        return incoming_hash

    # Both devices made independent changes since the last common state.
    # Auto-resolve by accepting the incoming upload — last write wins.
    log.warning(
        "conflict auto-resolved: device=%s file=%s incoming=%s canonical=%s last_known=%s",
        device.name, file_path, incoming_hash[:8], canonical_hash[:8],
        device_last_known_hash[:8] if device_last_known_hash else None,
    )
    await _accept_as_canonical(db, device, file_path, incoming_hash, data, now, sync_event)
    return incoming_hash


async def handle_file_delete(
    db: AsyncSession,
    device: Device,
    file_path: str,
    sync_event: SyncEvent,
) -> None:
    now = datetime.now(timezone.utc)
    version = Version(
        file_path=file_path,
        hash="",
        device_id=device.id,
        received_at=now,
        is_canonical=False,
    )
    db.add(version)
    await _record_event_file(db, sync_event, file_path, "deleted", "")
    sync_event.files_uploaded += 1


async def handle_manifest_upload(
    db: AsyncSession,
    device: Device,
    data: bytes,
    sync_event: SyncEvent,
) -> None:
    now = datetime.now(timezone.utc)

    # Promote this device's pending versions to canonical
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)

    pending_versions = await _get_pending_versions_for_device(db, device.id, sync_event.started_at)
    for version in pending_versions:
        prev = await db.execute(
            select(Version).where(Version.file_path == version.file_path, Version.is_canonical == True)  # noqa: E712
        )
        for old in prev.scalars().all():
            old.is_canonical = False
        version.is_canonical = True
        if version.hash == "":
            canonical_dict[version.file_path] = ""
        else:
            canonical_dict[version.file_path] = version.hash

    new_canonical = mf.from_dict(canonical_dict)
    mf.save_canonical(app.config.CANONICAL_MANIFEST, new_canonical)

    # Save device's uploaded manifest
    device_manifest_dir = app.config.DEVICES_DIR / device.name
    device_manifest_dir.mkdir(parents=True, exist_ok=True)
    (device_manifest_dir / "manifest.json").write_bytes(data)

    # Save a canonical snapshot
    snapshot_path = app.config.SNAPSHOTS_DIR / f"{int(now.timestamp())}.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(mf.serialize(new_canonical))

    snapshot = ManifestSnapshot(
        sync_event_id=sync_event.id,
        created_at=now,
        manifest_json=mf.serialize(new_canonical).decode(),
    )
    db.add(snapshot)

    await _apply_retention(db, new_canonical)
    device.last_sync = now
    sync_event.finished_at = now
    clear_force_accept(device)
    # Record the canonical state this device successfully synced to.
    # Used for conflict detection on future uploads — updated on sync completion,
    # not on manifest GET, so offline changes made before fetching are detected.
    await save_last_fetched_manifest(db, device.id, new_canonical)


# ─── Trust Next Sync ──────────────────────────────────────────────────────────

_FORCE_ACCEPT_TTL = 300  # seconds — auto-expires if no manifest PUT arrives


def is_force_accept(device: Device) -> bool:
    """Return True if Trust Next Sync is active for this device."""
    if device.force_accept_at is None:
        return False
    age = (datetime.now(timezone.utc) - device.force_accept_at.replace(tzinfo=timezone.utc)).total_seconds()
    return age <= _FORCE_ACCEPT_TTL


def set_force_accept(device: Device) -> None:
    """Activate Trust Next Sync. Caller must commit the session."""
    device.force_accept_at = datetime.now(timezone.utc)


def clear_force_accept(device: Device) -> None:
    """Deactivate Trust Next Sync. Caller must commit the session."""
    device.force_accept_at = None


# ─── Per-file fetch tracking ──────────────────────────────────────────────────


async def record_file_as_fetched(
    db: AsyncSession, device_id: int, file_path: str, file_hash: str
) -> None:
    """Record that a device just downloaded file_path at file_hash.

    After a successful download the device "knows" the canonical hash for
    this file, so any new progress uploaded later is a clean advance, not
    a conflict.
    """
    result = await db.execute(
        select(DeviceFileFetch).where(
            DeviceFileFetch.device_id == device_id,
            DeviceFileFetch.file_path == file_path,
        )
    )
    fetch = result.scalar_one_or_none()
    if fetch is None:
        db.add(DeviceFileFetch(device_id=device_id, file_path=file_path, hash=file_hash))
    elif fetch.hash != file_hash:
        fetch.hash = file_hash


async def save_last_fetched_manifest(
    db: AsyncSession, device_id: int, manifest: mf.Manifest
) -> None:
    """Replace all fetch records for this device with the given manifest.

    Called at sync completion so the device's known state is updated atomically.
    """
    await db.execute(delete(DeviceFileFetch).where(DeviceFileFetch.device_id == device_id))
    for entry in manifest:
        db.add(DeviceFileFetch(device_id=device_id, file_path=entry["path"], hash=entry["hash"]))


async def _get_device_last_known_hash(
    db: AsyncSession, device_id: int, file_path: str
) -> str | None:
    """Return the hash the device last saw for file_path, or None if unknown."""
    result = await db.execute(
        select(DeviceFileFetch).where(
            DeviceFileFetch.device_id == device_id,
            DeviceFileFetch.file_path == file_path,
        )
    )
    fetch = result.scalar_one_or_none()
    return fetch.hash if fetch else None


# ─── Internal helpers ─────────────────────────────────────────────────────────


async def _get_pending_versions_for_device(
    db: AsyncSession, device_id: int, since: datetime
) -> list[Version]:
    """Return versions created during the current sync session that still need promotion."""
    result = await db.execute(
        select(Version).where(
            Version.device_id == device_id,
            Version.is_canonical == False,  # noqa: E712
            Version.received_at >= since,
        )
    )
    return list(result.scalars().all())


async def _accept_as_canonical(
    db: AsyncSession,
    device: Device,
    file_path: str,
    hash: str,
    data: bytes,
    now: datetime,
    sync_event: SyncEvent,
) -> None:
    await store_blob(data)
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)
    canonical_dict[file_path] = hash
    mf.save_canonical(app.config.CANONICAL_MANIFEST, mf.from_dict(canonical_dict))

    prev = await db.execute(
        select(Version).where(Version.file_path == file_path, Version.is_canonical == True)  # noqa: E712
    )
    for old in prev.scalars().all():
        old.is_canonical = False

    version = Version(
        file_path=file_path,
        hash=hash,
        device_id=device.id,
        received_at=now,
        is_canonical=True,
    )
    db.add(version)
    db.add(StoredFile(path=file_path, hash=hash, size=len(data), stored_at=now))
    await _record_event_file(db, sync_event, file_path, "uploaded", hash)
    sync_event.files_uploaded += 1


async def _record_event_file(
    db: AsyncSession, sync_event: SyncEvent, file_path: str, action: str, hash: str
) -> None:
    db.add(SyncEventFile(sync_event_id=sync_event.id, file_path=file_path, action=action, hash=hash))


async def _apply_retention(db: AsyncSession, canonical: list) -> None:
    for dir_name, limit in app.config.LIMITED_HISTORY_DIRS.items():
        result = await db.execute(
            select(Version)
            .where(Version.file_path.like(f"{dir_name}/%"))
            .order_by(Version.received_at.desc())
        )
        versions = list(result.scalars().all())
        non_canonical = [v for v in versions if not v.is_canonical]
        for v in non_canonical[limit:]:
            await db.delete(v)
