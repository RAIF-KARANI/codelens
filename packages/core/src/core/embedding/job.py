"""Embed a repository's code nodes: only the ones whose vector is stale.

A node is (re-)embedded when any of these hold:
  - it has no embedding yet (a new node)
  - its embedding was made by a different model
  - the chunk text we'd embed now differs from what was embedded: its code,
    its header (path, enclosing class), its skeleton, or the chunk format
    version changed

The chunk-text hash is the single test of staleness (planning.md D47). The
indexer leaves old vectors in place, because many code edits don't change a
chunk (a module's skeleton shows only signatures).

The last check means every run rebuilds every chunk text to hash it. That's
cheap (string work over rows already in memory) and makes re-runs exact: an
up-to-date repo produces zero writes.
"""

import hashlib
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import bindparam, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.db.models import CodeEdge, CodeNode, File, Repo
from core.enums import EdgeKind
from core.providers import Embedder, EmbedKind
from core.text import Chunk, ChunkSource, NodeInfo, build_chunk

nodes_t = CodeNode.__table__
edges_t = CodeEdge.__table__
files_t = File.__table__
repos_t = Repo.__table__


class EmbeddingError(Exception):
    """A user-facing error (unknown repo, ...)."""


@dataclass(slots=True)
class EmbedReport:
    repo_name: str
    model: str
    nodes_total: int = 0
    up_to_date: int = 0
    embedded: int = 0
    truncated: int = 0
    input_tokens: int = 0
    seconds: float = 0.0

    @property
    def nodes_per_second(self) -> float:
        return self.embedded / self.seconds if self.seconds else 0.0


@dataclass(frozen=True, slots=True)
class _Pending:
    node_id: int
    chunk: Chunk
    text_hash: str


ProgressFn = Callable[[int, int], None]  # (done, total)


async def embed_repository(
    engine: AsyncEngine,
    embedder: Embedder,
    repo_name: str,
    *,
    max_tokens: int,
    page_size: int = 256,
    limit: int | None = None,
    progress: ProgressFn | None = None,
) -> EmbedReport:
    report = EmbedReport(repo_name, embedder.model)
    async with engine.connect() as conn:
        repo_id = await conn.scalar(select(repos_t.c.id).where(repos_t.c.full_name == repo_name))
        if repo_id is None:
            raise EmbeddingError(f"no indexed repo named {repo_name!r}; run core.index first")
        rows, sources = await _load_chunk_sources(conn, repo_id)

    pending: list[_Pending] = []
    for row in rows:
        chunk = build_chunk(sources[row.id], max_tokens=max_tokens)
        text_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        stale = (
            row.missing or row.embedding_model != embedder.model or row.embedded_hash != text_hash
        )
        if stale:
            pending.append(_Pending(row.id, chunk, text_hash))
    report.nodes_total = len(rows)
    report.up_to_date = len(rows) - len(pending)
    if limit is not None:
        pending = pending[:limit]

    started = time.perf_counter()
    # One connection, one transaction per page: an interrupted run keeps every
    # finished page, and the next run picks up exactly the remaining nodes.
    async with engine.connect() as conn:
        for start in range(0, len(pending), page_size):
            page = pending[start : start + page_size]
            result = await embedder.embed([p.chunk.text for p in page], kind=EmbedKind.DOCUMENT)
            async with conn.begin():
                await _write_page(conn, page, result.vectors, embedder.model)
            report.embedded += len(page)
            report.truncated += sum(p.chunk.truncated for p in page)
            report.input_tokens += result.usage.input_tokens
            if progress is not None:
                progress(report.embedded, len(pending))
    report.seconds = time.perf_counter() - started
    return report


async def _write_page(
    conn: AsyncConnection, page: Sequence[_Pending], vectors: Sequence[list[float]], model: str
) -> None:
    await conn.execute(
        update(nodes_t)
        .where(nodes_t.c.id == bindparam("b_id"))
        .values(
            embedding=bindparam("b_embedding"),
            embedding_model=bindparam("b_model"),
            embedded_hash=bindparam("b_hash"),
        ),
        [
            {"b_id": p.node_id, "b_embedding": v, "b_model": model, "b_hash": p.text_hash}
            for p, v in zip(page, vectors, strict=True)
        ],
    )


async def _load_chunk_sources(
    conn: AsyncConnection, repo_id: int
) -> tuple[list, dict[int, ChunkSource]]:  # type: ignore[type-arg]
    """Every node of the repo with what its chunk needs: path, parent, children."""
    rows = list(
        await conn.execute(
            select(
                nodes_t.c.id,
                nodes_t.c.kind,
                nodes_t.c.name,
                nodes_t.c.qualified_name,
                nodes_t.c.signature,
                nodes_t.c.start_line,
                nodes_t.c.end_line,
                nodes_t.c.code,
                files_t.c.path,
                nodes_t.c.embedding.is_(None).label("missing"),
                nodes_t.c.embedding_model,
                nodes_t.c.embedded_hash,
            )
            .join(files_t, files_t.c.id == nodes_t.c.file_id)
            .where(nodes_t.c.repo_id == repo_id)
            .order_by(nodes_t.c.id)
        )
    )
    contains = await conn.execute(
        select(edges_t.c.src_node_id, edges_t.c.dst_node_id).where(
            edges_t.c.repo_id == repo_id,
            edges_t.c.kind == EdgeKind.CONTAINS,
            edges_t.c.dst_node_id.is_not(None),
        )
    )
    parent_of: dict[int, int] = {}
    children_of: dict[int, list[int]] = defaultdict(list)
    for src, dst in contains:
        parent_of[dst] = src
        children_of[src].append(dst)

    info = {
        r.id: NodeInfo(r.kind, r.name, r.qualified_name, r.signature, r.start_line, r.end_line)
        for r in rows
    }
    sources = {
        r.id: ChunkSource(
            node=info[r.id],
            path=r.path,
            code=r.code,
            parent=info.get(parent_of[r.id]) if r.id in parent_of else None,
            children=tuple(info[c] for c in children_of.get(r.id, ()) if c in info),
        )
        for r in rows
    }
    return rows, sources
