"""Moving demo repos to the hosted demo's database (core/demo): only public
GitHub repos, everything the dashboard shows, nothing personal or private."""

import asyncio
import gzip
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from core.config import Settings
from core.db import create_engine
from core.demo import DemoError, Summary, export, import_

pytestmark = pytest.mark.integration

Sql = Callable[..., list[Any]]
NAME = "demo/shop"
COUNTED = ("code_nodes", "code_edges", "files", "index_runs", "reviews", "findings")


async def public(_: str) -> bool:
    return True


async def private(_: str) -> bool:
    return False


def run(settings: Settings, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    async def go() -> Any:
        engine = create_engine(settings.database_url, pooled=False)
        try:
            return await fn(engine, *args, **kwargs)
        finally:
            await engine.dispose()

    return asyncio.run(go())


@pytest.fixture
def reviewed(shop: Any, sql: Sql, worker_on_fakes: Any) -> int:
    """The shop repo, as if it came from GitHub, with a finished review and a vote."""
    from worker.tasks import review

    review_id = sql(
        "INSERT INTO reviews (repo_id, source, head_sha, status)"
        " VALUES (:repo, 'cli', :head, 'queued') RETURNING id",
        repo=shop.repo_id,
        head=shop.head_sha,
    )[0].id
    review.apply(args=(review_id, shop.base_sha)).get()
    sql(
        "UPDATE repos SET source = 'github', full_name = :name, local_path = NULL",
        name=NAME,
    )
    sql(
        "INSERT INTO finding_feedback (finding_id, github_user_id, vote)"
        " SELECT id, '42', 1 FROM findings LIMIT 1"
    )
    return review_id


def counts(sql: Sql) -> dict[str, int]:
    return {t: sql(f"SELECT count(*) AS n FROM {t}")[0].n for t in COUNTED}


def test_export_then_import_replaces_the_repo(
    settings: Settings, sql: Sql, reviewed: int, tmp_path: Path
) -> None:
    before = counts(sql)
    findings = sql("SELECT id, title FROM findings ORDER BY id")
    out = tmp_path / "demo.json.gz"

    exported: Summary = run(settings, export, [NAME], out, is_public=public)
    with gzip.open(out, "rt", encoding="utf-8") as f:
        data = json.load(f)
    # Importing into the same database replaces the repo with its copy.
    imported: Summary = run(settings, import_, out)

    assert exported.repos == imported.repos == [NAME]
    assert {t: exported.rows[t] for t in COUNTED} == before
    assert counts(sql) == before
    assert sql("SELECT id, title FROM findings ORDER BY id") == findings
    # Left out: embeddings, votes, where the checkout was.
    assert all(n["embedding"] is None for n in data["tables"]["code_nodes"])
    assert data["tables"]["repos"][0]["local_path"] is None
    assert "finding_feedback" not in data["tables"]
    assert sql("SELECT count(*) AS n FROM finding_feedback")[0].n == 0
    assert sql("SELECT count(*) AS n FROM code_nodes WHERE embedding IS NOT NULL")[0].n == 0
    # Ids were kept, and the sequences moved past them: a new row still fits.
    [repo] = sql("SELECT id FROM repos")
    sql(
        "INSERT INTO reviews (repo_id, source, head_sha, status)"
        " VALUES (:repo, 'cli', 'x', 'queued')",
        repo=repo.id,
    )


def test_only_public_github_repos_are_exported(
    settings: Settings, sql: Sql, reviewed: int, tmp_path: Path
) -> None:
    out = tmp_path / "demo.json.gz"
    with pytest.raises(DemoError, match="not public"):
        run(settings, export, [NAME], out, is_public=private)
    with pytest.raises(DemoError, match="not in this database"):
        run(settings, export, ["someone/else"], out, is_public=public)
    sql("UPDATE repos SET source = 'local'")
    with pytest.raises(DemoError, match="local repo"):
        run(settings, export, [NAME], out, is_public=public)
    assert not out.exists()  # nothing written when refused


def test_an_export_from_another_schema_is_refused(
    settings: Settings, reviewed: int, tmp_path: Path
) -> None:
    out = tmp_path / "demo.json.gz"
    run(settings, export, [NAME], out, is_public=public)
    with gzip.open(out, "rt", encoding="utf-8") as f:
        data = json.load(f)
    data["revision"] = "0000"
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(data, f)
    with pytest.raises(DemoError, match="schema mismatch"):
        run(settings, import_, out)
