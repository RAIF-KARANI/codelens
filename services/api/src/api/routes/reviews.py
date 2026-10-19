"""Reviews: queue a manual review; read one with its findings, diff, trace,
graph and live events. Every route sees only the reviews of repos the caller
may see (api/access.py, D111)."""

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from api import access, schemas
from api.auth import User, current_user, require_writable
from api.dependencies import get_engine, get_events_redis, get_queue, get_session
from api.errors import ApiError
from api.graph import review_graph
from api.queries import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    page_of,
    review_summaries,
    severity_counts,
    votes,
)
from api.queue import JobQueue, QueueUnavailable
from api.sse import review_stream
from core.db import models
from core.diff import git
from core.enums import FindingStatus, RepoSource, ReviewSource, ReviewStatus, Severity
from core.github import retry

# Router-level too: auth runs before any other validation (cached per request).
router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"], dependencies=[Depends(current_user)])

Session = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(current_user)]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.SUGGESTION: 2}


@router.get("")
async def list_reviews(
    session: Session,
    user: CurrentUser,
    status_: Annotated[ReviewStatus | None, Query(alias="status")] = None,
    include_eval: bool = False,
    cursor: int | None = None,
    limit: Limit = DEFAULT_LIMIT,
) -> schemas.Page[schemas.ReviewListItem]:
    """Reviews across every repo the caller may see, newest first: the
    dashboard's Reviews page and command palette. Eval runs are left out unless
    asked for: they are measurements, hundreds per run, not reviews anyone
    waits for (D155); an eval repo's own list still has them."""
    query = (
        select(models.Review, models.Repo.full_name)
        .join(models.Repo, models.Repo.id == models.Review.repo_id)
        .where(access.visible(user))
    )
    if not include_eval:
        query = query.where(models.Review.source != ReviewSource.EVAL)
    if status_ is not None:
        query = query.where(models.Review.status == status_)
    if cursor is not None:
        query = query.where(models.Review.id < cursor)
    rows = (await session.execute(query.order_by(models.Review.id.desc()).limit(limit + 1))).all()
    names = {review.id: name for review, name in rows}
    items, next_cursor = page_of([review for review, _ in rows], limit)
    return schemas.Page(
        items=[
            schemas.ReviewListItem(**summary.model_dump(), repo_full_name=names[summary.id])
            for summary in await review_summaries(session, items)
        ],
        next_cursor=next_cursor,
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_writable)])
async def create_review(
    body: schemas.ReviewCreate,
    session: Session,
    user: CurrentUser,
    queue: Annotated[JobQueue, Depends(get_queue)],
) -> schemas.ReviewQueued:
    """Queue a review of `head` against its merge-base with `base`, in a repo
    indexed from a local checkout. Follow it with GET /reviews/{id}."""
    repo = await access.repo_for(session, user, body.repo_id)
    if repo.source is not RepoSource.LOCAL or not repo.local_path:
        raise ApiError(409, "unsupported_source", f"{repo.full_name} has no local checkout")
    # git runs subprocesses: off the event loop.
    head_sha, base_sha = await asyncio.to_thread(
        _resolve_refs, Path(repo.local_path), body.head, body.base
    )
    review = models.Review(
        repo_id=repo.id,
        source=ReviewSource.CLI,
        title=body.head,  # the branch: what the change is called
        head_sha=head_sha,
        status=ReviewStatus.QUEUED,
    )
    session.add(review)
    # Commit before enqueueing, or the worker could pick the job up before the
    # row exists.
    await session.commit()
    try:
        await queue.review(review.id, base_sha)
    except QueueUnavailable:
        # Keep the history honest: the review was asked for and never ran.
        review.status = ReviewStatus.FAILED
        review.finished_at = func.now()
        review.error = "could not enqueue: the job queue is unavailable"
        await session.commit()
        raise
    return schemas.ReviewQueued(review_id=review.id, status=review.status, head_sha=head_sha)


@router.post(
    "/{review_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_writable)],
)
async def retry_review(
    review_id: int,
    session: Session,
    user: CurrentUser,
    queue: Annotated[JobQueue, Depends(get_queue)],
) -> schemas.ReviewQueued:
    """Review a failed pull request review's commit again: the dashboard's
    Review again button (D181). A new review, like a push; the failed one stays.
    Only the PR's newest review, and only one that failed."""
    await access.review_for(session, user, review_id)
    try:
        again = await retry.retry_review(await session.connection(), review_id)
    except retry.RetryRefused as refused:
        raise ApiError(
            404 if refused.code == "not_found" else 409, refused.code, str(refused)
        ) from refused
    # Commit before enqueueing, or the worker could pick the job up first.
    await session.commit()
    review = await session.get_one(models.Review, again.review_id)
    try:
        await queue.review(again.review_id, again.base_sha, title=again.title)
    except QueueUnavailable:
        review.status = ReviewStatus.FAILED
        review.finished_at = func.now()
        review.error = "could not enqueue: the job queue is unavailable"
        await session.commit()
        raise
    return schemas.ReviewQueued(review_id=review.id, status=review.status, head_sha=review.head_sha)


def _resolve_refs(checkout: Path, head: str, base: str) -> tuple[str, str]:
    """(head_sha, base_sha). Resolving now means a typo fails here as a 422, not
    later as a failed review. It also pins the review to the commits that
    existed when it was asked for, even if the branches move while it waits."""
    if not checkout.is_dir():
        raise ApiError(409, "checkout_missing", "the repo's local checkout is gone")
    try:
        return git.resolve(checkout, head), git.resolve(checkout, base)
    except git.GitError as exc:
        raise ApiError(422, "invalid_revision", str(exc)) from exc


@router.get("/{review_id}")
async def get_review(
    review_id: int, session: Session, user: CurrentUser, include_dropped: bool = False
) -> schemas.ReviewDetailWithVotes:
    """A review with its findings: posted ones by default, and with
    `include_dropped=true` also those validation rejected, with the reason.
    Each finding carries its 👍/👎 counts and the caller's own vote."""
    review = await access.review_for(session, user, review_id)
    query = select(models.Finding).where(models.Finding.review_id == review_id)
    if not include_dropped:
        query = query.where(models.Finding.status == FindingStatus.POSTED)
    findings = sorted(
        (await session.scalars(query)).all(),
        key=lambda f: (
            f.status is not FindingStatus.POSTED,
            _SEVERITY_ORDER[f.severity],
            f.file_path,
            f.line_start,
        ),
    )
    counts = await severity_counts(session, [review_id])
    tallies, mine = await votes(session, [f.id for f in findings], user.github_id)
    return schemas.ReviewDetailWithVotes(
        **schemas.ReviewSummary.model_validate(review).model_dump(exclude={"counts"}),
        counts=counts[review_id],
        token_usage=review.token_usage,
        pipeline_config=review.pipeline_config,
        findings=[
            schemas.FindingWithVotes.model_validate(f).model_copy(
                update={"votes": tallies[f.id], "my_vote": mine.get(f.id)}
            )
            for f in findings
        ],
    )


@router.get("/{review_id}/diff")
async def get_review_diff(
    review_id: int, session: Session, user: CurrentUser
) -> schemas.ReviewDiff:
    """What was reviewed: the unified diff and the changed nodes, as stored
    with the review (D110)."""
    review = await access.review_for(session, user, review_id)
    if review.diff is None:
        # Reviews before Phase 6 didn't store their diff.
        raise ApiError(404, "not_recorded", f"review {review_id} has no stored diff")
    return schemas.ReviewDiff(
        review_id=review_id,
        base_sha=review.base_sha,
        head_sha=review.head_sha,
        diff=review.diff,
        seeds=[schemas.Seed.model_validate(s) for s in review.seeds or []],
    )


@router.get("/{review_id}/trace")
async def get_review_trace(
    review_id: int, session: Session, user: CurrentUser
) -> schemas.ReviewTrace:
    """Why each piece of context was (or wasn't) shown to the model: every
    retrieved candidate per review chunk, with its stage scores, plus the
    token budgets the prompt was packed with."""
    await access.review_for(session, user, review_id)
    items = (
        await session.scalars(
            select(models.ReviewContextItem)
            .where(models.ReviewContextItem.review_id == review_id)
            .order_by(
                models.ReviewContextItem.chunk_index,
                models.ReviewContextItem.included.desc(),
                models.ReviewContextItem.id,
            )
        )
    ).all()
    chunks: dict[int, list[schemas.TraceItem]] = {}
    for item in items:
        chunks.setdefault(item.chunk_index, []).append(
            schemas.TraceItem.model_validate(item, from_attributes=True)
        )
    packed = await session.scalar(
        select(models.ReviewEvent.payload)
        .where(
            models.ReviewEvent.review_id == review_id,
            models.ReviewEvent.type == "stage",
            models.ReviewEvent.payload["stage"].astext == "packed",
        )
        .limit(1)
    )
    return schemas.ReviewTrace(
        review_id=review_id,
        budgets=(packed or {}).get("budgets"),
        chunks=[schemas.TraceChunk(index=i, items=v) for i, v in sorted(chunks.items())],
    )


@router.get("/{review_id}/graph")
async def get_review_graph(
    review_id: int, session: Session, user: CurrentUser
) -> schemas.ReviewGraph:
    """The call graph around the review's changed code (api/graph.py)."""
    review = await access.review_for(session, user, review_id)
    return await review_graph(session, review)


@router.get(
    "/{review_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def review_events(
    review_id: int,
    session: Session,
    user: CurrentUser,
    engine: Annotated[AsyncEngine, Depends(get_engine)],
    redis: Annotated[Redis | None, Depends(get_events_redis)],
    last_event_id: Annotated[int | None, Header(ge=0)] = None,
    after: Annotated[int | None, Query(ge=0)] = None,
) -> StreamingResponse:
    """The review's timeline as Server-Sent Events: everything after
    `Last-Event-ID` (an EventSource sends it when it reconnects; `?after=`
    does the same), then live events until `end` (api/sse.py)."""
    await access.review_for(session, user, review_id)
    # Give the access check's connection back now. The request's session would
    # otherwise hold it, idle in a transaction, until the stream ends, so ~15
    # open streams would use up the pool every other request needs (D129). The
    # stream reads with short connections of its own (api/sse.py).
    await session.close()
    start = after if after is not None else (last_event_id or 0)
    return StreamingResponse(
        review_stream(engine, redis, review_id, start),
        media_type="text/event-stream",
        # no-cache: never replay a stale stream. X-Accel-Buffering: nginx-style
        # proxies would otherwise hold events back until a buffer fills.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
