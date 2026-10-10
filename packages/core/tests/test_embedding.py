"""The embedding job against a real Postgres, with the deterministic FakeEmbedder."""

import asyncio
import hashlib
import shutil
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from core.config import get_settings
from core.db import create_engine
from core.embedding import EmbeddingError, EmbedReport, embed_repository
from core.embedding.job import _load_chunk_sources
from core.indexing import index_repository
from core.providers.fake import FakeEmbedder
from core.text import build_chunk

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parent / "fixtures" / "repos" / "py_shop"
REPO = "local/shop"
MAX_TOKENS = get_settings().embed_max_tokens


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def index(url: str, root: Path) -> None:
    async def go() -> None:
        engine = create_engine(url, pooled=False)
        try:
            await index_repository(engine, root, name=REPO)
        finally:
            await engine.dispose()

    run(go())


def embed(
    url: str, embedder: FakeEmbedder | None = None, *, limit: int | None = None
) -> EmbedReport:
    async def go() -> EmbedReport:
        engine = create_engine(url, pooled=False)
        try:
            return await embed_repository(
                engine,
                embedder or FakeEmbedder(get_settings().embed_dim),
                REPO,
                max_tokens=MAX_TOKENS,
                page_size=8,
                limit=limit,
            )
        finally:
            await engine.dispose()

    return run(go())


def query(url: str, sql: str) -> list[Any]:
    async def go() -> list[Any]:
        engine = create_engine(url, pooled=False)
        try:
            async with engine.connect() as conn:
                return list(await conn.execute(text(sql)))
        finally:
            await engine.dispose()

    return run(go())


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """A private copy of the fixture, so tests can edit files."""
    return Path(shutil.copytree(FIXTURE, tmp_path / "py_shop"))


def test_embeds_every_node_and_records_provenance(
    migrated_database_url: str, repo_copy: Path
) -> None:
    index(migrated_database_url, repo_copy)

    report = embed(migrated_database_url)

    rows = query(
        migrated_database_url,
        "SELECT id, embedding IS NOT NULL AS has, embedding_model, embedded_hash FROM code_nodes",
    )
    assert report.embedded == report.nodes_total == len(rows) > 0
    assert all(r.has and r.embedding_model == "fake-embedder" for r in rows)

    # embedded_hash is the sha256 of exactly the chunk text that was embedded.
    async def chunk_hashes() -> dict[int, str]:
        engine = create_engine(migrated_database_url, pooled=False)
        try:
            async with engine.connect() as conn:
                repo_id = (await conn.execute(text("SELECT id FROM repos"))).scalar_one()
                _, sources = await _load_chunk_sources(conn, repo_id)
        finally:
            await engine.dispose()
        return {
            node_id: hashlib.sha256(
                build_chunk(src, max_tokens=MAX_TOKENS).text.encode()
            ).hexdigest()
            for node_id, src in sources.items()
        }

    assert {r.id: r.embedded_hash for r in rows} == run(chunk_hashes())


def _versions(url: str) -> list[tuple[int, str]]:
    return sorted((r.id, r.v) for r in query(url, "SELECT id, xmin::text AS v FROM code_nodes"))


def test_rerun_on_up_to_date_repo_writes_nothing(
    migrated_database_url: str, repo_copy: Path
) -> None:
    index(migrated_database_url, repo_copy)
    embed(migrated_database_url)
    before = _versions(migrated_database_url)

    report = embed(migrated_database_url)

    assert report.embedded == 0
    assert report.up_to_date == report.nodes_total
    assert _versions(migrated_database_url) == before


def _embedded_names(url: str, before: dict[str, str]) -> set[str]:
    """Nodes whose embedded_hash differs from `before` (i.e. were re-embedded)."""
    after = {
        r.qualified_name: r.embedded_hash
        for r in query(url, "SELECT qualified_name, embedded_hash FROM code_nodes")
    }
    return {name for name, h in after.items() if before.get(name) != h}


def test_editing_a_function_body_reembeds_only_that_function(
    migrated_database_url: str, repo_copy: Path
) -> None:
    """The module's code changed too, but its chunk is a skeleton that only shows
    the function's signature, so its chunk text (and vector) are still current."""
    index(migrated_database_url, repo_copy)
    embed(migrated_database_url)
    before = {
        r.qualified_name: r.embedded_hash
        for r in query(
            migrated_database_url, "SELECT qualified_name, embedded_hash FROM code_nodes"
        )
    }

    pricing = repo_copy / "shop" / "pricing.py"
    pricing.write_text(pricing.read_text().replace("(1 + TAX_RATE)", "(1.0 + TAX_RATE)"))
    index(migrated_database_url, repo_copy)
    report = embed(migrated_database_url)

    assert report.embedded == 1
    assert _embedded_names(migrated_database_url, before) == {"shop.pricing.with_tax"}


def test_changing_a_class_header_reembeds_its_methods_too(
    migrated_database_url: str, repo_copy: Path
) -> None:
    """The methods' code is unchanged, but their chunk header names the class
    signature, so their chunk text (and hash) changed: they must be re-embedded."""
    index(migrated_database_url, repo_copy)
    embed(migrated_database_url)
    before = {
        r.qualified_name: r.embedded_hash
        for r in query(
            migrated_database_url, "SELECT qualified_name, embedded_hash FROM code_nodes"
        )
    }

    models = repo_copy / "shop" / "models.py"
    models.write_text(
        models.read_text().replace("class Order(Entity):", "class Order(Entity, object):")
    )
    index(migrated_database_url, repo_copy)
    embed(migrated_database_url)

    assert _embedded_names(migrated_database_url, before) == {
        "shop.models",  # code changed
        "shop.models.Order",  # code changed
        "shop.models.Order.__init__",  # header changed: "# in class: class Order(Entity, object)"
        "shop.models.Order.total",
        "shop.models.Order.summary",
    }


class OtherModel(FakeEmbedder):
    model = "fake-embedder-v2"


def test_switching_models_reembeds_everything(migrated_database_url: str, repo_copy: Path) -> None:
    index(migrated_database_url, repo_copy)
    first = embed(migrated_database_url)

    second = embed(migrated_database_url, OtherModel(get_settings().embed_dim))

    assert second.embedded == first.embedded
    assert {
        r.embedding_model
        for r in query(migrated_database_url, "SELECT embedding_model FROM code_nodes")
    } == {"fake-embedder-v2"}


def test_limit_then_resume(migrated_database_url: str, repo_copy: Path) -> None:
    index(migrated_database_url, repo_copy)

    partial = embed(migrated_database_url, limit=10)
    rest = embed(migrated_database_url)

    assert partial.embedded == 10
    assert rest.up_to_date == 10
    assert partial.embedded + rest.embedded == rest.nodes_total


def test_unknown_repo_is_a_clear_error(migrated_database_url: str) -> None:
    async def go() -> None:
        engine = create_engine(migrated_database_url, pooled=False)
        try:
            await embed_repository(engine, FakeEmbedder(4), "local/nope", max_tokens=MAX_TOKENS)
        finally:
            await engine.dispose()

    with pytest.raises(EmbeddingError, match=r"run core\.index first"):
        run(go())
