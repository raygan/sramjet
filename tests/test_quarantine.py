"""Integration tests for the per-device quarantine feature."""

import hashlib
import json

import pytest

DEVICE_A = "iphone"    # quarantined device
DEVICE_B = "ipad"      # normal device


def manifest_for(path: str, data: bytes) -> bytes:
    return json.dumps([{"path": path, "hash": hashlib.md5(data).hexdigest()}]).encode()


async def full_sync(client, device: str, path: str, data: bytes) -> None:
    """Upload a single file and commit the manifest."""
    await client.options(f"/sync/{device}/")
    await client.put(f"/sync/{device}/{path}", content=data)
    await client.put(f"/sync/{device}/manifest.server", content=manifest_for(path, data))


# ─── Quarantine settings ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_quarantine_defaults_off(client):
    """A newly registered device has no quarantine."""
    await client.options(f"/sync/{DEVICE_A}/")
    r = await client.get("/api/devices")
    device = next(d for d in r.json() if d["name"] == DEVICE_A)
    assert device["quarantine_saves"] is False
    assert device["quarantine_states"] is False


@pytest.mark.asyncio
async def test_set_quarantine_via_dashboard(client):
    """POSTing to the quarantine endpoint updates the device in DB."""
    await client.options(f"/sync/{DEVICE_A}/")
    r = await client.post(
        f"/devices/{DEVICE_A}/quarantine",
        data={"states": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r = await client.get("/api/devices")
    device = next(d for d in r.json() if d["name"] == DEVICE_A)
    assert device["quarantine_states"] is True
    assert device["quarantine_saves"] is False


# ─── Quarantined uploads stay out of main canonical ───────────────────────────

@pytest.mark.asyncio
async def test_quarantined_state_invisible_to_other_device(client):
    """States uploaded by a quarantined device should not appear in the main manifest."""
    state_path = "states/mGBA/Mother 3.state1"
    state_data = b"quarantined state data"

    # Quarantine iphone's states
    await client.options(f"/sync/{DEVICE_A}/")
    await client.post(
        f"/devices/{DEVICE_A}/quarantine",
        data={"states": "on"},
        follow_redirects=False,
    )

    # iphone uploads a state
    await client.options(f"/sync/{DEVICE_A}/")
    await client.put(f"/sync/{DEVICE_A}/{state_path}", content=state_data)
    await client.put(
        f"/sync/{DEVICE_A}/manifest.server",
        content=manifest_for(state_path, state_data),
    )

    # ipad should NOT see it
    r = await client.get(f"/sync/{DEVICE_B}/manifest.server")
    if r.status_code == 200:
        manifest = json.loads(r.content)
        paths = [e["path"] for e in manifest]
        assert state_path not in paths


@pytest.mark.asyncio
async def test_quarantined_state_accessible_to_quarantined_device(client):
    """The quarantined device should receive its own states in its manifest."""
    state_path = "states/mGBA/Mother 3.state1"
    state_data = b"my quarantined state"

    await client.options(f"/sync/{DEVICE_A}/")
    await client.post(
        f"/devices/{DEVICE_A}/quarantine",
        data={"states": "on"},
        follow_redirects=False,
    )

    await client.options(f"/sync/{DEVICE_A}/")
    await client.put(f"/sync/{DEVICE_A}/{state_path}", content=state_data)
    await client.put(
        f"/sync/{DEVICE_A}/manifest.server",
        content=manifest_for(state_path, state_data),
    )

    # iphone should see it in its hybrid manifest
    r = await client.get(f"/sync/{DEVICE_A}/manifest.server")
    assert r.status_code == 200
    manifest = json.loads(r.content)
    paths = [e["path"] for e in manifest]
    assert state_path in paths


@pytest.mark.asyncio
async def test_non_quarantined_files_still_shared(client):
    """Quarantining states should not affect saves — they still go to main canonical."""
    save_path = "saves/mGBA/Mother 3.srm"
    save_data = b"shared save data"

    await client.options(f"/sync/{DEVICE_A}/")
    await client.post(
        f"/devices/{DEVICE_A}/quarantine",
        data={"states": "on"},
        follow_redirects=False,
    )

    # Upload a save (not quarantined)
    await full_sync(client, DEVICE_A, save_path, save_data)

    # ipad should see the save
    r = await client.get(f"/sync/{DEVICE_B}/{save_path}")
    assert r.status_code == 200
    assert r.content == save_data


@pytest.mark.asyncio
async def test_hybrid_manifest_excludes_main_states(client):
    """Quarantined device's manifest should NOT include states uploaded by other devices."""
    state_path = "states/mGBA/Shared Game.state1"
    state_data = b"ipad state"

    # ipad uploads a state to main canonical
    await full_sync(client, DEVICE_B, state_path, state_data)

    # Quarantine iphone's states AFTER ipad already has a state in canonical
    await client.options(f"/sync/{DEVICE_A}/")
    await client.post(
        f"/devices/{DEVICE_A}/quarantine",
        data={"states": "on"},
        follow_redirects=False,
    )

    # iphone's manifest should not include ipad's state.
    # The hybrid manifest strips quarantined types from main canonical; since iphone
    # has no quarantine canonical states of its own, the manifest is empty → 404.
    r = await client.get(f"/sync/{DEVICE_A}/manifest.server")
    if r.status_code == 200:
        manifest = json.loads(r.content)
        paths = [e["path"] for e in manifest]
        assert state_path not in paths
    else:
        assert r.status_code == 404  # empty manifest after stripping
