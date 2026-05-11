"""Content-addressable file store.

Files are stored at: STORE_DIR/{hash[:2]}/{hash}.bin
Identical content is stored exactly once.
"""

import hashlib
from pathlib import Path

import aiofiles
import aiofiles.os

import app.config


def blob_path(hash: str) -> Path:
    return app.config.STORE_DIR / hash[:2] / f"{hash}.bin"


async def store_blob(data: bytes) -> tuple[str, int]:
    """Write data to the store. Returns (md5_hex, size). Idempotent."""
    md5 = hashlib.md5(data).hexdigest()
    dest = blob_path(md5)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(dest, "wb") as f:
            await f.write(data)
    return md5, len(data)


async def read_blob(hash: str) -> bytes | None:
    path = blob_path(hash)
    if not path.exists():
        return None
    async with aiofiles.open(path, "rb") as f:
        return await f.read()


def blob_exists(hash: str) -> bool:
    return blob_path(hash).exists()


def compute_md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()
