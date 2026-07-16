"""Tests for the MiSTer sync endpoints."""

import hashlib
import json

import pytest

from app.mister.convert import _genesis_byte_expand

DEVICE = "mister-living-room"
RA_DEVICE = "mac"

SNES_DATA = b"snes save content" * 100
SNES_HASH = hashlib.md5(SNES_DATA).hexdigest()


async def seed_ra_sync(client, path: str, data: bytes, device: str = RA_DEVICE) -> str:
    """Upload one file through the RetroArch WebDAV flow. Returns its hash."""
    file_hash = hashlib.md5(data).hexdigest()
    await client.options(f"/sync/{device}/")
    await client.put(f"/sync/{device}/{path}", content=data)
    manifest = [{"path": path, "hash": file_hash}]
    await client.put(f"/sync/{device}/manifest.server", content=json.dumps(manifest).encode())
    return file_hash


async def get_mister_manifest(client) -> dict[str, dict]:
    r = await client.get(f"/mister/{DEVICE}/manifest")
    assert r.status_code == 200
    return {e["path"]: e for e in json.loads(r.content)}


# ─── Manifest view ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manifest_translates_paths(client):
    await seed_ra_sync(client, "saves/Snes9x/Mother 2.srm", SNES_DATA)
    manifest = await get_mister_manifest(client)
    assert manifest["SNES/Mother 2.sav"]["hash"] == SNES_HASH
    assert manifest["SNES/Mother 2.sav"]["mtime"] > 0


@pytest.mark.asyncio
async def test_manifest_expands_gb_to_sibling_dirs(client):
    h = await seed_ra_sync(client, "saves/Gambatte/Link's Awakening.srm", b"gb data" * 512)
    manifest = await get_mister_manifest(client)
    for d in ("GAMEBOY", "GBC", "SGB"):
        assert manifest[f"{d}/Link's Awakening.sav"]["hash"] == h


@pytest.mark.asyncio
async def test_manifest_skips_unmapped_paths(client):
    await seed_ra_sync(client, "states/mGBA/Game.state1", b"state data")
    await seed_ra_sync(client, "saves/SomeUnknownCore/Game.srm", b"save data")
    manifest = await get_mister_manifest(client)
    assert manifest == {}


# ─── Download ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_identity_system(client):
    await seed_ra_sync(client, "saves/Snes9x/Mother 2.srm", SNES_DATA)
    r = await client.get(f"/mister/{DEVICE}/files/SNES/Mother 2.sav")
    assert r.status_code == 200
    assert r.content == SNES_DATA
    assert r.headers["x-canonical-hash"] == SNES_HASH


@pytest.mark.asyncio
async def test_download_genesis_collapses_and_pads(client):
    plain = bytes([0x10, 0x20, 0x30, 0x40]) * 2048
    expanded = _genesis_byte_expand(plain)
    await seed_ra_sync(client, "saves/Genesis Plus GX/Sonic.srm", expanded)
    r = await client.get(f"/mister/{DEVICE}/files/Genesis/Sonic.sav")
    assert r.status_code == 200
    assert len(r.content) == 65536
    assert r.content[: len(plain)] == plain
    assert set(r.content[len(plain):]) == {0xFF}


@pytest.mark.asyncio
async def test_download_unmapped_or_missing_404(client):
    assert (await client.get(f"/mister/{DEVICE}/files/Unknown/x.sav")).status_code == 404
    assert (await client.get(f"/mister/{DEVICE}/files/SNES/nope.sav")).status_code == 404


# ─── Upload ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_updates_canonical(client):
    r = await client.put(f"/mister/{DEVICE}/files/SNES/New Game.sav", content=SNES_DATA)
    assert r.status_code == 201
    assert json.loads(r.content)["canonical_hash"] == SNES_HASH
    await client.post(f"/mister/{DEVICE}/complete")

    # Visible to RetroArch devices under the mapped core path
    r = await client.get(f"/sync/{RA_DEVICE}/saves/Snes9x/New Game.srm")
    assert r.status_code == 200
    assert r.content == SNES_DATA


@pytest.mark.asyncio
async def test_upload_gba_strips_rtc_tail(client):
    save = bytes([0x22]) * 32768
    with_rtc = save + bytes([0x33]) * 68
    r = await client.put(f"/mister/{DEVICE}/files/GBA/Mother 3.sav", content=with_rtc)
    assert json.loads(r.content)["canonical_hash"] == hashlib.md5(save).hexdigest()

    r = await client.get(f"/sync/{RA_DEVICE}/saves/mGBA/Mother 3.srm")
    assert r.content == save


@pytest.mark.asyncio
async def test_upload_unmapped_404(client):
    r = await client.put(f"/mister/{DEVICE}/files/Unknown/x.sav", content=b"data")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_sibling_dir_uploads_map_to_same_canonical(client):
    data = b"gb progress" * 100
    r = await client.put(f"/mister/{DEVICE}/files/GBC/Game.sav", content=data)
    h = json.loads(r.content)["canonical_hash"]
    manifest = await get_mister_manifest(client)
    assert manifest["GAMEBOY/Game.sav"]["hash"] == h
    assert manifest["GBC/Game.sav"]["hash"] == h


# ─── Delete ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_tombstones_canonical(client):
    await seed_ra_sync(client, "saves/Snes9x/Mother 2.srm", SNES_DATA)
    r = await client.delete(f"/mister/{DEVICE}/files/SNES/Mother 2.sav")
    assert r.status_code == 204
    await client.post(f"/mister/{DEVICE}/complete")

    manifest = await get_mister_manifest(client)
    assert manifest["SNES/Mother 2.sav"]["hash"] == ""


# ─── Device integration ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mister_device_appears_in_dashboard(client):
    await client.put(f"/mister/{DEVICE}/files/SNES/Game.sav", content=SNES_DATA)
    await client.post(f"/mister/{DEVICE}/complete")
    r = await client.get("/devices")
    assert r.status_code == 200
    assert DEVICE.encode() in r.content


# ─── Client script download ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_download_substitutes_placeholders(client):
    r = await client.get("/mister-client?device=basement-mister")
    assert r.status_code == 200
    text = r.content.decode()
    assert "{{SERVER_URL}}" not in text
    assert "{{DEVICE_NAME}}" not in text
    assert "{{MISTER_DIRS}}" not in text
    assert 'DEVICE_NAME = "basement-mister"' in text
    compile(text, "sramjet_sync.py", "exec")  # must be valid Python
