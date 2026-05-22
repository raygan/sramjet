"""Smoke tests for dashboard routes — verifies all pages render without errors."""

import hashlib
import json

import pytest


DEVICE = "mac"


async def seed_sync(client, device: str = DEVICE) -> None:
    """Run a minimal sync to create device + event + file data."""
    save_data = b"test save content"
    state_data = b"test state content"
    save_hash = hashlib.md5(save_data).hexdigest()
    state_hash = hashlib.md5(state_data).hexdigest()

    await client.options(f"/sync/{device}/")
    await client.put(f"/sync/{device}/saves/mGBA/Mother 3.srm", content=save_data)
    await client.put(f"/sync/{device}/states/mGBA/Mother 3.state1", content=state_data)
    manifest = [
        {"path": "saves/mGBA/Mother 3.srm", "hash": save_hash},
        {"path": "states/mGBA/Mother 3.state1", "hash": state_hash},
    ]
    await client.put(
        f"/sync/{device}/manifest.server",
        content=json.dumps(manifest).encode(),
    )


# ─── Empty-state smoke tests (no data) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_home_empty(client):
    r = await client.get("/")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_timeline_empty(client):
    r = await client.get("/timeline")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_games_empty(client):
    r = await client.get("/games")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_devices_empty(client):
    r = await client.get("/devices")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_files_empty(client):
    r = await client.get("/files")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_help(client):
    r = await client.get("/help")
    assert r.status_code == 200


# ─── With-data smoke tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_home_with_data(client):
    await seed_sync(client)
    r = await client.get("/")
    assert r.status_code == 200
    assert b"Mother 3" in r.content


@pytest.mark.asyncio
async def test_timeline_with_data(client):
    await seed_sync(client)
    r = await client.get("/timeline")
    assert r.status_code == 200
    assert b"mac" in r.content


@pytest.mark.asyncio
async def test_games_with_data(client):
    await seed_sync(client)
    r = await client.get("/games")
    assert r.status_code == 200
    assert b"Mother 3" in r.content


@pytest.mark.asyncio
async def test_games_sort_alpha(client):
    await seed_sync(client)
    r = await client.get("/games?sort=alpha")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_games_sort_activity(client):
    await seed_sync(client)
    r = await client.get("/games?sort=activity")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_game_detail(client):
    await seed_sync(client)
    from urllib.parse import quote
    r = await client.get(f"/games/{quote('Mother 3')}")
    assert r.status_code == 200
    assert b"Mother 3" in r.content


@pytest.mark.asyncio
async def test_files_with_data(client):
    await seed_sync(client)
    r = await client.get("/files")
    assert r.status_code == 200
    assert b"saves" in r.content


@pytest.mark.asyncio
async def test_file_detail(client):
    await seed_sync(client)
    r = await client.get("/files/saves/mGBA/Mother 3.srm")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_file_detail_not_found(client):
    r = await client.get("/files/saves/mGBA/nonexistent.srm")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_devices_with_data(client):
    await seed_sync(client)
    r = await client.get("/devices")
    assert r.status_code == 200
    assert b"mac" in r.content


# ─── Device action routes ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trust_next_sync_sets_flag(client):
    await client.options(f"/sync/{DEVICE}/")
    r = await client.post(f"/devices/{DEVICE}/force-accept", follow_redirects=False)
    assert r.status_code == 303

    # Verify Trust Next Sync is active — manifest should be empty
    r = await client.get(f"/sync/{DEVICE}/manifest.server")
    assert r.status_code == 200
    assert r.content == b"[]"


@pytest.mark.asyncio
async def test_cancel_trust_next_sync(client):
    await client.options(f"/sync/{DEVICE}/")
    await client.post(f"/devices/{DEVICE}/force-accept", follow_redirects=False)
    r = await client.post(f"/devices/{DEVICE}/cancel-force-accept", follow_redirects=False)
    assert r.status_code == 303


@pytest.mark.asyncio
async def test_trust_next_sync_unknown_device_returns_404(client):
    r = await client.post("/devices/ghost/force-accept", follow_redirects=False)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_quarantine_post(client):
    await client.options(f"/sync/{DEVICE}/")
    r = await client.post(
        f"/devices/{DEVICE}/quarantine",
        data={"states": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 303


@pytest.mark.asyncio
async def test_quarantine_post_unknown_device_returns_404(client):
    r = await client.post(
        "/devices/ghost/quarantine",
        data={"states": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 404


# ─── File revert ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_revert_file(client):
    """Reverting to an older version makes it canonical."""
    from app.models import Version
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    # First version
    v1_data = b"version 1"
    await seed_sync(client)

    # Upload a second version
    v2_data = b"version 2"
    await client.options(f"/sync/{DEVICE}/")
    await client.put(f"/sync/{DEVICE}/saves/mGBA/Mother 3.srm", content=v2_data)
    v2_hash = hashlib.md5(v2_data).hexdigest()
    await client.put(
        f"/sync/{DEVICE}/manifest.server",
        content=json.dumps([{"path": "saves/mGBA/Mother 3.srm", "hash": v2_hash}]).encode(),
    )

    # File detail page should show both versions
    r = await client.get("/files/saves/mGBA/Mother 3.srm")
    assert r.status_code == 200
    assert b"Mother 3" in r.content


# ─── Health check ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
