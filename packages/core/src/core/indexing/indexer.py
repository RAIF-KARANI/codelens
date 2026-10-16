"""Index a local repository: parse everything, write only what changed."""

import asyncio
import logging
import subprocess
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.db.models import IndexRun, Repo
from core.diff import git
from core.enums import IndexRunStatus, RepoSource, RepoStatus
from core.indexing.persist import (
    FileWrite,
    analyze_graph_tables,
    count_graph,
    delete_files,
    load_files,
    load_id_map,
    relink_edges,
    write_file,
)
from core.parser import ParseResult, ParseStats, parse_repository, summarize
from core.parser.repository import parse_revision
from core.parser.types import NodeRef, ParsedEdge

logger = logging.getLogger(__name__)

repos_t = Repo.__table__
runs_t = IndexRun.__table__

# Skips that may be temporary. A file we merely failed to read this time keeps
# its previous rows instead of being deleted from the index.
_TRANSIENT_SKIPS = frozenset({"parse_error", "unreadable"})
# Below this many written rows, stale statistics are harmless; above it, ANALYZE.
_ANALYZE_THRESHOLD_ROWS = 1000


class IndexingError(Exception):
    """A user-facing indexing error (bad input, concurrent run)."""


class IndexLocked(IndexingError):
    """Another index run holds this repo's lock. Worth retrying later, unlike
    other indexing errors (the worker's index task does)."""


@dataclass(slots=True)
class IndexReport:
    repo_id: int
    repo_name: str
    index_run_id: int
    commit_sha: str | None
    parse: ParseStats
    files_new: int = 0
    files_changed: int = 0
    files_unchanged: int = 0
    files_deleted: int = 0
    writes: FileWrite = field(default_factory=FileWrite)
    nodes_deleted_with_files: int = 0
    edges_relinked: int = 0
    db_totals: dict[str, int] = field(default_factory=dict)
    parse_seconds: float = 0.0
    write_seconds: float = 0.0

    @property
    def wrote_graph(self) -> bool:
        return bool(
            self.files_new or self.files_changed or self.files_deleted or self.edges_relinked
        )

    def to_json(self) -> dict[str, Any]:
        """Stats stored on the index_runs row."""
        w = self.writes
        return {
            "files": {
                "parsed": self.parse.files,
                "new": self.files_new,
                "changed": self.files_changed,
                "unchanged": self.files_unchanged,
                "deleted": self.files_deleted,
                "skipped": dict(self.parse.skipped),
            },
            "nodes": {
                "by_kind": dict(self.parse.nodes_by_kind),
                "inserted": w.nodes_inserted,
                "updated": w.nodes_updated,
                "moved": w.nodes_moved,
                "kept": w.nodes_kept,
                "deleted": w.nodes_deleted + self.nodes_deleted_with_files,
            },
            "edges": {
                "by_kind": dict(self.parse.edges_by_kind),
                "written": w.edges_written,
                "relinked": self.edges_relinked,
            },
            "calls": {
                "total": self.parse.calls,
                "resolved": self.parse.calls_resolved,
                "external": self.parse.calls_external,
                "resolution": round(self.parse.call_resolution, 4),
                "internal_resolution": round(self.parse.internal_call_resolution, 4),
            },
            "db_totals": self.db_totals,
            "timing_s": {
                "parse": round(self.parse_seconds, 3),
                "write": round(self.write_seconds, 3),
            },
        }


async def index_repository(
    engine: AsyncEngine, root: Path, *, name: str | None = None
) -> IndexReport:
    """Index a working tree (the CLI's local repos). The repo row is created
    on the first run, as `local/<dir name>` unless `name` is given."""
    full_name = name or f"local/{(await asyncio.to_thread(root.resolve)).name}"
    async with _repo_lock(engine, full_name):
        # Parse outside any transaction: it's the slowest part, and we don't
        # want to hold row locks while it runs. It is blocking file I/O + CPU,
        # so it runs in a worker thread rather than stalling the event loop.
        started = time.perf_counter()
        root, result = await asyncio.to_thread(_resolve_and_parse, root)
        parse_seconds = time.perf_counter() - started
        commit_sha = await asyncio.to_thread(_git_head, root)
        report = await _index_locked(engine, full_name, result, commit_sha, root)
    report.parse_seconds = parse_seconds
    return report


async def index_revision(engine: AsyncEngine, git_dir: Path, rev: str, *, name: str) -> IndexReport:
    """Index a commit straight from git objects, e.g. a GitHub repo's bare
    mirror at its default branch: no checkout. The repo row must already
    exist (GitHub repos are created from webhook events)."""
    async with _repo_lock(engine, name):
        started = time.perf_counter()
        try:
            commit_sha = await asyncio.to_thread(git.resolve, git_dir, rev)
            result = await asyncio.to_thread(parse_revision, git_dir, commit_sha)
        except git.GitError as exc:
            raise IndexingError(str(exc)) from exc
        parse_seconds = time.perf_counter() - started
        report = await _index_locked(engine, name, result, commit_sha, None)
    report.parse_seconds = parse_seconds
    return report


@asynccontextmanager
async def _repo_lock(engine: AsyncEngine, full_name: str) -> AsyncIterator[None]:
    """A session-level advisory lock, held on its own connection for the whole
    run, so two indexers never interleave writes for the same repo. It is taken
    before parsing: a run that can't get it gives up at once, instead of parsing
    the whole repo and then throwing the result away, which a retried job used
    to do up to 61 times (D130)."""
    async with engine.connect() as lock_conn:
        locked = await lock_conn.scalar(
            text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": f"index:{full_name}"}
        )
        if not locked:
            raise IndexLocked(f"another index run for {full_name} is in progress")
        try:
            yield
        finally:
            await lock_conn.execute(
                text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": f"index:{full_name}"}
            )


async def _index_locked(
    engine: AsyncEngine,
    full_name: str,
    result: ParseResult,
    commit_sha: str | None,
    local_path: Path | None,
) -> IndexReport:
    async with engine.begin() as conn:
        repo_id = await _get_or_create_repo(conn, full_name, local_path)
        await conn.execute(
            update(repos_t)
            .where(repos_t.c.id == repo_id)
            .values(status=RepoStatus.INDEXING, updated_at=func.now())
        )
        run_id = await conn.scalar(
            insert(runs_t).values(repo_id=repo_id, commit_sha=commit_sha).returning(runs_t.c.id)
        )

    report = IndexReport(repo_id, full_name, run_id, commit_sha, summarize(result))
    started = time.perf_counter()
    try:
        await _write_graph(engine, repo_id, result, report)
    except Exception as exc:
        async with engine.begin() as conn:
            await _finish(conn, report, IndexRunStatus.FAILED, RepoStatus.FAILED, error=repr(exc))
        raise
    report.write_seconds = time.perf_counter() - started

    async with engine.begin() as conn:
        report.db_totals = dict(await count_graph(conn, repo_id))
        await _finish(conn, report, IndexRunStatus.SUCCEEDED, RepoStatus.READY)
    return report


async def _write_graph(
    engine: AsyncEngine, repo_id: int, result: ParseResult, report: IndexReport
) -> None:
    edges_by_source: dict[NodeRef, list[ParsedEdge]] = defaultdict(list)
    for edge in result.edges:
        edges_by_source[edge.source].append(edge)

    # One connection for the whole write phase (with NullPool, every
    # engine.begin() would open a fresh TCP connection), but still one
    # *transaction* per file: a crash leaves each file fully old or fully new,
    # and the next run finishes the job.
    async with engine.connect() as conn:
        stored = await load_files(conn, repo_id)
        id_map = await load_id_map(conn, repo_id)
        await conn.commit()

        # Files that vanished (or became unindexable) leave the index.
        # Transient skips keep their previous rows.
        keep = {f.path for f in result.files} | {
            s.path for s in result.skipped if s.reason in _TRANSIENT_SKIPS
        }
        gone = sorted(set(stored) - keep)
        if gone:
            async with _write_transaction(conn):
                report.nodes_deleted_with_files = await delete_files(conn, repo_id, gone, id_map)
            report.files_deleted = len(gone)

        totals = report.writes
        for parsed in result.files:
            previous = stored.get(parsed.path)
            if previous is not None and previous.content_hash == parsed.content_hash:
                report.files_unchanged += 1  # the idempotency guarantee: no writes at all
                continue
            file_edges = [e for n in parsed.nodes for e in edges_by_source.get(n.ref, ())]
            async with _write_transaction(conn):
                written = await write_file(conn, repo_id, parsed, file_edges, previous, id_map)
            _accumulate(totals, written)
            if previous is None:
                report.files_new += 1
            else:
                report.files_changed += 1

        # After a bulk write, refresh statistics *before* re-linking: the re-link
        # is the one query whose plan depends on knowing the tables are big now.
        rows_written = totals.nodes_inserted + totals.nodes_updated + totals.edges_written
        if rows_written >= _ANALYZE_THRESHOLD_ROWS:
            async with conn.begin():
                await analyze_graph_tables(conn)

        async with _write_transaction(conn):
            report.edges_relinked = await relink_edges(conn, repo_id)


@asynccontextmanager
async def _write_transaction(conn: AsyncConnection) -> AsyncIterator[None]:
    """A transaction whose COMMIT doesn't wait for the WAL to reach disk.

    Indexing commits once per file, and on Docker/WSL each durable flush costs
    tens of ms. With synchronous_commit off, a Postgres crash can lose the last
    few commits, but never corrupts anything: a lost commit just means the
    file still has its old hash, so the next run redoes it. The setting is
    LOCAL, so it ends with the transaction.
    """
    async with conn.begin():
        await conn.execute(text("SET LOCAL synchronous_commit = off"))
        yield


async def _get_or_create_repo(conn: AsyncConnection, full_name: str, root: Path | None) -> int:
    row = (
        await conn.execute(
            select(repos_t.c.id, repos_t.c.local_path).where(repos_t.c.full_name == full_name)
        )
    ).first()
    if root is None:  # indexing a revision: the repo row comes from elsewhere
        if row is None:
            raise IndexingError(f"no repo named {full_name!r}")
        return row.id
    if row is not None:
        if row.local_path is not None and row.local_path != str(root):
            raise IndexingError(
                f"{full_name!r} is already indexed from {row.local_path}; "
                "pass --name to index this path under a different name"
            )
        return row.id
    return await conn.scalar(
        insert(repos_t)
        .values(full_name=full_name, source=RepoSource.LOCAL, local_path=str(root))
        .returning(repos_t.c.id)
    )


async def _finish(
    conn: AsyncConnection,
    report: IndexReport,
    run_status: IndexRunStatus,
    repo_status: RepoStatus,
    *,
    error: str | None = None,
) -> None:
    await conn.execute(
        update(runs_t)
        .where(runs_t.c.id == report.index_run_id)
        .values(status=run_status, finished_at=func.now(), stats=report.to_json(), error=error)
    )
    values: dict[str, Any] = {"status": repo_status, "updated_at": func.now()}
    if run_status is IndexRunStatus.SUCCEEDED:
        values["last_indexed_sha"] = report.commit_sha
    await conn.execute(update(repos_t).where(repos_t.c.id == report.repo_id).values(**values))


def _resolve_and_parse(root: Path) -> tuple[Path, ParseResult]:
    root = root.resolve()
    if not root.is_dir():
        raise IndexingError(f"not a directory: {root}")
    return root, parse_repository(root)


def _accumulate(total: FileWrite, add: FileWrite) -> None:
    for name in FileWrite.__slots__:
        setattr(total, name, getattr(total, name) + getattr(add, name))


def _git_head(root: Path) -> str | None:
    """Commit SHA of the working tree, if it is a git checkout."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


__all__ = ["IndexLocked", "IndexReport", "IndexingError", "index_repository", "index_revision"]
