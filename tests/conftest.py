"""Shared pytest fixtures with per-test filesystem isolation."""

import os
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Set DATA_DIR before importing app modules so the initial config is valid
_global_tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = _global_tmp

# Import as modules (not `from app.main import app`) to avoid name shadowing
import app.config as app_config  # noqa: E402
import app.store as app_store  # noqa: E402
import app.sync.engine as app_engine  # noqa: E402
import app.webdav.router as app_webdav  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402


def _patch_paths(monkeypatch, tmp_path: Path) -> None:
    """Redirect all file-system paths in the app to tmp_path for this test."""
    store_dir = tmp_path / "store"
    manifests_dir = tmp_path / "manifests"
    snapshots_dir = manifests_dir / "snapshots"
    devices_dir = tmp_path / "devices"
    conflicts_dir = tmp_path / "conflicts"
    canonical = manifests_dir / "canonical.json"

    for d in (store_dir, snapshots_dir, devices_dir, conflicts_dir):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_config, "STORE_DIR", store_dir)
    monkeypatch.setattr(app_config, "MANIFESTS_DIR", manifests_dir)
    monkeypatch.setattr(app_config, "SNAPSHOTS_DIR", snapshots_dir)
    monkeypatch.setattr(app_config, "DEVICES_DIR", devices_dir)
    monkeypatch.setattr(app_config, "CONFLICTS_DIR", conflicts_dir)
    monkeypatch.setattr(app_config, "CANONICAL_MANIFEST", canonical)
    # app.store and app.sync.engine reference app.config attributes at call time,
    # so patching app_config above propagates automatically.


@pytest_asyncio.fixture
async def db_engine(tmp_path):
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_engine, tmp_path, monkeypatch):
    """AsyncClient with isolated DB and filesystem per test."""
    _patch_paths(monkeypatch, tmp_path)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    fastapi_app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as ac:
        yield ac

    fastapi_app.dependency_overrides.clear()
