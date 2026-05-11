"""Tests for content-addressable file store."""

import hashlib

import pytest

from app.store import blob_path, compute_md5, read_blob, store_blob


@pytest.mark.asyncio
async def test_store_and_read():
    data = b"hello world"
    expected_hash = hashlib.md5(data).hexdigest()
    hash_, size = await store_blob(data)
    assert hash_ == expected_hash
    assert size == len(data)
    result = await read_blob(hash_)
    assert result == data


@pytest.mark.asyncio
async def test_store_idempotent():
    data = b"same content"
    hash1, _ = await store_blob(data)
    hash2, _ = await store_blob(data)
    assert hash1 == hash2
    result = await read_blob(hash1)
    assert result == data


@pytest.mark.asyncio
async def test_read_missing():
    result = await read_blob("0" * 32)
    assert result is None


def test_compute_md5():
    data = b"test"
    assert compute_md5(data) == hashlib.md5(data).hexdigest()


def test_blob_path_structure():
    hash_ = "abcdef1234567890" * 2
    path = blob_path(hash_)
    assert path.parent.name == "ab"
    assert path.name == f"{hash_}.bin"
