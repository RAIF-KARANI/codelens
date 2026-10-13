"""The call graph around a review, for the dashboard's graph view (React Flow).

Nodes:
- **the review's seeds:** the changed functions and classes, stored with the
  review (D110). A seed that isn't in the stored graph (an added function,
  since the graph is indexed at the base) is still shown, as a node of its own;
- **their callers and callees,** one hop over `calls` and `inherits` edges,
  at most MAX_NEIGHBORS per seed and direction (hub control, idea.md §7.5);
- **every node the model was shown as context,** marked `in_context`.

Edges are the stored `calls` / `inherits` edges between those nodes.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import schemas
from core.db import models
from core.enums import EdgeKind

MAX_NEIGHBORS = 15
_KINDS = (EdgeKind.CALLS, EdgeKind.INHERITS)


async def review_graph(session: AsyncSession, review: models.Review) -> schemas.ReviewGraph:
    seeds: list[dict[str, Any]] = review.seeds or []
    nodes: dict[str, schemas.GraphNode] = {}

    stored = await _nodes(
        session,
        and_(
            models.CodeNode.repo_id == review.repo_id,
            models.CodeNode.qualified_name.in_({s["qualified_name"] for s in seeds}),
        ),
    )
    by_name = {row.qualified_name: row for row in stored}
    seed_ids: list[int] = []
    for seed in seeds:
        row = by_name.get(seed["qualified_name"])
        if row is None:  # not in the indexed graph yet, e.g. a new function
            key = f"seed:{seed['qualified_name']}"
            nodes[key] = schemas.GraphNode(
                id=key,
                node_id=None,
                label=seed["qualified_name"].rsplit(".", 1)[-1].rsplit(":", 1)[-1],
                qualified_name=seed["qualified_name"],
                kind=seed["kind"],
                path=seed["path"],
                is_seed=True,
                change=seed["change"],
                depth=0,
                in_context=False,
            )
            continue
        seed_ids.append(row.id)
        nodes[f"n{row.id}"] = _node(row, is_seed=True, change=seed["change"], depth=0)

    for row in await _neighbours(session, seed_ids):
        nodes.setdefault(f"n{row.id}", _node(row, is_seed=False, change=None, depth=1))

    context_ids = set(
        await session.scalars(
            select(models.ReviewContextItem.node_id).where(
                models.ReviewContextItem.review_id == review.id,
                models.ReviewContextItem.included,
                models.ReviewContextItem.node_id.is_not(None),
            )
        )
    )
    missing = [i for i in context_ids if f"n{i}" not in nodes]
    for row in await _nodes(session, models.CodeNode.id.in_(missing)) if missing else []:
        nodes[f"n{row.id}"] = _node(row, is_seed=False, change=None, depth=None)
    for node_id in context_ids:
        if (node := nodes.get(f"n{node_id}")) is not None:
            node.in_context = True

    ids = [n.node_id for n in nodes.values() if n.node_id is not None]
    edges = await session.execute(
        select(models.CodeEdge.src_node_id, models.CodeEdge.dst_node_id, models.CodeEdge.kind)
        .where(
            models.CodeEdge.src_node_id.in_(ids),
            models.CodeEdge.dst_node_id.in_(ids),
            models.CodeEdge.kind.in_(_KINDS),
        )
        .distinct()
    )
    return schemas.ReviewGraph(
        review_id=review.id,
        nodes=list(nodes.values()),
        edges=[
            schemas.GraphEdge(source=f"n{src}", target=f"n{dst}", kind=kind.value)
            for src, dst, kind in edges
        ],
    )


async def _neighbours(session: AsyncSession, seed_ids: Sequence[int]) -> list[Any]:
    """Callers and callees of the seeds, at most MAX_NEIGHBORS per seed and
    direction. Ranked by id for now; by centrality once U4 computes it."""
    if not seed_ids:
        return []
    e = models.CodeEdge
    rows = []
    for seed_col, other_col in ((e.dst_node_id, e.src_node_id), (e.src_node_id, e.dst_node_id)):
        # Distinct pairs first (a caller can both call and inherit), then rank:
        # ranking before DISTINCT would let duplicates use up the cap.
        pairs = (
            select(seed_col.label("seed"), other_col.label("other"))
            .where(seed_col.in_(seed_ids), other_col.is_not(None), e.kind.in_(_KINDS))
            .distinct()
            .subquery()
        )
        ranked = select(
            pairs.c.other,
            func.row_number().over(partition_by=pairs.c.seed, order_by=pairs.c.other).label("rank"),
        ).subquery()
        ids = select(ranked.c.other).where(ranked.c.rank <= MAX_NEIGHBORS)
        rows += await _nodes(session, models.CodeNode.id.in_(ids))
    return rows


async def _nodes(session: AsyncSession, where: Any) -> list[Any]:
    result = await session.execute(
        select(
            models.CodeNode.id,
            models.CodeNode.name,
            models.CodeNode.qualified_name,
            models.CodeNode.kind,
            models.File.path,
        )
        .join(models.File, models.File.id == models.CodeNode.file_id)
        .where(where)
    )
    return list(result)


def _node(row: Any, *, is_seed: bool, change: str | None, depth: int | None) -> schemas.GraphNode:
    return schemas.GraphNode(
        id=f"n{row.id}",
        node_id=row.id,
        label=row.name,
        qualified_name=row.qualified_name,
        kind=row.kind.value,
        path=row.path,
        is_seed=is_seed,
        change=change,
        depth=depth,
        in_context=False,
    )
