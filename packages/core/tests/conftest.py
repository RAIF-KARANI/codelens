"""Fixtures for tests that need a real Postgres (marked `integration`).

Each integration test gets its own throwaway database on the server from
DATABASE_URL, so tests never touch your dev data and can't leak state.
"""

import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import get_settings


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
    name = "codelens_test"
    # WITH (FORCE) also kills leftover connections from a crashed earlier run.
    asyncio.run(_execute_as_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    asyncio.run(_execute_as_admin(f'CREATE DATABASE "{name}"'))
    try:
        url = make_url(get_settings().database_url).set(database=name)
        yield url.render_as_string(hide_password=False)
    finally:
        asyncio.run(_execute_as_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
