"""S3 retrieve (B0): for each seed, the stored nodes nearest in meaning.

One embed call for all seed queries, then one vector search per seed. Every
seed's own stored node is excluded: the LLM already sees the changed code,
and the stored copy is the *old* version.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from core.db.models import CodeEdge, CodeNode, File
from core.enums import EdgeKind, NodeKind
from core.providers import Embedder, EmbedKind
from core.retrieval import SearchHit, vector_search
from core.review.config import RetrievalConfig
from core.review.seeds import Seed
from core.text.tokens import chars_for_tokens

nodes_t = CodeNode.__table__
edges_t = CodeEdge.__table__
files_t = File.__table__

_QUERY_MAX_TOKENS = 1024


@dataclass(frozen=True, slots=True)
class Candidate:
    node_id: int
    qualified_name: str
    ordinal: int
    kind: NodeKind
    path: str
    start_line: int
    end_line: int
    signature: str | None
    code: str
    similarity: float  # best over the seeds that retrieved it
    rank: int  # best rank over those seeds
    seeds: tuple[str, ...]  # qualified names of the seeds that retrieved it
    relation: str | None = None  # "calls the changed code" / "called by the changed code"

    @property
    def label(self) -> str:
        """The id the prompt uses, and the LLM cites as evidence."""
        return f"N{self.node_id}"


def select_seeds(seeds: Sequence[Seed], max_seeds: int) -> list[Seed]:
    """Biggest changes first when there are too many (planning.md §6.1)."""
    ranked = sorted(seeds, key=lambda s: (-s.change_size, s.path, s.node.start_line))
    return ranked[:max_seeds]


def seed_query(seed: Seed) -> str:
    """What a seed is searched with: its signature + code (head version, or
    the deleted base version), capped like a chunk."""
    node = seed.node
    text = f"{node.signature or node.name}\n{node.code}"
    return text[: chars_for_tokens(_QUERY_MAX_TOKENS)]


async def retrieve_for_seeds(
    conn: AsyncConnection,
    embedder: Embedder,
    *,
    repo_id: int,
    seeds: Sequence[Seed],
    config: RetrievalConfig,
) -> dict[int, list[Candidate]]:
    """seed index (into `seeds`) -> its candidates, best first."""
    if not config.enabled or config.vector_k == 0 or not seeds:
        return {}

    names = sorted({s.node.qualified_name for s in seeds})
    seed_ids = {
        row.id
        for row in await conn.execute(
            select(nodes_t.c.id).where(
                nodes_t.c.repo_id == repo_id, nodes_t.c.qualified_name.in_(names)
            )
        )
    }
    embedded = await embedder.embed([seed_query(s) for s in seeds], kind=EmbedKind.QUERY)

    hits_per_seed: dict[int, list[SearchHit]] = {}
    for index, vector in enumerate(embedded.vectors):
        hits_per_seed[index] = await vector_search(
            conn,
            repo_id=repo_id,
            query_vector=vector,
            k=config.vector_k,
            exclude_node_ids=seed_ids,
        )

    all_ids = {hit.node_id for hits in hits_per_seed.values() for hit in hits}
    relation = await _relations(conn, seed_ids, all_ids) if config.tag_relations else {}
    code = {
        row.id: row.code
        for row in await conn.execute(
            select(nodes_t.c.id, nodes_t.c.code).where(nodes_t.c.id.in_(all_ids))
        )
    }

    return {
        index: [
            Candidate(
                node_id=h.node_id,
                qualified_name=h.qualified_name,
                ordinal=h.ordinal,
                kind=h.kind,
                path=h.path,
                start_line=h.start_line,
                end_line=h.end_line,
                signature=h.signature,
                code=code.get(h.node_id, ""),
                similarity=h.similarity,
                rank=h.rank,
                seeds=(seeds[index].node.qualified_name,),
                relation=relation.get(h.node_id),
            )
            for h in hits
        ]
        for index, hits in hits_per_seed.items()
    }


async def _relations(
    conn: AsyncConnection, seed_ids: set[int], candidate_ids: set[int]
) -> dict[int, str]:
    """How each candidate relates to the changed code through `calls` edges.
    A caller is the more useful fact (it shows whether a change breaks it),
    so it wins when a node is both."""
    if not seed_ids or not candidate_ids:
        return {}
    rows = await conn.execute(
        select(edges_t.c.src_node_id, edges_t.c.dst_node_id).where(
            edges_t.c.kind == EdgeKind.CALLS,
            or_(
                and_(
                    edges_t.c.src_node_id.in_(candidate_ids),
                    edges_t.c.dst_node_id.in_(seed_ids),
                ),
                and_(
                    edges_t.c.src_node_id.in_(seed_ids),
                    edges_t.c.dst_node_id.in_(candidate_ids),
                ),
            ),
        )
    )
    found: dict[int, str] = {}
    for src, dst in rows:
        if src in candidate_ids and dst in seed_ids:
            found[src] = "calls the changed code"
        elif dst in candidate_ids:
            found.setdefault(dst, "called by the changed code")
    return found


def merge_candidates(lists: Sequence[Sequence[Candidate]]) -> list[Candidate]:
    """Union of several seeds' candidates: one entry per node, keeping its
    best similarity and rank and every seed that found it. Best first."""
    merged: dict[int, Candidate] = {}
    for candidates in lists:
        for c in candidates:
            prev = merged.get(c.node_id)
            merged[c.node_id] = c if prev is None else _merge(prev, c)
    return sorted(merged.values(), key=lambda c: (-c.similarity, c.node_id))


def _merge(a: Candidate, b: Candidate) -> Candidate:
    return Candidate(
        node_id=a.node_id,
        qualified_name=a.qualified_name,
        ordinal=a.ordinal,
        kind=a.kind,
        path=a.path,
        start_line=a.start_line,
        end_line=a.end_line,
        signature=a.signature,
        code=a.code,
        similarity=max(a.similarity, b.similarity),
        rank=min(a.rank, b.rank),
        seeds=tuple(dict.fromkeys(a.seeds + b.seeds)),
        relation=a.relation or b.relation,
    )
