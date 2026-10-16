"""The overview's numbers (GET /api/v1/stats): reviews, findings and repos
over the repos the caller may see (api/access.py, D111), in a few grouped
queries rather than one per repo."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, and_, cast, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Date

from api import access, schemas
from api.auth import User, current_user
from api.dependencies import get_session
from core.db import models
from core.enums import FindingStatus, ReviewSource, ReviewStatus

router = APIRouter(prefix="/api/v1/stats", tags=["stats"], dependencies=[Depends(current_user)])

Session = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(current_user)]


@router.get("")
async def get_stats(
    session: Session,
    user: CurrentUser,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    include_eval: bool = False,
    anchor: Literal["today", "latest"] = "today",
) -> schemas.Stats:
    """`days` UTC days ending today (or, with `anchor=latest`, on the day of the
    newest review: what the public demo asks for, since its reviews are made
    once and copied, D157): reviews created and how they ended, the findings
    they posted, the median time from queued to finished, and one row per day.
    Plus what is queued or running now, and the repos by index status. Eval
    runs are left out unless asked for (D155)."""
    today = datetime.now(UTC).date()

    def reviews(query: Select) -> Select:
        """`query`, limited to reviews of visible repos (and not evals). A query
        of aggregates alone must name `reviews` with `.select_from` first, or
        there is nothing to join the repos to."""
        query = query.join(models.Repo, models.Repo.id == models.Review.repo_id).where(
            access.visible(user)
        )
        if not include_eval:
            query = query.where(models.Review.source != ReviewSource.EVAL)
        return query

    r = models.Review
    last_day = today
    if anchor == "latest":
        newest = await session.scalar(reviews(select(func.max(r.created_at)).select_from(r)))
        if newest is not None:
            last_day = newest.astimezone(UTC).date()
    first_day = last_day - timedelta(days=days - 1)
    start = datetime.combine(first_day, datetime.min.time(), tzinfo=UTC)
    stop = start + timedelta(days=days)
    in_window = and_(r.created_at >= start, r.created_at < stop)
    review_day = cast(func.timezone("UTC", r.created_at), Date)

    by_status = dict(
        (
            await session.execute(
                reviews(select(r.status, func.count())).where(in_window).group_by(r.status)
            )
        ).all()
    )
    counts = schemas.ReviewCounts(
        total=sum(by_status.values()),
        completed=by_status.get(ReviewStatus.COMPLETED, 0),
        failed=by_status.get(ReviewStatus.FAILED, 0),
        superseded=by_status.get(ReviewStatus.SUPERSEDED, 0),
    )

    f = models.Finding
    posted = (
        select(f.severity, review_day, func.count())
        .select_from(f)
        .join(r, r.id == f.review_id)
        .where(in_window, f.status == FindingStatus.POSTED)
        .group_by(f.severity, review_day)
    )
    findings = schemas.SeverityCounts()
    findings_per_day: dict[date, int] = {}
    for severity, day, n in await session.execute(reviews(posted)):
        setattr(findings, severity.value, getattr(findings, severity.value) + n)
        findings_per_day[day] = findings_per_day.get(day, 0) + n

    reviews_per_day = dict(
        (
            await session.execute(
                reviews(select(review_day, func.count())).where(in_window).group_by(review_day)
            )
        ).all()
    )

    median = await session.scalar(
        reviews(
            select(
                func.percentile_cont(0.5).within_group(
                    extract("epoch", r.finished_at - r.created_at)
                )
            ).select_from(r)
        ).where(in_window, r.status == ReviewStatus.COMPLETED, r.finished_at.is_not(None))
    )

    in_progress = await session.scalar(
        reviews(select(func.count()).select_from(r)).where(
            r.status.in_([ReviewStatus.QUEUED, ReviewStatus.RUNNING])
        )
    )

    repo_status = dict(
        (
            await session.execute(
                select(models.Repo.status, func.count())
                .where(access.visible(user))
                .group_by(models.Repo.status)
            )
        ).all()
    )

    return schemas.Stats(
        days=days,
        ends_on=last_day,
        ends_today=last_day == today,
        reviews=counts,
        findings=findings,
        median_seconds=float(median) if median is not None else None,
        in_progress=in_progress or 0,
        repos=schemas.RepoCounts(
            total=sum(repo_status.values()),
            **{status.value: n for status, n in repo_status.items()},
        ),
        daily=[
            schemas.DayCount(
                day=day,
                reviews=reviews_per_day.get(day, 0),
                findings=findings_per_day.get(day, 0),
            )
            for day in (first_day + timedelta(days=i) for i in range(days))
        ],
    )
