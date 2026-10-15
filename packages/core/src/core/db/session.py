"""Async engine and session factories."""

from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


def create_engine(
    database_url: str, *, pooled: bool = True, transaction_pooler: bool = False
) -> AsyncEngine:
    """Create an async engine. Creating it does not open a connection.

    pooled=False is for Celery workers and CLIs. A pooled connection is bound to
    the event loop that opened it; each Celery task runs its own `asyncio.run()`
    loop, so reusing a pooled connection across tasks fails with
    "attached to a different loop". NullPool opens a fresh connection each time.

    transaction_pooler=True is for a URL that goes through a pooler in
    transaction mode (Supabase's port 6543, PgBouncer), as the hosted demo's
    does (docs/demo.md): it gives each transaction whichever server connection
    is free, so a statement prepared in one transaction may not exist in the
    next. asyncpg's statement caches are then turned off, and each prepared
    statement gets a unique name so two never collide on one server
    connection (SQLAlchemy's documented setup for PgBouncer).
    """
    connect_args: dict[str, Any] = {}
    if transaction_pooler:
        connect_args = {
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
        }
    if pooled:
        # pre_ping replaces connections that died (e.g. Postgres restarted)
        # instead of handing a broken one to a request.
        return create_async_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
    return create_async_engine(database_url, poolclass=NullPool, connect_args=connect_args)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: after commit, attributes stay loaded. Otherwise
    # touching them triggers an implicit lazy refresh, which async sessions forbid.
    return async_sessionmaker(engine, expire_on_commit=False)
