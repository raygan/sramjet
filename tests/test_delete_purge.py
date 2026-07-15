"""Tests for manual tombstone deletion and permanent purge (empty trash)."""

import hashlib
import json

import pytest

from app.store import blob_path


DEVICE = "mac"
SAVE_PATH = "saves/mGBA/Mother 3.srm"
STATE_PATH = "states/mGBA/Mother 3.state1"
SAVE_DATA = b"test save content"
STATE_DATA = b"test state content"
SAVE_HASH = hashlib.md5(SAVE_DATA).hexdigest()
STATE_HASH = hashlib.md5(STATE_DATA).hexdigest()


async def seed_sync(client, device: str = DEVICE) -> None:
    await client.options(f"/sync/{device}/")
    await client.put(f"/sync/{device}/{SAVE_PATH}", content=SAVE_DATA)
    await client.put(f"/sync/{device}/{STATE_PATH}", content=STATE_DATA)
    manifest = [
        {"path": SAVE_PATH, "hash": SAVE_HASH},
        {"path": STATE_PATH, "hash": STATE_HASH},
    ]
    await client.put(f"/sync/{device}/manifest.server", content=json.dumps(manifest).encode())


async def get_manifest_dict(client, device: str = DEVICE) -> dict[str, str]:
    r = await client.get(f"/sync/{device}/manifest.server")
    assert r.status_code == 200
    return {e["path"]: e["hash"] for e in json.loads(r.content)}


# ─── Tombstone delete ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_file_writes_tombstone(client):
    await seed_sync(client)
    r = await client.post("/files/delete", data={"path": SAVE_PATH})
    assert r.status_code == 303

    manifest = await get_manifest_dict(client)
    assert manifest[SAVE_PATH] == ""
    assert manifest[STATE_PATH] == STATE_HASH

    # No version should remain canonical
    history = (await client.get(f"/api/files/{SAVE_PATH}/history")).json()
    assert history
    assert not any(v["is_canonical"] for v in history)


@pytest.mark.asyncio
async def test_delete_folder_tombstones_all_under_prefix(client):
    await seed_sync(client)
    r = await client.post("/files/delete-folder", data={"prefix": "saves"})
    assert r.status_code == 303

    manifest = await get_manifest_dict(client)
    assert manifest[SAVE_PATH] == ""
    assert manifest[STATE_PATH] == STATE_HASH


@pytest.mark.asyncio
async def test_delete_subfolder_prefix(client):
    await seed_sync(client)
    await client.post("/files/delete-folder", data={"prefix": "saves/mGBA"})
    manifest = await get_manifest_dict(client)
    assert manifest[SAVE_PATH] == ""


@pytest.mark.asyncio
async def test_delete_nonexistent_path_is_noop(client):
    await seed_sync(client)
    r = await client.post("/files/delete", data={"path": "saves/nope.srm"})
    assert r.status_code == 303
    manifest = await get_manifest_dict(client)
    assert "saves/nope.srm" not in manifest


@pytest.mark.asyncio
async def test_files_page_shows_empty_trash_button(client):
    await seed_sync(client)
    r = await client.get("/files")
    assert b"Empty Trash" not in r.content

    await client.post("/files/delete", data={"path": SAVE_PATH})
    r = await client.get("/files")
    assert b"Empty Trash (1)" in r.content


# ─── Empty trash / purge ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_trash_purges_deleted_files(client):
    await seed_sync(client)
    await client.post("/files/delete", data={"path": SAVE_PATH})
    assert blob_path(SAVE_HASH).exists()

    r = await client.post("/files/empty-trash")
    assert r.status_code == 303

    manifest = await get_manifest_dict(client)
    assert SAVE_PATH not in manifest
    assert manifest[STATE_PATH] == STATE_HASH

    # History gone, blob unlinked; untouched file keeps its blob
    assert (await client.get(f"/files/{SAVE_PATH}")).status_code == 404
    assert (await client.get(f"/api/files/{SAVE_PATH}/history")).json() == []
    assert not blob_path(SAVE_HASH).exists()
    assert blob_path(STATE_HASH).exists()


@pytest.mark.asyncio
async def test_purge_single_file(client):
    await seed_sync(client)
    await client.post("/files/delete", data={"path": SAVE_PATH})
    r = await client.post("/files/purge", data={"path": SAVE_PATH})
    assert r.status_code == 303

    manifest = await get_manifest_dict(client)
    assert SAVE_PATH not in manifest
    assert not blob_path(SAVE_HASH).exists()


@pytest.mark.asyncio
async def test_empty_trash_skips_pinned_versions(client):
    await seed_sync(client)
    history = (await client.get(f"/api/files/{SAVE_PATH}/history")).json()
    version_id = history[0]["id"]
    await client.post(f"/files/{SAVE_PATH}/pin/{version_id}")

    await client.post("/files/delete", data={"path": SAVE_PATH})
    await client.post("/files/empty-trash")

    # Pinned path is skipped: tombstone stays, history and blob survive
    manifest = await get_manifest_dict(client)
    assert manifest[SAVE_PATH] == ""
    assert (await client.get(f"/api/files/{SAVE_PATH}/history")).json()
    assert blob_path(SAVE_HASH).exists()


@pytest.mark.asyncio
async def test_purge_preserves_blob_shared_with_other_path(client):
    other_path = "saves/mGBA/Mother 3 copy.srm"
    await seed_sync(client)
    # Upload identical content under a second path
    await client.options(f"/sync/{DEVICE}/")
    await client.put(f"/sync/{DEVICE}/{other_path}", content=SAVE_DATA)
    manifest = [
        {"path": SAVE_PATH, "hash": SAVE_HASH},
        {"path": other_path, "hash": SAVE_HASH},
        {"path": STATE_PATH, "hash": STATE_HASH},
    ]
    await client.put(f"/sync/{DEVICE}/manifest.server", content=json.dumps(manifest).encode())

    await client.post("/files/delete", data={"path": SAVE_PATH})
    await client.post("/files/empty-trash")

    # Blob still referenced by other_path — must survive
    assert blob_path(SAVE_HASH).exists()
    served = await get_manifest_dict(client)
    assert served[other_path] == SAVE_HASH


@pytest.mark.asyncio
async def test_deleted_file_reupload_after_purge(client):
    """After purge, a client re-uploading the file is treated as a fresh creation."""
    await seed_sync(client)
    await client.post("/files/delete", data={"path": SAVE_PATH})
    await client.post("/files/empty-trash")

    await seed_sync(client)
    manifest = await get_manifest_dict(client)
    assert manifest[SAVE_PATH] == SAVE_HASH
