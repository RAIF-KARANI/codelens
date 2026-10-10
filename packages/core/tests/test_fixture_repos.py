"""Golden tests: the exact code graph of two small but realistic repos.

Each fixture repo under tests/fixtures/repos/ has a sibling `<name>.graph`
file listing every file, node, skipped file and edge, one fact per line:

    file  shop/models.py python
    node  method shop.models.Product.gross#1 25-27
    skip  src/types.d.ts type_declarations
    edge  calls shop.models.Order.summary -> shop.models.Entity.describe
                                          ?> unresolved (kept by name)
                                          x> external (stdlib / third-party)

The parser output must match it exactly, and so must the graph read back from
Postgres after indexing. Any change to extraction or resolution shows up as a
readable diff in review.

The golden files were generated and then checked line by line against the
fixture source. After an *intentional* behaviour change, regenerate with
`UPDATE_GOLDEN=1 uv run pytest packages/core/tests/test_fixture_repos.py`
and review the diff before committing it.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from core.db import create_engine
from core.indexing import index_repository
from core.parser import ParseResult, parse_repository

FIXTURES = Path(__file__).parent / "fixtures" / "repos"
REPOS = ["py_shop", "ts_shop"]


def render_parse(result: ParseResult) -> list[str]:
    lines: list[str] = []
    for file in result.files:
        lines.append(f"file {file.path} {file.language.value}" + (" test" if file.is_test else ""))
        lines += [f"node {n.kind.value} {n.ref} {n.start_line}-{n.end_line}" for n in file.nodes]
    lines += sorted(f"skip {s.path} {s.reason}" for s in result.skipped)
    edges = []
    for e in result.edges:
        if e.target is not None:
            edges.append(f"edge {e.kind.value} {e.source} -> {e.target}")
        else:
            arrow = "x>" if e.external else "?>"
            edges.append(f"edge {e.kind.value} {e.source} {arrow} {e.target_name}")
    return lines + sorted(edges)


def golden_path(repo: str) -> Path:
    return FIXTURES / f"{repo}.graph"


def read_golden(repo: str) -> list[str]:
    return golden_path(repo).read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("repo", REPOS)
def test_parser_output_matches_golden(repo: str) -> None:
    actual = render_parse(parse_repository(FIXTURES / repo))

    if os.environ.get("UPDATE_GOLDEN") == "1":
        # newline="\n": LF on every OS, so regenerating on Windows gives a clean diff.
        golden_path(repo).write_text("\n".join(actual) + "\n", encoding="utf-8", newline="\n")
        pytest.skip(f"rewrote {golden_path(repo).name}; review the diff")
    # Comparing lists makes pytest print a line-level diff on failure.
    assert actual == read_golden(repo)


# ---------------------------------------------------------------- DB round trip


def _query(url: str, sql: str) -> list[Any]:
    async def go() -> list[Any]:
        engine = create_engine(url, pooled=False)
        try:
            async with engine.connect() as conn:
                return list(await conn.execute(text(sql)))
        finally:
            await engine.dispose()

    return asyncio.run(go())


def render_db(url: str) -> list[str]:
    """The same format, rebuilt purely from what is stored in Postgres."""
    lines: list[str] = []
    files = _query(url, "SELECT id, path, language, is_test FROM files ORDER BY path")
    nodes = _query(
        url,
        "SELECT file_id, kind, qualified_name, ordinal, start_line, end_line "
        "FROM code_nodes ORDER BY file_id, start_line, id",
    )
    for f in files:
        lines.append(f"file {f.path} {f.language}" + (" test" if f.is_test else ""))
        for n in (n for n in nodes if n.file_id == f.id):
            ref = n.qualified_name + (f"#{n.ordinal}" if n.ordinal else "")
            lines.append(f"node {n.kind} {ref} {n.start_line}-{n.end_line}")
    edges = _query(
        url,
        """
        SELECT e.kind, s.qualified_name AS src, s.ordinal AS src_ord, e.target_name,
               d.qualified_name AS dst, d.ordinal AS dst_ord
        FROM code_edges e
        JOIN code_nodes s ON s.id = e.src_node_id
        LEFT JOIN code_nodes d ON d.id = e.dst_node_id
        """,
    )
    rendered = []
    for e in edges:
        src = e.src + (f"#{e.src_ord}" if e.src_ord else "")
        if e.dst is not None:
            dst = e.dst + (f"#{e.dst_ord}" if e.dst_ord else "")
            rendered.append(f"edge {e.kind} {src} -> {dst}")
        else:
            rendered.append(f"edge {e.kind} {src} ?> {e.target_name}")
    return lines + sorted(rendered)


def _expected_in_db(repo: str) -> list[str]:
    """Golden lines as the DB can represent them: skipped files aren't stored,
    and 'external' isn't a column, so x> and ?> both read back as unresolved."""
    lines = [
        line.replace(" x> ", " ?> ") for line in read_golden(repo) if not line.startswith("skip ")
    ]
    files_and_nodes = [line for line in lines if not line.startswith("edge ")]
    return files_and_nodes + sorted(line for line in lines if line.startswith("edge "))


@pytest.mark.integration
@pytest.mark.parametrize("repo", REPOS)
def test_indexed_graph_matches_golden(repo: str, migrated_database_url: str) -> None:
    async def index() -> None:
        engine = create_engine(migrated_database_url, pooled=False)
        try:
            await index_repository(engine, FIXTURES / repo)
        finally:
            await engine.dispose()

    asyncio.run(index())
    assert render_db(migrated_database_url) == _expected_in_db(repo)

    # Re-indexing the unchanged fixture leaves the stored graph byte-identical.
    asyncio.run(index())
    assert render_db(migrated_database_url) == _expected_in_db(repo)
