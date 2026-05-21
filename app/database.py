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

    needs_stamp = False
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path)) as conn:
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                current_rev = None
                if "alembic_version" in tables:
                    row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
                    current_rev = row[0] if row else None
            log.info("DB tables=%s current_revision=%s", tables, current_rev)
            # Stamp needed when tables exist but Alembic has no recorded revision
            needs_stamp = "devices" in tables and current_rev is None
        except Exception as e:
            log.warning("DB detection failed: %s", e)

    log.info("needs_stamp=%s", needs_stamp)
    if needs_stamp:
        log.info("Stamping existing database to head")
        await asyncio.to_thread(command.stamp, alembic_cfg, "head")

    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
