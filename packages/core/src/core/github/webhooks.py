"""Webhook deliveries: proving one came from GitHub, and turning what it says
into database state plus the jobs it calls for.

GitHub signs every delivery with the App's webhook secret:
`X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the raw body>`. Anyone can
POST to our URL, so a delivery whose signature doesn't match is rejected
before its body is even parsed.

`apply_event` only writes rows and says which jobs to queue; it never does
slow work itself (GitHub gives a delivery 10 seconds).
"""

import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import delete, func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from core.db.models import Installation, Repo
from core.enums import RepoSource

SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
DELIVERY_HEADER = "X-GitHub-Delivery"

installations_t = Installation.__table__
repos_t = Repo.__table__


def sign(secret: str, body: bytes) -> str:
    """The signature header value GitHub would send for `body`."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    # compare_digest takes the same time wherever the strings differ, so an
    # attacker can't find the right signature byte by byte by timing replies.
    return hmac.compare_digest(sign(secret, body), header)


@dataclass
class EventOutcome:
    status: Literal["processed", "ignored"]
    index: list[int] = field(default_factory=list)  # repo ids to (re-)index


async def apply_event(
    conn: AsyncConnection, event: str, action: str | None, payload: dict[str, Any]
) -> EventOutcome:
    """Apply one event to the database; return what to queue for it.

    | Event | Effect | Jobs |
    |---|---|---|
    | `installation` created | the installation, and the repos it covers | index each repo |
    | `installation` other actions | the installation's state; `deleted` drops it | none |
    | `installation_repositories` | added repos attached, removed detached | index the added |
    | `push` to a default branch | the default branch | re-index that repo |

    Everything else (and pushes to other branches) is ignored. Pull requests
    arrive in step 3b.
    """
    if event in ("installation", "installation_repositories") and "installation" in payload:
        installation_id = await apply_installation_event(conn, action, payload)
        if installation_id is None:  # uninstalled
            return EventOutcome("processed")
        added = payload.get("repositories") if event == "installation" else None
        if event == "installation_repositories":
            added = payload.get("repositories_added")
            removed = payload.get("repositories_removed") or []
            await detach_repositories(conn, [r["id"] for r in removed])
        # Only a new installation, or new repos, need indexing: other actions
        # (suspend, new permissions accepted) repeat the full repo list.
        wants_index = event == "installation_repositories" or action == "created"
        ids = await attach_repositories(conn, installation_id, added or [])
        return EventOutcome("processed", ids if wants_index else [])
    if event == "push":
        return await _push(conn, payload)
    return EventOutcome("ignored")


async def apply_installation_event(
    conn: AsyncConnection, action: str | None, payload: dict[str, Any]
) -> int | None:
    """Mirror an installation into the installations table; returns its row id.

    Every action except `deleted` carries the installation's current state
    (account, repository selection, suspended_at), so they all upsert it:
    created, suspend, unsuspend, new_permissions_accepted, and the
    installation_repositories events. `deleted` removes the row (returns
    None), and its repos keep their data with installation_id set to NULL.
    """
    installation = payload["installation"]
    if action == "deleted":
        await conn.execute(
            delete(installations_t).where(
                installations_t.c.github_installation_id == installation["id"]
            )
        )
        return None
    account = installation["account"]
    values = {
        "account_id": account["id"],
        "account_login": account["login"],
        "account_type": account["type"],
        "repository_selection": installation.get("repository_selection"),
        "suspended_at": _timestamp(installation.get("suspended_at")),
    }
    return await conn.scalar(
        insert(installations_t)
        .values(github_installation_id=installation["id"], **values)
        .on_conflict_do_update(
            index_elements=[installations_t.c.github_installation_id],
            set_={**values, "updated_at": func.now()},
        )
        .returning(installations_t.c.id)
    )


async def attach_repositories(
    conn: AsyncConnection, installation_id: int, repositories: Sequence[dict[str, Any]]
) -> list[int]:
    """Upsert GitHub repos (keyed by GitHub's repo id, so a rename updates the
    name) under an installation; returns their row ids."""
    ids = []
    for repository in repositories:
        ids.append(
            await conn.scalar(
                insert(repos_t)
                .values(
                    github_id=repository["id"],
                    full_name=repository["full_name"],
                    source=RepoSource.GITHUB,
                    installation_id=installation_id,
                )
                .on_conflict_do_update(
                    index_elements=[repos_t.c.github_id],
                    set_={
                        "full_name": repository["full_name"],
                        "installation_id": installation_id,
                        "updated_at": func.now(),
                    },
                )
                .returning(repos_t.c.id)
            )
        )
    return ids


async def detach_repositories(conn: AsyncConnection, github_ids: Sequence[int]) -> None:
    """Repos the App lost access to keep their graph and reviews, but no
    longer belong to an installation, so nothing indexes or reviews them."""
    if github_ids:
        await conn.execute(
            update(repos_t)
            .where(repos_t.c.github_id.in_(github_ids))
            .values(installation_id=None, updated_at=func.now())
        )


async def _push(conn: AsyncConnection, payload: dict[str, Any]) -> EventOutcome:
    repository = payload.get("repository") or {}
    default_branch = repository.get("default_branch")
    # Only the default branch is indexed: PR branches are read straight from
    # git when they're reviewed. A deleted branch has nothing to index.
    if payload.get("deleted") or payload.get("ref") != f"refs/heads/{default_branch}":
        return EventOutcome("ignored")
    repo_id = await conn.scalar(
        update(repos_t)
        .where(repos_t.c.github_id == repository.get("id"), repos_t.c.installation_id.is_not(None))
        .values(default_branch=default_branch, updated_at=func.now())
        .returning(repos_t.c.id)
    )
    if repo_id is None:  # a repo the App doesn't (or no longer) cover
        return EventOutcome("ignored")
    return EventOutcome("processed", [repo_id])


def _timestamp(value: str | None) -> datetime | None:
    # GitHub sends ISO 8601 ("2026-10-12T17:00:00Z"); asyncpg wants a datetime.
    return datetime.fromisoformat(value) if value else None
