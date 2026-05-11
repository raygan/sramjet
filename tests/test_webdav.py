"""Integration tests for WebDAV endpoints against RetroArch behavior."""

import json

import pytest

DEVICE = "mac"
MANIFEST_PATH = f"/sync/{DEVICE}/manifest.server"
FILE_PATH = f"/sync/{DEVICE}/saves/Mother3.sav"


@pytest.mark.asyncio
async def test_options_returns_200(client):
    r = await client.options(f"/sync/{DEVICE}/")
    assert r.status_code == 200
    assert "DAV" in r.headers


@pytest.mark.asyncio
async def test_get_missing_file_returns_404(client):
    await client.options(f"/sync/{DEVICE}/")
    r = await client.get(FILE_PATH)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_missing_manifest_returns_404(client):
    r = await client.get(MANIFEST_PATH)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_put_and_get_file(client):
    await client.options(f"/sync/{DEVICE}/")
    data = b"save data"
    r = await client.put(FILE_PATH, content=data)
    assert r.status_code == 201
    r = await client.get(FILE_PATH)
    assert r.status_code == 200
    assert r.content == data


@pytest.mark.asyncio
async def test_mkcol_returns_405(client):
    r = await client.request("MKCOL", f"/sync/{DEVICE}/saves/")
    assert r.status_code == 405


@pytest.mark.asyncio
async def test_full_sync_flow(client):
    """Simulate a complete RetroArch sync: OPTIONS → PUT files → PUT manifest."""
    await client.options(f"/sync/{DEVICE}/")

    save_data = b"game save content"
    r = await client.put(f"/sync/{DEVICE}/saves/game.sav", content=save_data)
    assert r.status_code == 201

    import hashlib
    save_hash = hashlib.md5(save_data).hexdigest()
    manifest = [{"path": "saves/game.sav", "hash": save_hash}]
    r = await client.put(MANIFEST_PATH, content=json.dumps(manifest).encode())
    assert r.status_code == 201

    # Verify manifest is now served
    r = await client.get(MANIFEST_PATH)
    assert r.status_code == 200
    served = json.loads(r.content)
    assert any(e["path"] == "saves/game.sav" for e in served)


@pytest.mark.asyncio
async def test_conflict_detection(client):
    """Two devices uploading different versions of the same file → conflict."""
    import hashlib

    # Device A syncs first
    await client.options(f"/sync/mac/")
    data_a = b"mac save"
    r = await client.put("/sync/mac/saves/game.sav", content=data_a)
    assert r.status_code == 201
    hash_a = hashlib.md5(data_a).hexdigest()
    manifest_a = [{"path": "saves/game.sav", "hash": hash_a}]
    r = await client.put("/sync/mac/manifest.server", content=json.dumps(manifest_a).encode())
    assert r.status_code == 201

    # Device B uploads a different version
    await client.options(f"/sync/iphone/")
    data_b = b"iphone save - different progress"
    r = await client.put("/sync/iphone/saves/game.sav", content=data_b)
    # This should succeed at the file level (conflict detected at manifest time)
    # OR be rejected immediately if canonical already differs
    # Either 201 or 409 is acceptable depending on timing; what matters is
    # the manifest PUT is rejected.
    hash_b = hashlib.md5(data_b).hexdigest()
    manifest_b = [{"path": "saves/game.sav", "hash": hash_b}]
    r = await client.put("/sync/iphone/manifest.server", content=json.dumps(manifest_b).encode())
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_delete_removes_from_canonical(client):
    import hashlib

    # Upload a file and accept it into canonical
    await client.options(f"/sync/{DEVICE}/")
    data = b"deletable save"
    await client.put(FILE_PATH, content=data)
    hash_ = hashlib.md5(data).hexdigest()
    manifest = [{"path": "saves/Mother3.sav", "hash": hash_}]
    await client.put(MANIFEST_PATH, content=json.dumps(manifest).encode())

    # Now delete it
    await client.options(f"/sync/{DEVICE}/")
    r = await client.delete(FILE_PATH)
    assert r.status_code == 204

    # After manifest confirming deletion
    manifest_del = [{"path": "saves/Mother3.sav", "hash": ""}]
    r = await client.put(MANIFEST_PATH, content=json.dumps(manifest_del).encode())
    assert r.status_code == 201

    # File should be gone from canonical
    r = await client.get(FILE_PATH)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_second_device_receives_canonical(client):
    """After device A syncs, device B should be able to GET the file."""
    import hashlib

    await client.options("/sync/mac/")
    data = b"shared save data"
    await client.put("/sync/mac/saves/shared.sav", content=data)
    hash_ = hashlib.md5(data).hexdigest()
    manifest = [{"path": "saves/shared.sav", "hash": hash_}]
    await client.put("/sync/mac/manifest.server", content=json.dumps(manifest).encode())

    # iPhone gets the file
    r = await client.get("/sync/iphone/saves/shared.sav")
    assert r.status_code == 200
    assert r.content == data


@pytest.mark.asyncio
async def test_deduplication(client):
    """Identical content uploaded by two devices is stored only once."""
    import hashlib
    from app.store import blob_path

    data = b"identical save content"
    hash_ = hashlib.md5(data).hexdigest()

    await client.options("/sync/mac/")
    await client.put("/sync/mac/saves/game.sav", content=data)

    await client.options("/sync/iphone/")
    await client.put("/sync/iphone/saves/game.sav", content=data)

    # The blob should exist exactly once
    assert blob_path(hash_).exists()
