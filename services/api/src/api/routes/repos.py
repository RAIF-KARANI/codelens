"""Repositories: list, detail, index history, reviews, and re-indexing.

Every route sees only the repos the caller may see (api/access.py, D111)."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api import access, schemas
from api.auth import User, current_user, require_writable
from api.dependencies import get_queue, get_session, get_settings
from api.errors import ApiError
from api.queries import DEFAULT_LIMIT, MAX_LIMIT, page_of, review_summaries
from api.queue import JobQueue
from core.config import Settings
from core.db import models
from core.enums import RepoSource, RepoStatus, ReviewStatus
from core.github.webhooks import Policy

# A repo claimed for indexing ('pending') longer than this is taken as a lost
# job, so the button works again. A job retries a held lock for up to 30
# minutes (worker/tasks.py) before it gives up.
STALE_CLAIM = timedelta(hours=1)

# Router-level too: auth runs before any other validation (cached per request).
router = APIRouter(prefix="/api/v1/repos", tags=["repos"], dependencies=[Depends(current_user)])

Session = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(current_user)]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]

_last_review_at = (
    select(func.max(models.Review.created_at))
    .where(models.Review.repo_id == models.Repo.id)
    .correlate(models.Repo)
    .scalar_subquery()
)


@router.get("")
async def list_repos(session: Session, user: CurrentUser) -> list[schemas.Repo]:
    rows = await session.execute(
        select(models.Repo, _last_review_at)
        .where(access.visible(user))
        .order_by(models.Repo.full_name)
    )
    return [
        schemas.Repo.model_validate(repo).model_copy(update={"last_review_at": last})
        for repo, last in rows
    ]


@router.get("/{repo_id}")
async def get_repo(repo_id: int, session: Session, user: CurrentUser) -> schemas.RepoDetail:
    repo = await access.repo_for(session, user, repo_id)
    last_review_at = await session.scalar(
        select(func.max(models.Review.created_at)).where(models.Review.repo_id == repo_id)
    )
    latest = await session.scalar(
        select(models.IndexRun)
        .where(models.IndexRun.repo_id == repo_id)
        .order_by(models.IndexRun.id.desc())
        .limit(1)
    )
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
    repo_id: int,
    session: Session,
    user: CurrentUser,
    queue: Annotated[JobQueue, Depends(get_queue)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> schemas.IndexQueued:
    """Re-index the repo (a local checkout, or a GitHub repo's mirror at its
    default branch), then embed what changed. Poll index-runs for progress.
    One index per repo at a time: asking again while one is queued or running
    is a 409, so the button can't pile jobs onto the shared workers (D130)."""
    repo = await access.repo_for(session, user, repo_id)
    if repo.source is RepoSource.GITHUB:
        # The same account policy as webhooks: an account removed from the
        # allowlist, or an installation suspended, gets no more work.
        installation = (
            await session.get(models.Installation, repo.installation_id)
            if repo.installation_id is not None
            else None
        )
        indexable = installation is not None and Policy(settings.allowed_accounts).covers(
            installation.account_login, installation.suspended_at
        )
    else:
        indexable = repo.local_path is not None
    if not indexable:
        raise ApiError(
            409,
            "not_indexable",
            f"{repo.full_name} has no local checkout or allowed, active GitHub installation",
        )

    # Claim the repo in one statement, so two clicks can't both pass a check
    # and both queue a job.
    previous = repo.status
    claimed = await session.scalar(
        update(models.Repo)
        .where(
            models.Repo.id == repo_id,
            or_(
                models.Repo.status.not_in([RepoStatus.PENDING, RepoStatus.INDEXING]),
                models.Repo.updated_at < func.now() - STALE_CLAIM,
            ),
        )
        .values(status=RepoStatus.PENDING, updated_at=func.now())
        .returning(models.Repo.id)
    )
    if claimed is None:
        raise ApiError(409, "already_indexing", f"{repo.full_name} is already being indexed")
    await session.commit()
    try:
        await queue.index_repo(repo_id)
    except Exception:
        # Nothing was queued: give the repo its status back, so the next click works.
        await session.execute(
            update(models.Repo).where(models.Repo.id == repo_id).values(status=previous)
        )
        await session.commit()
        raise
    return schemas.IndexQueued(repo_id=repo_id)


@router.get("/{repo_id}/index-runs")
async def list_index_runs(
    repo_id: int,
    session: Session,
    user: CurrentUser,
    cursor: int | None = None,
    limit: Limit = DEFAULT_LIMIT,
) -> schemas.Page[schemas.IndexRun]:
    await access.repo_for(session, user, repo_id)
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
    user: CurrentUser,
    status_: Annotated[ReviewStatus | None, Query(alias="status")] = None,
    cursor: int | None = None,
    limit: Limit = DEFAULT_LIMIT,
) -> schemas.Page[schemas.ReviewSummary]:
    await access.repo_for(session, user, repo_id)
    query = select(models.Review).where(models.Review.repo_id == repo_id)
    if status_ is not None:
        query = query.where(models.Review.status == status_)
    if cursor is not None:
        query = query.where(models.Review.id < cursor)
    rows = (await session.scalars(query.order_by(models.Review.id.desc()).limit(limit + 1))).all()
    items, next_cursor = page_of(rows, limit)
    return schemas.Page(items=await review_summaries(session, items), next_cursor=next_cursor)
