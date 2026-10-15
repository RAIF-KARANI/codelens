from typing import Any

import pytest

import core.db.session as session
from core.config import Settings
from core.db.session import create_engine

URL = "postgresql+asyncpg://user:pw@db.example:6543/postgres"


@pytest.fixture
def engine_kwargs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The keyword arguments each create_engine() call passes to SQLAlchemy."""
    calls: list[dict[str, Any]] = []

    def fake_create_async_engine(url: str, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(session, "create_async_engine", fake_create_async_engine)
    return calls


def test_a_direct_connection_keeps_asyncpg_defaults(engine_kwargs: list[dict[str, Any]]) -> None:
    create_engine(URL)
    create_engine(URL, pooled=False)

    assert [kw["connect_args"] for kw in engine_kwargs] == [{}, {}]


@pytest.mark.parametrize("pooled", [True, False])
def test_a_transaction_pooler_turns_off_statement_caches(
    engine_kwargs: list[dict[str, Any]], pooled: bool
) -> None:
    # A transaction-mode pooler hands each transaction any server connection,
    # so a statement prepared earlier may not be there ("prepared statement
    # ... does not exist", seen through Supabase's port 6543).
    create_engine(URL, pooled=pooled, transaction_pooler=True)

    args = engine_kwargs[0]["connect_args"]
    assert args["statement_cache_size"] == 0
    assert args["prepared_statement_cache_size"] == 0
    names = {args["prepared_statement_name_func"]() for _ in range(3)}
    assert len(names) == 3  # unique, so two never collide on one server connection


def test_transaction_pooler_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).database_transaction_pooler is False
    monkeypatch.setenv("DATABASE_TRANSACTION_POOLER", "true")
    assert Settings(_env_file=None).database_transaction_pooler is True
