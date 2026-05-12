"""Core sync engine: file upload handling, conflict detection, versioning, canonical updates.

Conflict detection logic:
  A conflict occurs when a device uploads a file that differs from canonical AND
  that device has not previously fetched the current canonical version (i.e., they
  didn't know about it). If the device's last GET of manifest.server included
  the current canonical hash for this file, the upload is a clean advance.
"""

from datetime import datetime, timezone

import app.config
from app import manifest as mf
from app.models import Conflict, Device, ManifestSnapshot, StoredFile, SyncEvent, SyncEventFile, Version
from app.store import compute_md5, store_blob
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class ConflictError(Exception):
    def __init__(self, file_path: str):
        self.file_path = file_path
        super().__init__(f"Conflict on {file_path}")


class ActiveConflictsError(Exception):
    def __init__(self, paths: list[str]):
        self.paths = paths
        super().__init__(f"Unresolved conflicts: {paths}")


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
    if is_force_accept(device.name):
        if canonical_hash != incoming_hash:
            await _accept_as_canonical(db, device, file_path, incoming_hash, data, now, sync_event)
        return incoming_hash

    # Reject if this file is already locked in an unresolved conflict
    existing_conflict = await _get_active_conflict(db, file_path)
    if existing_conflict is not None:
        raise ConflictError(file_path)

    if canonical_hash is None or mf.is_deleted(canonical_hash):
        # No prior canonical, or file was previously deleted — treat as re-creation
        await _accept_as_canonical(db, device, file_path, incoming_hash, data, now, sync_event)
        return incoming_hash

    if canonical_hash == incoming_hash:
        # Identical to canonical — nothing to store
        return incoming_hash

    # File differs from canonical. Check if this device knew about the current canonical
    # by looking at what they last fetched from us.
    device_last_known_hash = _get_device_last_known_hash(device.name, file_path)

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

    # Both devices made independent changes since the last common state — genuine conflict
    await store_blob(data)
    conflict_device_result = await db.execute(
        select(Device).where(Device.id != device.id)
        .order_by(Device.last_sync.desc())
        .limit(1)
    )
    # Find which device established the canonical version
    canonical_version_result = await db.execute(
        select(Version)
        .where(Version.file_path == file_path, Version.hash == canonical_hash, Version.is_canonical == True)  # noqa: E712
        .order_by(Version.received_at.desc())
        .limit(1)
    )
    canonical_version = canonical_version_result.scalar_one_or_none()

    if canonical_version is not None:
        canonical_device_result = await db.execute(select(Device).where(Device.id == canonical_version.device_id))
        canonical_device = canonical_device_result.scalar_one_or_none()
    else:
        canonical_device = None

    await _record_conflict(
        db,
        file_path=file_path,
        canonical_hash=canonical_hash,
        device_a=canonical_device or device,
        hash_a=canonical_hash,
        device_b=device,
        hash_b=incoming_hash,
        now=now,
    )
    await _record_event_file(db, sync_event, file_path, "conflicted", incoming_hash)
    sync_event.had_conflicts = True
    raise ConflictError(file_path)


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

    active_conflicts = await _get_all_active_conflicts(db)
    if active_conflicts:
        sync_event.had_conflicts = True
        raise ActiveConflictsError([c.file_path for c in active_conflicts])

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
            # Keep the entry with hash="" so other devices see the deletion
            # signal in the manifest and delete locally, rather than re-uploading.
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
    clear_force_accept(device.name)
    # Record the canonical state this device successfully synced to.
    # This is used for conflict detection on future uploads — only update
    # on sync completion, not on manifest GET, so offline changes made
    # before fetching the manifest are correctly detected as conflicts.
    save_last_fetched_manifest(device.name, new_canonical)


async def handle_conflict_resolution(
    db: AsyncSession,
    conflict: Conflict,
    winning_hash: str,
) -> None:
    now = datetime.now(timezone.utc)
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)

    if winning_hash == "":
        canonical_dict.pop(conflict.file_path, None)
    else:
        canonical_dict[conflict.file_path] = winning_hash

    mf.save_canonical(app.config.CANONICAL_MANIFEST, mf.from_dict(canonical_dict))

    result = await db.execute(
        select(Version).where(
            Version.file_path == conflict.file_path,
            Version.hash == winning_hash,
        )
    )
    for v in result.scalars().all():
        v.is_canonical = True

    conflict.resolved_at = now
    conflict.resolved_by_hash = winning_hash

    # Remove the conflicted file from the losing device(s)' last_fetched_manifest.
    # This ensures their next upload of the losing hash fails the conflict check
    # (device_last_known_hash = None ≠ canonical_hash) and gets rejected with 409,
    # forcing them to delete their local file and download the winner instead.
    if winning_hash == conflict.hash_a:
        losing_ids = [conflict.device_b_id]
    elif winning_hash == conflict.hash_b:
        losing_ids = [conflict.device_a_id]
    else:
        # canonical_hash won — both device versions lost
        losing_ids = [conflict.device_a_id, conflict.device_b_id]

    for device_id in losing_ids:
        device_result = await db.execute(select(Device).where(Device.id == device_id))
        device = device_result.scalar_one_or_none()
        if device:
            _invalidate_last_fetched_entry(device.name, conflict.file_path)


def is_force_accept(device_name: str) -> bool:
    """Return True if the user has requested that this device's next sync be accepted unconditionally."""
    return (app.config.DEVICES_DIR / device_name / "force_accept").exists()


def set_force_accept(device_name: str) -> None:
    device_dir = app.config.DEVICES_DIR / device_name
    device_dir.mkdir(parents=True, exist_ok=True)
    (device_dir / "force_accept").touch()


def clear_force_accept(device_name: str) -> None:
    flag = app.config.DEVICES_DIR / device_name / "force_accept"
    flag.unlink(missing_ok=True)


def record_file_as_fetched(device_name: str, file_path: str, file_hash: str) -> None:
    """Record that a device just downloaded file_path at file_hash.

    After a successful download the device "knows" the canonical hash for
    this file, so any new progress uploaded later is a clean advance, not
    a conflict.  We only update the single entry rather than the whole
    manifest to avoid clobbering other files' tracking state.
    """
    path = app.config.DEVICES_DIR / device_name / "last_fetched_manifest.json"
    if path.exists():
        manifest_dict = mf.to_dict(mf.load_canonical(path))
    else:
        manifest_dict = {}
    if manifest_dict.get(file_path) == file_hash:
        return
    manifest_dict[file_path] = file_hash
    mf.save_canonical(path, mf.from_dict(manifest_dict))


def _invalidate_last_fetched_entry(device_name: str, file_path: str) -> None:
    """Remove one file from a device's last_fetched_manifest so the next upload
    of that file is treated as unknown and triggers conflict detection."""
    path = app.config.DEVICES_DIR / device_name / "last_fetched_manifest.json"
    if not path.exists():
        return
    manifest_dict = mf.to_dict(mf.load_canonical(path))
    manifest_dict.pop(file_path, None)
    mf.save_canonical(path, mf.from_dict(manifest_dict))


def save_last_fetched_manifest(device_name: str, manifest: mf.Manifest) -> None:
    """Record the canonical manifest a device fetched, so we can detect clean advances."""
    device_dir = app.config.DEVICES_DIR / device_name
    device_dir.mkdir(parents=True, exist_ok=True)
    (device_dir / "last_fetched_manifest.json").write_bytes(mf.serialize(manifest))


def _get_device_last_known_hash(device_name: str, file_path: str) -> str | None:
    """Return the hash the device last saw for file_path, or None if unknown."""
    path = app.config.DEVICES_DIR / device_name / "last_fetched_manifest.json"
    if not path.exists():
        return None
    fetched = mf.load_canonical(path)
    return mf.to_dict(fetched).get(file_path)


# ─── Internal helpers ────────────────────────────────────────────────────────


async def _get_active_conflict(db: AsyncSession, file_path: str) -> Conflict | None:
    result = await db.execute(
        select(Conflict).where(Conflict.file_path == file_path, Conflict.resolved_at.is_(None))
    )
    return result.scalar_one_or_none()


async def _get_all_active_conflicts(db: AsyncSession) -> list[Conflict]:
    result = await db.execute(select(Conflict).where(Conflict.resolved_at.is_(None)))
    return list(result.scalars().all())


async def _get_pending_versions_for_device(
    db: AsyncSession, device_id: int, since: datetime
) -> list[Version]:
    """Return versions created during the current sync session that still need promotion.

    Filtering by `since` (the sync event start time) prevents the race condition where
    _accept_as_canonical decanonizes an old version, making it look like a pending
    version to the concurrent manifest PUT handler, which would then re-promote it and
    overwrite the newly accepted canonical.
    """
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


async def _record_conflict(
    db: AsyncSession,
    file_path: str,
    canonical_hash: str,
    device_a: Device,
    hash_a: str,
    device_b: Device,
    hash_b: str,
    now: datetime,
) -> None:
    existing = await _get_active_conflict(db, file_path)
    if existing is None:
        db.add(Conflict(
            file_path=file_path,
            canonical_hash=canonical_hash,
            device_a_id=device_a.id,
            device_b_id=device_b.id,
            hash_a=hash_a,
            hash_b=hash_b,
            detected_at=now,
        ))


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
