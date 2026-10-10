"""Async engine and session factories."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


def create_engine(database_url: str, *, pooled: bool = True) -> AsyncEngine:
    """Create an async engine. Creating it does not open a connection.

    pooled=False is for Celery workers and CLIs. A pooled connection is bound to
    the event loop that opened it; each Celery task runs its own `asyncio.run()`
    loop, so reusing a pooled connection across tasks fails with
    "attached to a different loop". NullPool opens a fresh connection each time.
    """
    if pooled:
        # pre_ping replaces connections that died (e.g. Postgres restarted)
        # instead of handing a broken one to a request.
        return create_async_engine(database_url, pool_pre_ping=True)
    return create_async_engine(database_url, poolclass=NullPool)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: after commit, attributes stay loaded. Otherwise
    # touching them triggers an implicit lazy refresh, which async sessions forbid.
    return async_sessionmaker(engine, expire_on_commit=False)
