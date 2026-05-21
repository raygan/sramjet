import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    import sqlite3
    import logging
    from pathlib import Path
    from alembic import command
    from alembic.config import Config

    log = logging.getLogger(__name__)
    alembic_cfg = Config("alembic.ini")

    db_path = Path(DATABASE_URL.replace("sqlite+aiosqlite:///", ""))
    log.info("DB init — url=%s path=%s exists=%s", DATABASE_URL, db_path, db_path.exists())

    has_devices = False
    has_alembic_version = False
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path)) as conn:
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
            log.info("DB tables found: %s", tables)
            has_devices = "devices" in tables
            has_alembic_version = "alembic_version" in tables
        except Exception as e:
            log.warning("DB table detection failed: %s", e)

    log.info("has_devices=%s has_alembic_version=%s", has_devices, has_alembic_version)

    if has_devices and not has_alembic_version:
        log.info("Legacy install detected — stamping to head")
        await asyncio.to_thread(command.stamp, alembic_cfg, "head")

    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
