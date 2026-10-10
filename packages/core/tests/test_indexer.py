"""Indexer behaviour against a real Postgres: idempotency, node-level diffs,
deletions with re-linking, and concurrency."""

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from sqlalchemy import text

from core.config import get_settings
from core.db import create_engine
from core.indexing import IndexingError, IndexReport, index_repository
from core.parser import parse_repository

pytestmark = pytest.mark.integration


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(content).lstrip("\n"), encoding="utf-8")


def index(url: str, root: Path, name: str | None = None) -> IndexReport:
    async def _go() -> IndexReport:
        engine = create_engine(url, pooled=False)
        try:
            return await index_repository(engine, root, name=name)
        finally:
            await engine.dispose()

    return run(_go())


def query(url: str, sql: str, **params: Any) -> list[Any]:
    async def _go() -> list[Any]:
        engine = create_engine(url, pooled=False)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(sql), params)
                return list(result) if result.returns_rows else []
        finally:
            await engine.dispose()

    return run(_go())


REPO = {
    "app/__init__.py": "",
    "app/util.py": """
        def helper():
            return 1
        """,
    "app/models.py": """
        class Base:
            @property
            def value(self):
                return 1

            @value.setter
            def value(self, v):
                pass

        class User(Base):
            def save(self):
                return self.value
        """,
    "app/main.py": """
        from app.util import helper
        from app.models import User
        import requests

        def run():
            helper()
            User().save()
            requests.get("x")
        """,
    "web/client.ts": """
        import { helper } from './helpers';
        export function go() { return helper(); }
        """,
    "web/helpers.ts": """
        export function helper() { return 1; }
        """,
}


def test_first_run_stores_exactly_the_parsed_graph(
    migrated_database_url: str, tmp_path: Path
) -> None:
    write(tmp_path, REPO)
    parsed = parse_repository(tmp_path)

    report = index(migrated_database_url, tmp_path)

    nodes = query(
        migrated_database_url,
        "SELECT qualified_name, ordinal, kind, start_line, end_line, content_hash FROM code_nodes",
    )
    assert sorted(
        (n.qualified_name, n.ordinal, n.kind, n.start_line, n.end_line, n.content_hash)
        for n in nodes
    ) == sorted(
        (n.qualified_name, n.ordinal, n.kind.value, n.start_line, n.end_line, n.content_hash)
        for f in parsed.files
        for n in f.nodes
    )

    # Every edge, resolved to the same (qualified_name, ordinal) the parser chose,
    # including edges written before their target file was inserted (re-linked).
    stored_edges = query(
        migrated_database_url,
        """
        SELECT s.qualified_name AS src, s.ordinal AS src_ord, e.kind, e.target_name,
               d.qualified_name AS dst, d.ordinal AS dst_ord
        FROM code_edges e
        JOIN code_nodes s ON s.id = e.src_node_id
        LEFT JOIN code_nodes d ON d.id = e.dst_node_id
        """,
    )
    assert sorted(
        (e.src, e.src_ord, e.kind, e.target_name, e.dst, e.dst_ord) for e in stored_edges
    ) == sorted(
        (
            e.source.qualified_name,
            e.source.ordinal,
            e.kind.value,
            e.target_name,
            e.target.qualified_name if e.target else None,
            e.target.ordinal if e.target else None,
        )
        for e in parsed.edges
    )

    [repo] = query(migrated_database_url, "SELECT full_name, source, status, local_path FROM repos")
    assert (repo.full_name, repo.source, repo.status) == (
        f"local/{tmp_path.name}",
        "local",
        "ready",
    )
    assert repo.local_path == str(tmp_path.resolve())
    [run_row] = query(migrated_database_url, "SELECT status, stats FROM index_runs")
    assert run_row.status == "succeeded"
    assert run_row.stats["files"]["new"] == len(parsed.files)
    assert report.files_new == len(parsed.files)


def _row_versions(url: str) -> list[tuple[str, int, str]]:
    # xmin is the id of the transaction that last wrote a row. If a second run
    # inserts, updates, or deletes anything, this snapshot changes.
    rows = query(
        url,
        """
        SELECT 'files' AS tbl, id, xmin::text AS v FROM files
        UNION ALL SELECT 'nodes', id, xmin::text FROM code_nodes
        UNION ALL SELECT 'edges', id, xmin::text FROM code_edges
        """,
    )
    return sorted((r.tbl, r.id, r.v) for r in rows)


def test_second_run_on_unchanged_repo_writes_nothing(
    migrated_database_url: str, tmp_path: Path
) -> None:
    write(tmp_path, REPO)
    index(migrated_database_url, tmp_path)
    before = _row_versions(migrated_database_url)

    report = index(migrated_database_url, tmp_path)

    assert _row_versions(migrated_database_url) == before
    assert not report.wrote_graph
    assert report.files_unchanged == len(REPO)
    statuses = query(migrated_database_url, "SELECT status FROM index_runs ORDER BY id")
    assert [s.status for s in statuses] == ["succeeded", "succeeded"]  # runs are still recorded


def test_editing_one_function_rewrites_only_what_changed(
    migrated_database_url: str, tmp_path: Path
) -> None:
    write(
        tmp_path,
        {
            "m.py": """
                def f1():
                    return 1

                def f2():
                    return 2

                def f3():
                    return 3
                """
        },
    )
    index(migrated_database_url, tmp_path)
    # Pretend the embedding job ran, so we can see which embeddings survive.
    dim = get_settings().embed_dim
    query(
        migrated_database_url,
        f"UPDATE code_nodes SET embedding = array_fill(0.1, ARRAY[{dim}])::vector",
    )
    ids_before = {
        r.qualified_name: r.id
        for r in query(migrated_database_url, "SELECT id, qualified_name FROM code_nodes")
    }

    write(
        tmp_path,
        {
            "m.py": """
                def f1():
                    return 1

                def f2():
                    x = 2
                    return x

                def f3():
                    return 3
                """
        },
    )
    report = index(migrated_database_url, tmp_path)

    rows = {
        r.qualified_name: r
        for r in query(
            migrated_database_url,
            "SELECT id, qualified_name, start_line, embedding IS NOT NULL AS has_embedding"
            " FROM code_nodes",
        )
    }
    assert {name: row.id for name, row in rows.items()} == ids_before  # identities are stable
    writes = report.writes
    assert (writes.nodes_updated, writes.nodes_moved, writes.nodes_kept) == (2, 1, 1)
    assert rows["m.f1"].has_embedding  # untouched
    assert not rows["m.f2"].has_embedding  # code changed: embedding cleared for recompute
    assert not rows["m"].has_embedding  # the module's code is the whole file, so it changed too
    assert rows["m.f3"].has_embedding  # only moved down a line: embedding still valid
    assert rows["m.f3"].start_line == 8


def test_deleted_target_keeps_edge_and_relinks_when_it_returns(
    migrated_database_url: str, tmp_path: Path
) -> None:
    files = {
        "a.py": """
            def helper():
                return 1
            """,
        "b.py": """
            from a import helper

            def use():
                return helper()
            """,
    }
    write(tmp_path, files)
    index(migrated_database_url, tmp_path)
    edge_sql = """
        SELECT e.dst_node_id FROM code_edges e JOIN code_nodes s ON s.id = e.src_node_id
        WHERE s.qualified_name = 'b.use' AND e.kind = 'calls' AND e.target_name = 'a.helper'
        """
    [edge] = query(migrated_database_url, edge_sql)
    assert edge.dst_node_id is not None

    (tmp_path / "a.py").unlink()
    report = index(migrated_database_url, tmp_path)
    [edge] = query(migrated_database_url, edge_sql)
    assert report.files_deleted == 1
    assert edge.dst_node_id is None  # b.py unchanged, so its edge stays, unresolved

    write(tmp_path, {"a.py": files["a.py"]})
    report = index(migrated_database_url, tmp_path)
    [edge] = query(migrated_database_url, edge_sql)
    [helper] = query(
        migrated_database_url, "SELECT id FROM code_nodes WHERE qualified_name = 'a.helper'"
    )
    assert report.edges_relinked >= 1
    assert edge.dst_node_id == helper.id


def test_concurrent_run_for_the_same_repo_is_rejected(
    migrated_database_url: str, tmp_path: Path
) -> None:
    write(tmp_path, {"m.py": "x = 1\n"})
    name = "local/concurrent"

    async def _go() -> None:
        engine = create_engine(migrated_database_url, pooled=False)
        try:
            async with engine.connect() as holder:  # simulate another indexer holding the lock
                await holder.execute(
                    text("SELECT pg_advisory_lock(hashtext(:key))"), {"key": f"index:{name}"}
                )
                with pytest.raises(IndexingError, match="in progress"):
                    await index_repository(engine, tmp_path, name=name)
        finally:
            await engine.dispose()

    run(_go())


def test_same_name_from_a_different_path_is_rejected(
    migrated_database_url: str, tmp_path: Path
) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    write(first, {"m.py": "x = 1\n"})
    write(second, {"m.py": "x = 1\n"})
    index(migrated_database_url, first, name="local/app")

    with pytest.raises(IndexingError, match="already indexed from"):
        index(migrated_database_url, second, name="local/app")
