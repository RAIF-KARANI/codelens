"""Alembic environment: runs migrations over the async (asyncpg) engine."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import core.db.models  # noqa: F401 — registers every table on Base.metadata
from core.config import get_settings
from core.db.base import Base

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False: don't silence loggers that were already
    # configured (pytest's, or the app's when migrations run programmatically).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    # Tests point Alembic at a throwaway DB via set_main_option("sqlalchemy.url").
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    """`alembic upgrade --sql`: emit SQL to stdout instead of executing it."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # Alembic's migration API is synchronous; run_sync hands it a sync-style
    # Connection that drives the asyncpg connection under the hood.
    engine = create_async_engine(database_url(), poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
