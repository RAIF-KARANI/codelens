"""Moving demo repos into the hosted demo's database (planning.md §15).

Reviews are made on the laptop (it has the GPU); the public demo only shows
them. `export` writes the named repos' rows to a file, and `import` loads that
file into the demo's database, replacing any older copy of the same repos.

What goes, and what doesn't:
- **Copied:** the repo, its files, code graph (nodes and edges), index runs,
  and its reviews with their findings, retrieval trace and timeline. That is
  everything the dashboard shows (D110: a review is a complete record).
- **Left out:** embeddings (large, and a demo has no model to search with),
  👍/👎 votes (people's own), the App's installations and webhook log, and a
  local checkout's path.
- **Refused:** any repo that isn't public on GitHub. Every signed-in or
  anonymous demo visitor sees every repo of the demo (D115, D117), so a
  private repo must never get there. The check asks GitHub without
  credentials: a private repo then looks like it doesn't exist.

Row ids are kept, so the rows still point at each other; afterwards each
table's id sequence is moved past the highest id.
"""

import gzip
import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import DateTime, Table, delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.db import models
from core.enums import RepoSource

FORMAT = "codelens-demo/1"

# Parents before children, so foreign keys hold while importing.
TABLES: tuple[Table, ...] = (
    models.Repo.__table__,
    models.File.__table__,
    models.CodeNode.__table__,
    models.CodeEdge.__table__,
    models.IndexRun.__table__,
    models.Review.__table__,
    models.Finding.__table__,
    models.ReviewContextItem.__table__,
    models.ReviewEvent.__table__,
)  # type: ignore[assignment]

# Columns not copied: they're written as NULL.
LEFT_OUT: dict[str, frozenset[str]] = {
    "repos": frozenset({"installation_id", "local_path"}),
    "code_nodes": frozenset({"embedding", "embedding_model", "embedded_hash", "tsv"}),
}


class DemoError(Exception):
    pass


IsPublic = Callable[[str], Awaitable[bool]]


def github_public_check(api_url: str = "https://api.github.com") -> IsPublic:
    """Whether GitHub shows `owner/name` to anyone: no credentials are sent,
    so a private repo answers 404, like a missing one."""

    async def is_public(full_name: str) -> bool:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{api_url}/repos/{full_name}",
                headers={"Accept": "application/vnd.github+json", "User-Agent": "CodeLens"},
            )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return response.json().get("private") is False

    return is_public


@dataclass(frozen=True, slots=True)
class Summary:
    repos: list[str]
    rows: dict[str, int]


async def export(
    engine: AsyncEngine, full_names: Sequence[str], out: Path, *, is_public: IsPublic
) -> Summary:
    async with engine.connect() as conn:
        repos = (
            await conn.execute(
                select(models.Repo.id, models.Repo.full_name, models.Repo.source).where(
                    models.Repo.full_name.in_(full_names)
                )
            )
        ).all()
        missing = set(full_names) - {r.full_name for r in repos}
        if missing:
            raise DemoError(f"not in this database: {', '.join(sorted(missing))}")
        for repo in repos:
            if repo.source is not RepoSource.GITHUB:
                raise DemoError(f"{repo.full_name} is a local repo: only public GitHub repos go")
            if not await is_public(repo.full_name):
                raise DemoError(f"{repo.full_name} is not public on GitHub: refusing to export it")
        revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        repo_ids = [r.id for r in repos]
        tables = {t.name: [_row(t, r) for r in await _rows(conn, t, repo_ids)] for t in TABLES}

    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(
            {"format": FORMAT, "revision": revision, "repos": sorted(full_names), "tables": tables},
            f,
            default=_json_default,
        )
    return Summary(sorted(full_names), {name: len(rows) for name, rows in tables.items()})


async def import_(engine: AsyncEngine, path: Path) -> Summary:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != FORMAT:
        raise DemoError(f"{path} is not a CodeLens demo export")
    by_name = {t.name: t for t in TABLES}
    repo_ids = [row["id"] for row in data["tables"]["repos"]]

    async with engine.begin() as conn:
        revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != data["revision"]:
            raise DemoError(
                f"schema mismatch: the export is at {data['revision']}, this database at"
                f" {revision}. Run the migrations so both are at the same revision."
            )
        # Replace: deleting the repos cascades to every row that belongs to them.
        await conn.execute(delete(models.Repo).where(models.Repo.id.in_(repo_ids)))
        for name, rows in data["tables"].items():
            table = by_name[name]
            if rows:
                await conn.execute(table.insert(), [_typed(table, row) for row in rows])
        for table in TABLES:
            # Kept ids would otherwise collide with the next new row's.
            await conn.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'),"
                    f" GREATEST((SELECT max(id) FROM {table.name}), 1))"
                )
            )
    return Summary(sorted(data["repos"]), {n: len(r) for n, r in data["tables"].items()})


async def _rows(conn: Any, table: Table, repo_ids: list[int]) -> Iterable[Any]:
    """The table's rows that belong to these repos."""
    c = table.c
    if table.name == "repos":
        where = c.id.in_(repo_ids)
    elif "repo_id" in c:
        where = c.repo_id.in_(repo_ids)
    else:
        # Rows of a review (findings, trace, timeline): through their review.
        reviews = select(models.Review.id).where(models.Review.repo_id.in_(repo_ids))
        where = c.review_id.in_(reviews)
    return (await conn.execute(select(table).where(where).order_by(c.id))).all()


def _row(table: Table, row: Any) -> dict[str, Any]:
    left_out = LEFT_OUT.get(table.name, frozenset())
    return {
        col.name: None if col.name in left_out else getattr(row, col.name) for col in table.columns
    }


def _typed(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    """JSON back into what the database driver expects: datetimes."""
    out = dict(row)
    for col in table.columns:
        if isinstance(col.type, DateTime) and isinstance(out.get(col.name), str):
            out[col.name] = datetime.fromisoformat(out[col.name])
    return out


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"can't export a {type(value).__name__}")


__all__ = ["DemoError", "Summary", "export", "github_public_check", "import_"]
