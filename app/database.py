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
    from sqlalchemy import text

    alembic_cfg = Config("alembic.ini")

    # Detect existing databases that predate Alembic using sqlite_master —
    # more reliable than the inspect API inside a transaction context.
    async with engine.connect() as conn:
        has_devices = (await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='devices'")
        )).fetchone() is not None

        has_alembic_version = (await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'")
        )).fetchone() is not None

    if has_devices and not has_alembic_version:
        # Legacy install — stamp without running migrations
        await asyncio.to_thread(command.stamp, alembic_cfg, "head")
    else:
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
