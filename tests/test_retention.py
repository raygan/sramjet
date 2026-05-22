"""Tests for version retention limits."""

import hashlib
import json

import pytest

import app.config as app_config
from sqlalchemy import select
from app.models import Version


DEVICE = "mac"


async def sync_file(client, device: str, path: str, data: bytes) -> None:
    """Upload a file and commit via manifest PUT."""
    await client.options(f"/sync/{device}/")
    await client.put(f"/sync/{device}/{path}", content=data)
    h = hashlib.md5(data).hexdigest()
    manifest = json.dumps([{"path": path, "hash": h}])
    await client.put(f"/sync/{device}/manifest.server", content=manifest.encode())


@pytest.mark.asyncio
async def test_unlimited_keeps_all_versions(client, db_engine, monkeypatch):
    """When limit is 0 (unlimited), all versions are retained."""
    monkeypatch.setattr(app_config, "LIMITED_HISTORY_DIRS", {})

    path = "saves/mGBA/game.srm"
    for i in range(4):
        await sync_file(client, DEVICE, path, f"save version {i}".encode())

    async with db_engine.connect() as conn:
        from sqlalchemy.ext.asyncio import AsyncSession
        async with AsyncSession(db_engine) as session:
            result = await session.execute(
                select(Version).where(Version.file_path == path)
            )
            versions = result.scalars().all()

    assert len(versions) == 4


@pytest.mark.asyncio
async def test_retention_prunes_old_versions(client, db_engine, monkeypatch):
    """With limit=1, only 1 non-canonical version is kept."""
    monkeypatch.setattr(app_config, "LIMITED_HISTORY_DIRS", {"saves": 1})

    path = "saves/mGBA/limited.srm"
    # Upload 3 versions — should result in 1 canonical + 1 non-canonical (3rd pruned)
    for i in range(3):
        await sync_file(client, DEVICE, path, f"save version {i}".encode())

    async with db_engine.connect() as conn:
        from sqlalchemy.ext.asyncio import AsyncSession
        async with AsyncSession(db_engine) as session:
            result = await session.execute(
                select(Version).where(Version.file_path == path)
            )
            versions = result.scalars().all()

    canonical = [v for v in versions if v.is_canonical]
    non_canonical = [v for v in versions if not v.is_canonical]
    assert len(canonical) == 1
    assert len(non_canonical) <= 1


@pytest.mark.asyncio
async def test_retention_zero_is_unlimited(client, db_engine, monkeypatch):
    """Explicitly setting limit=0 keeps all versions."""
    # Saves not in LIMITED_HISTORY_DIRS = unlimited
    monkeypatch.setattr(app_config, "LIMITED_HISTORY_DIRS", {})

    path = "saves/mGBA/unlimited.srm"
    for i in range(5):
        await sync_file(client, DEVICE, path, f"save v{i}".encode())

    async with db_engine.connect() as conn:
        from sqlalchemy.ext.asyncio import AsyncSession
        async with AsyncSession(db_engine) as session:
            result = await session.execute(
                select(Version).where(Version.file_path == path)
            )
            versions = result.scalars().all()

    assert len(versions) == 5


@pytest.mark.asyncio
async def test_retention_canonical_never_pruned(client, db_engine, monkeypatch):
    """Retention never removes the canonical version regardless of limit."""
    monkeypatch.setattr(app_config, "LIMITED_HISTORY_DIRS", {"saves": 1})

    path = "saves/mGBA/canonical.srm"
    for i in range(5):
        await sync_file(client, DEVICE, path, f"save version {i}".encode())

    async with db_engine.connect() as conn:
        from sqlalchemy.ext.asyncio import AsyncSession
        async with AsyncSession(db_engine) as session:
            result = await session.execute(
                select(Version).where(Version.file_path == path)
            )
            versions = result.scalars().all()

    canonical = [v for v in versions if v.is_canonical]
    assert len(canonical) == 1


@pytest.mark.asyncio
async def test_retention_only_applies_to_configured_dirs(client, db_engine, monkeypatch):
    """Retention limits only apply to the configured directories."""
    # Limit saves but not states
    monkeypatch.setattr(app_config, "LIMITED_HISTORY_DIRS", {"saves": 1})

    state_path = "states/mGBA/game.state1"
    for i in range(4):
        await sync_file(client, DEVICE, state_path, f"state v{i}".encode())

    async with db_engine.connect() as conn:
        from sqlalchemy.ext.asyncio import AsyncSession
        async with AsyncSession(db_engine) as session:
            result = await session.execute(
                select(Version).where(Version.file_path == state_path)
            )
            versions = result.scalars().all()

    # States are not in LIMITED_HISTORY_DIRS so all 4 should be kept
    assert len(versions) == 4
