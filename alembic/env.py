from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.config import DATABASE_URL
from app.database import Base
import app.models  # noqa: F401 — ensures all models are registered for autogenerate

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Alembic is synchronous — derive a sync SQLite URL from the async one.
SYNC_URL = DATABASE_URL.replace("sqlite+aiosqlite:", "sqlite:")


def run_migrations_offline() -> None:
    """Generate SQL without a live database connection."""
    context.configure(
        url=SYNC_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the live database using a sync engine."""
    engine = create_engine(SYNC_URL)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
