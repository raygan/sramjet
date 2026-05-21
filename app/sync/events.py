"""Sync event lifecycle management.

A sync event is opened by the OPTIONS request and closed by the manifest PUT.
If RetroArch finds no changes to sync, it skips the manifest PUT entirely and
exits cleanly — so we must also auto-close events older than SYNC_EVENT_WINDOW_SECONDS.

To avoid duplicate events from rapid OPTIONS requests, we reuse any open event
that is still within the window rather than always creating a new one.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.config
from app.models import Device, SyncEvent


async def get_or_create_device(db: AsyncSession, name: str) -> tuple[Device, bool]:
    """Return (device, created). Creates device on first contact."""
    result = await db.execute(select(Device).where(Device.name == name))
    device = result.scalar_one_or_none()
    if device is None:
        now = datetime.now(timezone.utc)
        device = Device(
            name=name,
            display_name=name,
            first_seen=now,
            last_sync=None,
        )
        db.add(device)
        await db.flush()
        return device, True
    return device, False


async def open_sync_event(db: AsyncSession, device: Device) -> SyncEvent:
    """Return a current sync event for this device, creating one if needed.

    - Any open events older than SYNC_EVENT_WINDOW_SECONDS are closed first.
    - If a fresh open event already exists, reuse it (avoids duplicates from
      rapid OPTIONS requests, and from "no-change" syncs that never PUT a manifest).
    """
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(SyncEvent).where(
            SyncEvent.device_id == device.id,
            SyncEvent.finished_at.is_(None),
        )
    )
    open_events = result.scalars().all()

    fresh_event = None
    for event in open_events:
        age = (now - event.started_at.replace(tzinfo=timezone.utc)).total_seconds()
        if age > app.config.SYNC_EVENT_WINDOW_SECONDS:
            event.finished_at = now  # auto-close stale events
        elif fresh_event is None:
            fresh_event = event
        else:
            # Extra duplicate fresh event — close it
            event.finished_at = now

    if fresh_event is not None:
        return fresh_event

    event = SyncEvent(
        device_id=device.id,
        started_at=now,
        finished_at=None,
        files_uploaded=0,
        files_downloaded=0,
    )
    db.add(event)
    await db.flush()
    return event


async def get_open_event(db: AsyncSession, device_id: int) -> SyncEvent | None:
    result = await db.execute(
        select(SyncEvent).where(
            SyncEvent.device_id == device_id,
            SyncEvent.finished_at.is_(None),
        )
    )
    # Return the most recent one in case there are stale duplicates not yet cleaned
    events = result.scalars().all()
    if not events:
        return None
    return max(events, key=lambda e: e.started_at)


async def close_sync_event(db: AsyncSession, event: SyncEvent) -> None:
    event.finished_at = datetime.now(timezone.utc)
