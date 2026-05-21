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

    # Detect installs that predate Alembic (or had a failed first stamp):
    # if the app tables exist but alembic_version has no recorded revision,
    # stamp to head before upgrading so the migration doesn't try to recreate
    # tables that already exist.
    db_path = Path(DATABASE_URL.replace("sqlite+aiosqlite:///", ""))
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
            needs_stamp = "devices" in tables and current_rev is None
        except Exception:
            pass

    if needs_stamp:
        await asyncio.to_thread(command.stamp, alembic_cfg, "head")

    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
