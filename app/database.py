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
    from pathlib import Path
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")

    # Use plain sqlite3 to detect legacy installs — no asyncio complexity,
    # no risk of event loop conflicts with Alembic's sync engine.
    db_path = Path(DATABASE_URL.replace("sqlite+aiosqlite:///", ""))
    has_devices = False
    has_alembic_version = False
    if db_path.exists():
        with sqlite3.connect(db_path) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        has_devices = "devices" in tables
        has_alembic_version = "alembic_version" in tables

    if has_devices and not has_alembic_version:
        # Legacy install — stamp without running migrations
        await asyncio.to_thread(command.stamp, alembic_cfg, "head")
    else:
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
