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
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    alembic_cfg = Config("alembic.ini")

    # Detect existing databases that predate Alembic: if our tables are already
    # there but the alembic_version table isn't, stamp to head rather than
    # running migrations (which would fail on the already-existing tables).
    async with engine.begin() as conn:
        def _check(sync_conn):
            tables = inspect(sync_conn).get_table_names()
            return "devices" in tables and "alembic_version" not in tables

        is_legacy = await conn.run_sync(_check)

    if is_legacy:
        await asyncio.to_thread(command.stamp, alembic_cfg, "head")
    else:
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
