"""Semantic search: nearest code nodes to a query vector (pgvector, cosine).

Two pgvector details matter here:

1. Filtered HNSW. The index finds the `ef_search` nearest vectors *first* and
   applies `WHERE repo_id = ...` *afterwards*. With many repos in one table, a
   search can come back with far fewer than k rows. pgvector >= 0.8 fixes
   this with iterative scans: keep scanning the graph until k rows survive
   the filter (`hnsw.iterative_scan = relaxed_order`).
2. `relaxed_order` may return rows slightly out of distance order (that's what
   makes it fast), so we re-sort by the exact distance we selected.

Both settings are `SET LOCAL`: they last for this transaction only.

Measured (planning.md D51): for a 17k-node table Postgres plans this as an
*exact* sequential scan with a top-N heapsort (25-90 ms), not the HNSW index,
because pgvector's cost estimate overstates the index at that size. Forcing
the index (`SET LOCAL enable_seqscan = off`) runs the same query in ~7 ms.
We keep the planner's choice for now, since exact results are the better
default while it's this cheap. The index takes over by itself as tables grow.
"""

from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from core.db.models import CodeNode, File
from core.enums import NodeKind

nodes_t = CodeNode.__table__
files_t = File.__table__

# Candidate-list size for the HNSW graph walk. pgvector's default is 40;
# larger means better recall for a little more time. Never below k.
DEFAULT_EF_SEARCH = 100


@dataclass(frozen=True, slots=True)
class SearchHit:
    rank: int  # 1-based
    node_id: int
    kind: NodeKind
    qualified_name: str
    ordinal: int
    path: str
    start_line: int
    end_line: int
    signature: str | None
    distance: float  # cosine distance: 0 = same direction, 2 = opposite

    @property
    def similarity(self) -> float:
        return 1.0 - self.distance


def vector_search_query(
    *,
    repo_id: int,
    query_vector: Sequence[float],
    k: int,
    exclude_node_ids: Collection[int] = (),
    kinds: Collection[NodeKind] | None = None,
) -> Select:  # type: ignore[type-arg]
    """The SELECT, separate so the CLI can EXPLAIN exactly what search runs."""
    distance = nodes_t.c.embedding.cosine_distance(list(query_vector)).label("distance")
    query = (
        select(
            nodes_t.c.id,
            nodes_t.c.kind,
            nodes_t.c.qualified_name,
            nodes_t.c.ordinal,
            files_t.c.path,
            nodes_t.c.start_line,
            nodes_t.c.end_line,
            nodes_t.c.signature,
            distance,
        )
        .join(files_t, files_t.c.id == nodes_t.c.file_id)
        .where(nodes_t.c.repo_id == repo_id, nodes_t.c.embedding.is_not(None))
        # ORDER BY the distance expression (not a re-computed one) is what lets
        # Postgres use the HNSW index for this query.
        .order_by(distance)
        .limit(k)
    )
    if exclude_node_ids:
        query = query.where(nodes_t.c.id.not_in(list(exclude_node_ids)))
    if kinds:
        query = query.where(nodes_t.c.kind.in_(list(kinds)))
    return query


@asynccontextmanager
async def _transaction(conn: AsyncConnection) -> AsyncIterator[None]:
    """Run in the caller's transaction if there is one, else in our own.

    SET LOCAL needs a transaction. A connection that has already executed a
    statement is mid-transaction (SQLAlchemy autobegins), and calling begin()
    again would raise. In that case the settings simply last until the
    caller's transaction ends, which is harmless: they only affect HNSW scans.
    """
    if conn.in_transaction():
        yield
    else:
        async with conn.begin():
            yield


async def configure_hnsw(conn: AsyncConnection, *, k: int, ef_search: int | None = None) -> None:
    """Per-transaction HNSW settings (call inside the search's transaction)."""
    ef = max(ef_search or DEFAULT_EF_SEARCH, k)
    await conn.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    # SET doesn't take bind parameters; ef is an int we computed, not user text.
    await conn.execute(text(f"SET LOCAL hnsw.ef_search = {int(ef)}"))


async def explain_vector_search(
    conn: AsyncConnection,
    *,
    repo_id: int,
    query_vector: Sequence[float],
    k: int,
    kinds: Collection[NodeKind] | None = None,
    analyze: bool = False,
) -> list[str]:
    """The Postgres plan for a search, e.g. to confirm the HNSW index is used.

    Compiled with literal values, because the asyncpg dialect's positional
    placeholders ($1, $2) can't be passed through a textual EXPLAIN. Safe: every
    value here is a number or an enum we produced, never free user text.
    """
    query = vector_search_query(repo_id=repo_id, query_vector=query_vector, k=k, kinds=kinds)
    sql = str(query.compile(dialect=conn.dialect, compile_kwargs={"literal_binds": True}))
    async with _transaction(conn):
        await configure_hnsw(conn, k=k)
        result = await conn.exec_driver_sql(f"EXPLAIN {'ANALYZE ' if analyze else ''}{sql}")
        return [row[0] for row in result]


async def vector_search(
    conn: AsyncConnection,
    *,
    repo_id: int,
    query_vector: Sequence[float],
    k: int = 10,
    exclude_node_ids: Collection[int] = (),
    kinds: Collection[NodeKind] | None = None,
    ef_search: int | None = None,
) -> list[SearchHit]:
    """Top-k nodes of one repo by cosine distance to `query_vector`.

    Nodes without an embedding (not yet run through core.embed) are skipped.
    """
    if k <= 0:
        return []
    query = vector_search_query(
        repo_id=repo_id,
        query_vector=query_vector,
        k=k,
        exclude_node_ids=exclude_node_ids,
        kinds=kinds,
    )
    async with _transaction(conn):
        await configure_hnsw(conn, k=k, ef_search=ef_search)
        rows = list(await conn.execute(query))

    rows.sort(key=lambda r: (r.distance, r.id))  # relaxed_order -> exact order
    return [
        SearchHit(
            rank=i,
            node_id=r.id,
            kind=r.kind,
            qualified_name=r.qualified_name,
            ordinal=r.ordinal,
            path=r.path,
            start_line=r.start_line,
            end_line=r.end_line,
            signature=r.signature,
            distance=float(r.distance),
        )
        for i, r in enumerate(rows, start=1)
    ]
