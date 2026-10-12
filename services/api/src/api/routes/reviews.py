"""Reviews: queue a manual review, read one with its findings."""

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import schemas
from api.auth import current_user, require_writable
from api.dependencies import get_queue, get_session
from api.errors import ApiError, not_found
from api.queries import severity_counts
from api.queue import JobQueue, QueueUnavailable
from core.db import models
from core.diff import git
from core.enums import FindingStatus, RepoSource, ReviewSource, ReviewStatus, Severity

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"], dependencies=[Depends(current_user)])

Session = Annotated[AsyncSession, Depends(get_session)]

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.SUGGESTION: 2}


@router.post("", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_writable)])
async def create_review(
    body: schemas.ReviewCreate,
    session: Session,
    queue: Annotated[JobQueue, Depends(get_queue)],
) -> schemas.ReviewQueued:
    """Queue a review of `head` against its merge-base with `base`, in a repo
    indexed from a local checkout. Follow it with GET /reviews/{id}."""
    repo = await session.get(models.Repo, body.repo_id)
    if repo is None:
        raise not_found("repo", body.repo_id)
    if repo.source is not RepoSource.LOCAL or not repo.local_path:
        raise ApiError(409, "unsupported_source", f"{repo.full_name} has no local checkout")
    # git runs subprocesses: off the event loop.
    head_sha, base_sha = await asyncio.to_thread(
        _resolve_refs, Path(repo.local_path), body.head, body.base
    )
    review = models.Review(
        repo_id=repo.id, source=ReviewSource.CLI, head_sha=head_sha, status=ReviewStatus.QUEUED
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
    review_id: int, session: Session, include_dropped: bool = False
) -> schemas.ReviewDetail:
    """A review with its findings: posted ones by default, and with
    `include_dropped=true` also those validation rejected, with the reason."""
    review = await session.get(models.Review, review_id)
    if review is None:
        raise not_found("review", review_id)
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
    return schemas.ReviewDetail(
        **schemas.ReviewSummary.model_validate(review).model_dump(exclude={"counts"}),
        counts=counts[review_id],
        token_usage=review.token_usage,
        pipeline_config=review.pipeline_config,
        findings=[schemas.Finding.model_validate(f) for f in findings],
    )
