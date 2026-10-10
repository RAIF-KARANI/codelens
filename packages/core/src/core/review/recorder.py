"""S10 persist: the review row, its event timeline, the context trace, findings.

Each write is its own short transaction on one connection: events become
visible as they happen (Phase 5 streams them over SSE), and a crash mid-review
leaves a `running`/`failed` review with everything recorded so far.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, insert, update
from sqlalchemy.ext.asyncio import AsyncConnection

from core.db.models import Finding, Review, ReviewContextItem, ReviewEvent
from core.enums import ReviewSource, ReviewStatus
from core.providers import Usage
from core.review.config import PipelineConfig
from core.review.packing import ReviewChunk
from core.review.validate import CheckedFinding

reviews_t = Review.__table__
events_t = ReviewEvent.__table__
items_t = ReviewContextItem.__table__
findings_t = Finding.__table__


class ReviewRecorder:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn
        self._seq = 0
        self.review_id: int | None = None

    async def start(
        self,
        *,
        repo_id: int,
        source: ReviewSource,
        base_sha: str,
        head_sha: str,
        model: str,
        config: PipelineConfig,
    ) -> int:
        async with self._conn.begin():
            self.review_id = await self._conn.scalar(
                insert(reviews_t)
                .values(
                    repo_id=repo_id,
                    source=source,
                    base_sha=base_sha,
                    head_sha=head_sha,
                    model=model,
                    status=ReviewStatus.RUNNING,
                    started_at=func.now(),
                    pipeline_config=config.model_dump(mode="json"),
                )
                .returning(reviews_t.c.id)
            )
        return self.review_id

    async def event(self, type_: str, **payload: Any) -> None:
        self._seq += 1
        async with self._conn.begin():
            await self._conn.execute(
                insert(events_t).values(
                    review_id=self.review_id, seq=self._seq, type=type_, payload=payload
                )
            )

    async def context(self, chunks: Sequence[ReviewChunk]) -> None:
        rows = [
            {
                "review_id": self.review_id,
                "chunk_index": chunk.index,
                "node_id": item.candidate.node_id,
                "label": item.candidate.label,
                "qualified_name": item.candidate.qualified_name,
                "stage_scores": {
                    "vector_similarity": round(item.candidate.similarity, 4),
                    "vector_rank": item.candidate.rank,
                    "retrieved_for": list(item.candidate.seeds),
                    **({"excluded": item.reason} if item.reason else {}),
                },
                "included": item.included,
                "truncated": item.truncated,
                "token_cost": item.tokens,
            }
            for chunk in chunks
            for item in chunk.trace
        ]
        if rows:
            async with self._conn.begin():
                await self._conn.execute(insert(items_t), rows)

    async def findings(self, checked: Sequence[CheckedFinding]) -> None:
        rows = [
            {
                "review_id": self.review_id,
                "file_path": c.finding.file_path,
                "line_start": c.line_start,
                "line_end": c.line_end,
                "severity": c.finding.severity,
                "category": c.finding.category.value,
                "title": c.finding.title,
                "explanation": c.finding.explanation,
                "suggested_fix": c.finding.suggested_fix,
                "evidence_node_ids": list(c.evidence_node_ids),
                "confidence": c.finding.confidence,
                "fingerprint": c.fingerprint,
                "status": c.status,
                "drop_reason": c.drop_reason,
            }
            for c in checked
        ]
        if rows:
            async with self._conn.begin():
                await self._conn.execute(insert(findings_t), rows)

    async def finish(self, status: ReviewStatus, usage: Usage, *, error: str | None = None) -> None:
        async with self._conn.begin():
            await self._conn.execute(
                update(reviews_t)
                .where(reviews_t.c.id == self.review_id)
                .values(
                    status=status,
                    finished_at=func.now(),
                    error=error,
                    token_usage={
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "llm_ms": round(usage.duration_ms),
                    },
                )
            )
