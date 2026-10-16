"""Who may see what (D111). Every repo, review, finding and code node is
reached through its repo, and a repo is visible to:

- users whose token lists the repo's App installation (the dashboard fills
  `installations` from GitHub at login), and
- API_ADMIN_LOGINS, who see every repo, including local and eval ones that
  have no installation;
- on a demo instance (DEMO_MODE), every signed-in user: its database holds
  only the public demo repos it was given (D115).

Anything not visible is a 404, exactly like something that doesn't exist, so
a private repo's existence doesn't leak.
"""

from typing import Any

from sqlalchemy import ColumnElement, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import User
from api.errors import not_found
from core.db import models


def visible(user: User) -> ColumnElement[bool]:
    """A filter on repos (`models.Repo`) for this user.

    Per installation, not per repo (accepted for now, D137): GitHub lists an
    installation for a user who can see any one of its repos, so an outside
    collaborator on one repo of an organisation would see all of them. Fine
    while the owner is the dashboard's only user; per-repo access comes
    before anyone else signs in (docs/hosted-service.md)."""
    if user.is_admin or user.demo:
        return true()
    return models.Repo.installation_id.in_(
        select(models.Installation.id).where(
            models.Installation.github_installation_id.in_(user.installations)
        )
    )


async def repo_for(session: AsyncSession, user: User, repo_id: int) -> models.Repo:
    repo = await session.scalar(select(models.Repo).where(models.Repo.id == repo_id, visible(user)))
    if repo is None:
        raise not_found("repo", repo_id)
    return repo


async def review_for(session: AsyncSession, user: User, review_id: int) -> models.Review:
    review = await session.scalar(
        select(models.Review)
        .join(models.Repo, models.Repo.id == models.Review.repo_id)
        .where(models.Review.id == review_id, visible(user))
    )
    if review is None:
        raise not_found("review", review_id)
    return review


async def finding_for(session: AsyncSession, user: User, finding_id: int) -> models.Finding:
    finding = await session.scalar(
        select(models.Finding)
        .join(models.Review, models.Review.id == models.Finding.review_id)
        .join(models.Repo, models.Repo.id == models.Review.repo_id)
        .where(models.Finding.id == finding_id, visible(user))
    )
    if finding is None:
        raise not_found("finding", finding_id)
    return finding


async def node_for(session: AsyncSession, user: User, node_id: int) -> Any:
    """A code node with its file's path (a Row: node fields + `path`)."""
    row = (
        await session.execute(
            select(models.CodeNode, models.File.path)
            .join(models.File, models.File.id == models.CodeNode.file_id)
            .join(models.Repo, models.Repo.id == models.CodeNode.repo_id)
            .where(models.CodeNode.id == node_id, visible(user))
        )
    ).first()
    if row is None:
        raise not_found("node", node_id)
    return row
