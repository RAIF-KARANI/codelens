"""S3 retrieve (B0): for each seed, the stored nodes nearest in meaning.

One embed call for all seed queries, then one vector search per seed. Every
seed's own stored node is excluded: the LLM already sees the changed code,
and the stored copy is the *old* version.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from core.db.models import CodeNode, File
from core.enums import NodeKind
from core.providers import Embedder, EmbedKind
from core.retrieval import SearchHit, vector_search
from core.review.config import RetrievalConfig
from core.review.seeds import Seed
from core.text.tokens import chars_for_tokens

nodes_t = CodeNode.__table__
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
            )
            for h in hits
        ]
        for index, hits in hits_per_seed.items()
    }


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
    )
