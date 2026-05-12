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
