"""Vector search against a real Postgres + pgvector, with the FakeEmbedder.

The fake embedder is lexical (hashed words), so these tests check search
*mechanics*: scoping, ordering, filters, exclusions. They don't check
semantic quality, which needs the real model (`python -m core.search`).
"""

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from core.config import get_settings
from core.db import create_engine
from core.embedding import embed_repository
from core.enums import NodeKind
from core.indexing import index_repository
from core.providers import EmbedKind
from core.providers.fake import FakeEmbedder
from core.retrieval import SearchHit, vector_search
from core.retrieval.vector import explain_vector_search

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures" / "repos"
EMBEDDER = FakeEmbedder(get_settings().embed_dim)


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@pytest.fixture
def two_repos(migrated_database_url: str) -> str:
    """py_shop and ts_shop indexed + embedded into the same database."""

    async def go() -> None:
        engine = create_engine(migrated_database_url, pooled=False)
        try:
            for name in ("py_shop", "ts_shop"):
                await index_repository(engine, FIXTURES / name, name=f"local/{name}")
                await embed_repository(
                    engine, EMBEDDER, f"local/{name}", max_tokens=get_settings().embed_max_tokens
                )
        finally:
            await engine.dispose()

    run(go())
    return migrated_database_url


def search(url: str, query: str, **kwargs: Any) -> list[SearchHit]:
    async def go() -> list[SearchHit]:
        engine = create_engine(url, pooled=False)
        try:
            async with engine.connect() as conn:
                repo_id = await _repo_id(conn, kwargs.pop("repo", "local/py_shop"))
                vector = (await EMBEDDER.embed([query], kind=EmbedKind.QUERY)).vectors[0]
                return await vector_search(conn, repo_id=repo_id, query_vector=vector, **kwargs)
        finally:
            await engine.dispose()

    return run(go())


async def _repo_id(conn: AsyncConnection, name: str) -> int:
    return (
        await conn.execute(text("SELECT id FROM repos WHERE full_name = :n"), {"n": name})
    ).scalar_one()


def test_finds_the_matching_function_and_orders_by_distance(two_repos: str) -> None:
    hits = search(two_repos, "apply discount to an amount by percent", k=5)

    assert "shop.pricing.apply_discount" in [h.qualified_name for h in hits[:3]]
    assert [h.rank for h in hits] == [1, 2, 3, 4, 5]
    assert [h.distance for h in hits] == sorted(h.distance for h in hits)
    assert all(abs(h.similarity - (1 - h.distance)) < 1e-9 for h in hits)


def test_results_are_scoped_to_one_repo(two_repos: str) -> None:
    py_hits = search(two_repos, "cart total price", k=100)
    ts_hits = search(two_repos, "cart total price", k=100, repo="local/ts_shop")

    # k exceeds both repos: each search returns exactly its own repo's nodes.
    assert {h.path.split("/")[0] for h in py_hits} <= {"shop", "scripts", "tests"}
    assert all(h.path.startswith("src/") for h in ts_hits)
    assert len(py_hits) == 30  # every embedded py_shop node, nothing from ts_shop
    assert not {h.node_id for h in py_hits} & {h.node_id for h in ts_hits}


def test_kind_filter_and_exclusions(two_repos: str) -> None:
    classes = search(two_repos, "order product entity", k=10, kinds={NodeKind.CLASS})
    top = search(two_repos, "apply discount percent", k=3)
    without_top = search(
        two_repos, "apply discount percent", k=3, exclude_node_ids={top[0].node_id}
    )

    assert classes and all(h.kind is NodeKind.CLASS for h in classes)
    assert top[0].node_id not in {h.node_id for h in without_top}
    assert [h.node_id for h in without_top][:2] == [h.node_id for h in top[1:3]]


def test_nodes_without_embeddings_are_skipped(two_repos: str) -> None:
    async def clear_one() -> None:
        engine = create_engine(two_repos, pooled=False)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE code_nodes SET embedding = NULL "
                        "WHERE qualified_name = 'shop.pricing.apply_discount'"
                    )
                )
        finally:
            await engine.dispose()

    run(clear_one())
    hits = search(two_repos, "apply discount percent", k=100)

    assert "shop.pricing.apply_discount" not in {h.qualified_name for h in hits}
    assert len(hits) == 29


def test_runs_inside_the_callers_open_transaction(two_repos: str) -> None:
    """A connection that has already executed something is mid-transaction
    (SQLAlchemy autobegins); search must join it rather than fail."""

    async def go() -> list[SearchHit]:
        engine = create_engine(two_repos, pooled=False)
        try:
            async with engine.connect() as conn:
                repo_id = await _repo_id(conn, "local/py_shop")
                assert conn.in_transaction()
                vector = (await EMBEDDER.embed(["checkout"], kind=EmbedKind.QUERY)).vectors[0]
                return await vector_search(conn, repo_id=repo_id, query_vector=vector, k=3)
        finally:
            await engine.dispose()

    assert len(run(go())) == 3


def test_k_zero_and_explain(two_repos: str) -> None:
    assert search(two_repos, "anything", k=0) == []

    async def plan() -> list[str]:
        engine = create_engine(two_repos, pooled=False)
        try:
            async with engine.connect() as conn:
                repo_id = await _repo_id(conn, "local/py_shop")
                vector = (await EMBEDDER.embed(["x"], kind=EmbedKind.QUERY)).vectors[0]
                return await explain_vector_search(conn, repo_id=repo_id, query_vector=vector, k=5)
        finally:
            await engine.dispose()

    lines = run(plan())
    assert lines[0].startswith("Limit")
