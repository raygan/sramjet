"""Manual deletion (tombstoning) and permanent purge, driven from the dashboard.

Tombstoning writes hash="" into the canonical manifest so devices delete the
file on their next sync. Purging permanently removes a path's history rows
and any blobs no longer referenced by anything else.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.config
from app import manifest as mf
from app.models import DeviceFileFetch, StoredFile, SyncEventFile, Version
from app.store import blob_path


def trashed_paths() -> list[str]:
    """All canonical paths currently marked deleted (the 'trash')."""
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    return [e["path"] for e in canonical if mf.is_deleted(e["hash"])]


async def tombstone_paths(db: AsyncSession, paths: list[str]) -> int:
    """Mark paths deleted in the canonical manifest. Caller must commit.

    Returns the number of files tombstoned. Paths not in the canonical
    manifest or already tombstoned are skipped.
    """
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)
    targets = [p for p in paths if p in canonical_dict and not mf.is_deleted(canonical_dict[p])]
    if not targets:
        return 0

    for path in targets:
        canonical_dict[path] = ""
    mf.save_canonical(app.config.CANONICAL_MANIFEST, mf.from_dict(canonical_dict))

    result = await db.execute(
        select(Version).where(Version.file_path.in_(targets), Version.is_canonical == True)  # noqa: E712
    )
    for v in result.scalars().all():
        v.is_canonical = False
    return len(targets)


async def purge_paths(db: AsyncSession, paths: list[str]) -> tuple[int, list[str]]:
    """Permanently remove all history for paths. Caller must commit.

    Deletes Version, SyncEventFile, DeviceFileFetch and StoredFile rows,
    removes the paths from the canonical manifest, and unlinks blobs that
    are no longer referenced by any remaining row or canonical entry.

    Paths with pinned versions are skipped entirely — unpin first.
    Returns (purged_count, skipped_pinned_paths).
    """
    pinned_result = await db.execute(
        select(Version.file_path)
        .where(Version.file_path.in_(paths), Version.is_pinned == True)  # noqa: E712
        .distinct()
    )
    skipped = sorted(set(pinned_result.scalars().all()))
    targets = [p for p in paths if p not in set(skipped)]
    if not targets:
        return 0, skipped

    version_hashes = await db.execute(select(Version.hash).where(Version.file_path.in_(targets)))
    event_hashes = await db.execute(select(SyncEventFile.hash).where(SyncEventFile.file_path.in_(targets)))
    affected = {h for h in list(version_hashes.scalars()) + list(event_hashes.scalars()) if h}

    await db.execute(delete(Version).where(Version.file_path.in_(targets)))
    await db.execute(delete(SyncEventFile).where(SyncEventFile.file_path.in_(targets)))
    await db.execute(delete(DeviceFileFetch).where(DeviceFileFetch.file_path.in_(targets)))
    await db.execute(delete(StoredFile).where(StoredFile.path.in_(targets)))

    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)
    for path in targets:
        canonical_dict.pop(path, None)
    mf.save_canonical(app.config.CANONICAL_MANIFEST, mf.from_dict(canonical_dict))

    if affected:
        survivors: set[str] = {h for h in canonical_dict.values() if h}
        remaining_versions = await db.execute(select(Version.hash).where(Version.hash.in_(affected)))
        survivors |= set(remaining_versions.scalars().all())
        remaining_events = await db.execute(select(SyncEventFile.hash).where(SyncEventFile.hash.in_(affected)))
        survivors |= set(remaining_events.scalars().all())
        for h in affected - survivors:
            blob_path(h).unlink(missing_ok=True)

    return len(targets), skipped
