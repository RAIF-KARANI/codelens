"""Fixtures for tests that need a real Postgres (marked `integration`), shared by
every suite (core, services, evals).

Each integration test gets its own throwaway database on the server from
DATABASE_URL, so tests never touch your dev data and can't leak state.
"""

import asyncio
from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import get_settings
from core.db.migrate import alembic_config

_TEST_DB = "codelens_test"


async def _execute_as_admin(sql: str) -> None:
    # Connect to the maintenance DB: you can't drop the database you're in.
    url = make_url(get_settings().database_url).set(database="postgres")
    # CREATE/DROP DATABASE refuse to run inside a transaction block.
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(sql))
    finally:
        await engine.dispose()


@pytest.fixture
def empty_database_url() -> Iterator[str]:
    """URL of a freshly created, empty database; dropped after the test."""
    # WITH (FORCE) also kills leftover connections from a crashed earlier run.
    asyncio.run(_execute_as_admin(f'DROP DATABASE IF EXISTS "{_TEST_DB}" WITH (FORCE)'))
    asyncio.run(_execute_as_admin(f'CREATE DATABASE "{_TEST_DB}"'))
    try:
        url = make_url(get_settings().database_url).set(database=_TEST_DB)
        yield url.render_as_string(hide_password=False)
    finally:
        asyncio.run(_execute_as_admin(f'DROP DATABASE IF EXISTS "{_TEST_DB}" WITH (FORCE)'))


@pytest.fixture
def migrated_database_url(empty_database_url: str) -> str:
    """A fresh database at the latest migration."""
    command.upgrade(alembic_config(empty_database_url), "head")
    return empty_database_url
