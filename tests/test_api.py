"""Tests for the internal REST API."""

import json

import pytest


@pytest.mark.asyncio
async def test_devices_empty(client):
    r = await client.get("/api/devices")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_device_auto_registered(client):
    await client.options("/sync/mac/")
    r = await client.get("/api/devices")
    assert r.status_code == 200
    devices = r.json()
    assert any(d["name"] == "mac" for d in devices)


@pytest.mark.asyncio
async def test_rename_device(client):
    await client.options("/sync/mac/")
    r = await client.patch("/api/devices/mac", json={"display_name": "My Mac"})
    assert r.status_code == 200
    assert r.json()["display_name"] == "My Mac"


@pytest.mark.asyncio
async def test_conflicts_empty(client):
    r = await client.get("/api/conflicts")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_conflict_resolution(client):
    import hashlib

    # Create a conflict
    await client.options("/sync/mac/")
    data_a = b"mac progress"
    await client.put("/sync/mac/saves/game.sav", content=data_a)
    hash_a = hashlib.md5(data_a).hexdigest()
    await client.put("/sync/mac/manifest.server", content=json.dumps([{"path": "saves/game.sav", "hash": hash_a}]).encode())

    await client.options("/sync/iphone/")
    data_b = b"iphone progress"
    await client.put("/sync/iphone/saves/game.sav", content=data_b)
    hash_b = hashlib.md5(data_b).hexdigest()
    await client.put("/sync/iphone/manifest.server", content=json.dumps([{"path": "saves/game.sav", "hash": hash_b}]).encode())

    # There should be a conflict
    r = await client.get("/api/conflicts")
    conflicts = r.json()
    assert len(conflicts) == 1
    conflict_id = conflicts[0]["id"]

    # Resolve in favor of mac
    r = await client.post(f"/api/conflicts/{conflict_id}/resolve", json={"winning_hash": hash_a})
    assert r.status_code == 204

    # Conflict should be gone
    r = await client.get("/api/conflicts")
    assert r.json() == []


@pytest.mark.asyncio
async def test_timeline(client):
    await client.options("/sync/mac/")
    r = await client.get("/api/timeline")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_snapshots_after_sync(client):
    import hashlib

    await client.options("/sync/mac/")
    data = b"save data"
    await client.put("/sync/mac/saves/game.sav", content=data)
    hash_ = hashlib.md5(data).hexdigest()
    await client.put("/sync/mac/manifest.server", content=json.dumps([{"path": "saves/game.sav", "hash": hash_}]).encode())

    r = await client.get("/api/snapshots")
    assert r.status_code == 200
    snapshots = r.json()
    assert len(snapshots) >= 1
    assert snapshots[0]["file_count"] == 1
