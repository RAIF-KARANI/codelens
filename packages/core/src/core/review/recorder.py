"""S10 persist: the review row, its event timeline, the context trace, findings.

Each write is its own short transaction on one connection: events become
visible as they happen, and a crash mid-review leaves a `running`/`failed`
review with everything recorded so far.

After each event is committed, an optional `notify(review_id)` rings the
doorbell for anyone streaming the review (the worker publishes on Redis;
core.review.events). The event itself is only ever read from the database.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sqlalchemy import delete, func, insert, select, update
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

logger = logging.getLogger(__name__)

Notify = Callable[[int], Awaitable[None]]  # review_id -> "it has new events"


async def ring(notify: Notify | None, review_id: int) -> None:
    """Tell streamers a review has new events. Best effort: a lost doorbell
    only delays a live viewer until their next poll, so it must never fail
    the review."""
    if notify is None:
        return
    try:
        await notify(review_id)
    except Exception:  # Redis down, ...: the event is safe in the database
        logger.warning("could not notify streamers of review %s", review_id, exc_info=True)


class ReviewRecorder:
    def __init__(self, conn: AsyncConnection, notify: Notify | None = None) -> None:
        self._conn = conn
        self._notify = notify
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
        review_id: int | None = None,
    ) -> int:
        """Insert a running review, or claim `review_id`, one the API queued."""
        values = {
            "base_sha": base_sha,
            "head_sha": head_sha,
            "model": model,
            "status": ReviewStatus.RUNNING,
            "started_at": func.now(),
            "pipeline_config": config.model_dump(mode="json"),
        }
        async with self._conn.begin():
            if review_id is None:
                self.review_id = await self._conn.scalar(
                    insert(reviews_t)
                    .values(repo_id=repo_id, source=source, **values)
                    .returning(reviews_t.c.id)
                )
                return self.review_id
            # A `running` review here is a retry after a crashed attempt (the
            # worker acks late, so the broker redelivers). Its partial timeline,
            # trace and findings are cleared so the review restarts cleanly and
            # event seq numbers start again at 1.
            for table in (events_t, items_t, findings_t):
                await self._conn.execute(delete(table).where(table.c.review_id == review_id))
            self.review_id = await self._conn.scalar(
                update(reviews_t)
                .where(
                    reviews_t.c.id == review_id,
                    reviews_t.c.repo_id == repo_id,
                    reviews_t.c.status.in_([ReviewStatus.QUEUED, ReviewStatus.RUNNING]),
                )
                .values(finished_at=None, error=None, **values)
                .returning(reviews_t.c.id)
            )
            if self.review_id is None:
                raise ValueError(f"review {review_id} is not a queued review of repo {repo_id}")
        return self.review_id

    async def event(self, type_: str, **payload: Any) -> None:
        self._seq += 1
        async with self._conn.begin():
            await self._conn.execute(
                insert(events_t).values(
                    review_id=self.review_id, seq=self._seq, type=type_, payload=payload
                )
            )
        if self.review_id is not None:
            await ring(self._notify, self.review_id)

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
                "topic": c.topic,
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


async def append_event(conn: AsyncConnection, review_id: int, type_: str, **payload: Any) -> None:
    """Add an event after the pipeline's own recorder is done (e.g. the worker
    posting the review to GitHub): next seq after the last one. The caller
    commits."""
    seq = await conn.scalar(
        select(func.coalesce(func.max(events_t.c.seq), 0) + 1).where(
            events_t.c.review_id == review_id
        )
    )
    await conn.execute(
        insert(events_t).values(review_id=review_id, seq=seq, type=type_, payload=payload)
    )


async def fail_unfinished(conn: AsyncConnection, review_id: int, error: str) -> bool:
    """Mark a queued or running review failed. For errors raised before the
    pipeline could record its own end (a bad revision, a missing repo). A review
    that already finished is left alone. Returns whether a row changed."""
    async with conn.begin():
        changed = await conn.scalar(
            update(reviews_t)
            .where(
                reviews_t.c.id == review_id,
                reviews_t.c.status.in_([ReviewStatus.QUEUED, ReviewStatus.RUNNING]),
            )
            .values(status=ReviewStatus.FAILED, finished_at=func.now(), error=error)
            .returning(reviews_t.c.id)
        )
    return changed is not None
