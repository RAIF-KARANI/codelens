"""Repositories: list, detail, index history, reviews, and re-indexing."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import schemas
from api.auth import current_user, require_writable
from api.dependencies import get_queue, get_session
from api.errors import ApiError, not_found
from api.queries import DEFAULT_LIMIT, MAX_LIMIT, page_of, review_summaries
from api.queue import JobQueue
from core.db import models
from core.enums import RepoSource, ReviewStatus

router = APIRouter(prefix="/api/v1/repos", tags=["repos"], dependencies=[Depends(current_user)])

Session = Annotated[AsyncSession, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]

_last_review_at = (
    select(func.max(models.Review.created_at))
    .where(models.Review.repo_id == models.Repo.id)
    .correlate(models.Repo)
    .scalar_subquery()
)


@router.get("")
async def list_repos(session: Session) -> list[schemas.Repo]:
    rows = await session.execute(
        select(models.Repo, _last_review_at).order_by(models.Repo.full_name)
    )
    return [
        schemas.Repo.model_validate(repo).model_copy(update={"last_review_at": last})
        for repo, last in rows
    ]


@router.get("/{repo_id}")
async def get_repo(repo_id: int, session: Session) -> schemas.RepoDetail:
    row = (
        await session.execute(select(models.Repo, _last_review_at).where(models.Repo.id == repo_id))
    ).first()
    if row is None:
        raise not_found("repo", repo_id)
    latest = await session.scalar(
        select(models.IndexRun)
        .where(models.IndexRun.repo_id == repo_id)
        .order_by(models.IndexRun.id.desc())
        .limit(1)
    )
    repo, last_review_at = row
    return schemas.RepoDetail(
        **schemas.Repo.model_validate(repo).model_dump(exclude={"last_review_at"}),
        last_review_at=last_review_at,
        latest_index_run=schemas.IndexRun.model_validate(latest) if latest else None,
    )


@router.post(
    "/{repo_id}/index",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_writable)],
)
async def index_repo(
    repo_id: int, session: Session, queue: Annotated[JobQueue, Depends(get_queue)]
) -> schemas.IndexQueued:
    """Re-index the repo (a local checkout, or a GitHub repo's mirror at its
    default branch), then embed what changed. Poll index-runs for progress."""
    repo = await session.get(models.Repo, repo_id)
    if repo is None:
        raise not_found("repo", repo_id)
    indexable = (
        repo.installation_id is not None
        if repo.source is RepoSource.GITHUB
        else repo.local_path is not None
    )
    if not indexable:
        raise ApiError(
            409, "not_indexable", f"{repo.full_name} has no local checkout or GitHub installation"
        )
    await queue.index_repo(repo_id)
    return schemas.IndexQueued(repo_id=repo_id)


@router.get("/{repo_id}/index-runs")
async def list_index_runs(
    repo_id: int,
    session: Session,
    cursor: int | None = None,
    limit: Limit = DEFAULT_LIMIT,
) -> schemas.Page[schemas.IndexRun]:
    await _ensure_repo(session, repo_id)
    query = select(models.IndexRun).where(models.IndexRun.repo_id == repo_id)
    if cursor is not None:
        query = query.where(models.IndexRun.id < cursor)
    rows = (await session.scalars(query.order_by(models.IndexRun.id.desc()).limit(limit + 1))).all()
    items, next_cursor = page_of(rows, limit)
    return schemas.Page(
        items=[schemas.IndexRun.model_validate(r) for r in items], next_cursor=next_cursor
    )


@router.get("/{repo_id}/reviews")
async def list_reviews(
    repo_id: int,
    session: Session,
    status_: Annotated[ReviewStatus | None, Query(alias="status")] = None,
    cursor: int | None = None,
    limit: Limit = DEFAULT_LIMIT,
) -> schemas.Page[schemas.ReviewSummary]:
    await _ensure_repo(session, repo_id)
    query = select(models.Review).where(models.Review.repo_id == repo_id)
    if status_ is not None:
        query = query.where(models.Review.status == status_)
    if cursor is not None:
        query = query.where(models.Review.id < cursor)
    rows = (await session.scalars(query.order_by(models.Review.id.desc()).limit(limit + 1))).all()
    items, next_cursor = page_of(rows, limit)
    return schemas.Page(items=await review_summaries(session, items), next_cursor=next_cursor)


async def _ensure_repo(session: AsyncSession, repo_id: int) -> None:
    # A missing repo is a 404, not an empty page.
    if await session.get(models.Repo, repo_id) is None:
        raise not_found("repo", repo_id)
