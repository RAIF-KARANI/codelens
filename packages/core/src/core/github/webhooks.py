"""Webhook deliveries: proving one came from GitHub, and turning what it says
into database state plus the jobs it calls for.

GitHub signs every delivery with the App's webhook secret:
`X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the raw body>`. Anyone can
POST to our URL, so a delivery whose signature doesn't match is rejected
before its body is even parsed.

`apply_event` only writes rows and says which jobs to queue; it never does
slow work itself (GitHub gives a delivery 10 seconds).

Who it works for is a `Policy`: only installations by allowed accounts are
mirrored, indexed and reviewed (D119), and each account may start a limited
number of reviews per hour (D120). The reviews run on one machine's GPU.
"""

import hashlib
import hmac
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from core.db.models import Installation, Repo, Review
from core.enums import RepoSource, ReviewSource, ReviewStatus

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
DELIVERY_HEADER = "X-GitHub-Delivery"

installations_t = Installation.__table__
repos_t = Repo.__table__
reviews_t = Review.__table__


def sign(secret: str, body: bytes) -> str:
    """The signature header value GitHub would send for `body`."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    # compare_digest takes the same time wherever the strings differ, so an
    # attacker can't find the right signature byte by byte by timing replies.
    return hmac.compare_digest(sign(secret, body), header)


@dataclass(frozen=True, slots=True)
class Policy:
    """Whose installations CodeLens acts on, and how much it reviews for each."""

    # Lowercased GitHub logins (users or organisations). Empty: nobody.
    allowed_accounts: frozenset[str]
    reviews_per_hour: int = 0  # per account; 0 means no cap

    def allows(self, login: str) -> bool:
        return login.lower() in self.allowed_accounts


@dataclass(frozen=True, slots=True)
class QueuedReview:
    review_id: int
    base_sha: str  # the PR's base branch tip; the diff is against its merge-base
    title: str | None  # the pull request's title, for the prompt


@dataclass
class EventOutcome:
    status: Literal["processed", "ignored"]
    index: list[int] = field(default_factory=list)  # repo ids to (re-)index
    reviews: list[QueuedReview] = field(default_factory=list)


# The actions that put new code (or a newly reviewable PR) in front of us.
# Drafts wait for ready_for_review; closed and edited PRs need nothing.
REVIEWED_ACTIONS = frozenset({"opened", "synchronize", "reopened", "ready_for_review"})


async def apply_event(
    conn: AsyncConnection,
    event: str,
    action: str | None,
    payload: dict[str, Any],
    policy: Policy,
) -> EventOutcome:
    """Apply one event to the database; return what to queue for it.

    | Event | Effect | Jobs |
    |---|---|---|
    | `installation` created | the installation, and the repos it covers | index each repo |
    | `installation` other actions | the installation's state; `deleted` drops it | none |
    | `installation_repositories` | added repos attached, removed detached | index the added |
    | `push` to a default branch | the default branch | re-index that repo |
    | `pull_request` with new code | a queued review (older queued ones superseded) | review |

    Everything else (pushes to other branches, drafts, ...) is ignored, and so
    is every event from an installation by an account the policy doesn't
    allow, except `deleted`: removing what an uninstall leaves is always safe.
    """
    if event in ("installation", "installation_repositories") and "installation" in payload:
        account = payload["installation"]["account"]["login"]
        if action != "deleted" and not policy.allows(account):
            # A warning, not an error: it is what the allowlist is for. It
            # also tells the owner who forgot to list themselves.
            logger.warning(
                "installation %s by %s ignored: not in GITHUB_ALLOWED_ACCOUNTS",
                payload["installation"]["id"],
                account,
            )
            return EventOutcome("ignored")
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
        return await _push(conn, payload, policy)
    if event == "pull_request":
        return await _pull_request(conn, action, payload, policy)
    return EventOutcome("ignored")


def _covered_for(policy: Policy, github_repo_id: object) -> list[ColumnElement[bool]]:
    """WHERE clauses for a repo the App covers for an allowed account. Push
    and pull request payloads name only the installation's id, so the account
    comes from the mirrored installation. Checking it on every event means
    removing an account from the list takes effect at once."""
    return [
        repos_t.c.github_id == github_repo_id,
        repos_t.c.installation_id == installations_t.c.id,
        func.lower(installations_t.c.account_login).in_(policy.allowed_accounts),
    ]


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


async def _push(conn: AsyncConnection, payload: dict[str, Any], policy: Policy) -> EventOutcome:
    repository = payload.get("repository") or {}
    default_branch = repository.get("default_branch")
    # Only the default branch is indexed: PR branches are read straight from
    # git when they're reviewed. A deleted branch has nothing to index.
    if payload.get("deleted") or payload.get("ref") != f"refs/heads/{default_branch}":
        return EventOutcome("ignored")
    repo_id = await conn.scalar(
        update(repos_t)
        .where(*_covered_for(policy, repository.get("id")))
        .values(default_branch=default_branch, updated_at=func.now())
        .returning(repos_t.c.id)
    )
    if repo_id is None:  # a repo the App doesn't (or no longer) cover
        return EventOutcome("ignored")
    return EventOutcome("processed", [repo_id])


async def _pull_request(
    conn: AsyncConnection, action: str | None, payload: dict[str, Any], policy: Policy
) -> EventOutcome:
    pr = payload.get("pull_request") or {}
    if action not in REVIEWED_ACTIONS or pr.get("draft") or pr.get("state") != "open":
        return EventOutcome("ignored")
    covered = (
        await conn.execute(
            select(repos_t.c.id, installations_t.c.account_login).where(
                *_covered_for(policy, (payload.get("repository") or {}).get("id"))
            )
        )
    ).first()
    if covered is None:
        return EventOutcome("ignored")
    repo_id, account = covered
    number, head_sha = pr["number"], pr["head"]["sha"]

    same_pr = (reviews_t.c.repo_id == repo_id) & (reviews_t.c.pr_number == number)
    # This exact head is already queued, running or reviewed: a redelivery, or
    # a PR reopened without new commits. (A failed review may be retried.)
    done = await conn.scalar(
        select(reviews_t.c.id).where(
            same_pr,
            reviews_t.c.head_sha == head_sha,
            reviews_t.c.status.in_(
                [ReviewStatus.QUEUED, ReviewStatus.RUNNING, ReviewStatus.COMPLETED]
            ),
        )
    )
    if done is not None:
        return EventOutcome("ignored")
    if policy.reviews_per_hour:
        started = await _reviews_started(conn, account, besides=same_pr)
        if started >= policy.reviews_per_hour:
            # Recorded, not queued: the dashboard says why. A failed review
            # may be retried, so the next push, or reopening the PR, reviews
            # it once the hour has room again.
            await conn.execute(
                insert(reviews_t).values(
                    repo_id=repo_id,
                    source=ReviewSource.GITHUB,
                    pr_number=number,
                    title=pr.get("title"),
                    head_sha=head_sha,
                    status=ReviewStatus.FAILED,
                    finished_at=func.now(),
                    error=(
                        f"not reviewed: {account} started {started} reviews in the last hour,"
                        f" the limit (GITHUB_REVIEWS_PER_HOUR={policy.reviews_per_hour});"
                        " push again or reopen the PR later to review this commit"
                    ),
                )
            )
            full_name = (payload.get("repository") or {}).get("full_name")
            logger.warning(
                "review of %s#%s not queued: %s is over its hourly limit",
                full_name,
                number,
                account,
            )
            return EventOutcome("processed")
    # A newer push makes queued reviews of older heads pointless (D103).
    # Running ones are left to finish; the worker won't post them (stale).
    await conn.execute(
        update(reviews_t)
        .where(same_pr, reviews_t.c.status == ReviewStatus.QUEUED)
        .values(
            status=ReviewStatus.SUPERSEDED,
            finished_at=func.now(),
            error=f"superseded by a push of {head_sha[:7]}",
        )
    )
    review_id = await conn.scalar(
        insert(reviews_t)
        .values(
            repo_id=repo_id,
            source=ReviewSource.GITHUB,
            pr_number=number,
            title=pr.get("title"),
            head_sha=head_sha,
            status=ReviewStatus.QUEUED,
        )
        .returning(reviews_t.c.id)
    )
    queued = QueuedReview(review_id, pr["base"]["sha"], pr.get("title"))
    return EventOutcome("processed", reviews=[queued])


async def _reviews_started(
    conn: AsyncConnection, account: str, *, besides: ColumnElement[bool]
) -> int:
    """Reviews of `account`'s repos that started in the last hour or are
    waiting to: what uses, or will use, the GPU. Reviews refused for the
    limit and superseded ones never start, so they don't count; neither do
    the PR's own queued reviews (`besides`), which the new one supersedes."""
    return await conn.scalar(
        select(func.count())
        .select_from(
            reviews_t.join(repos_t, reviews_t.c.repo_id == repos_t.c.id).join(
                installations_t, repos_t.c.installation_id == installations_t.c.id
            )
        )
        .where(
            func.lower(installations_t.c.account_login) == account.lower(),
            or_(
                reviews_t.c.status == ReviewStatus.QUEUED,
                reviews_t.c.started_at > func.now() - timedelta(hours=1),
            ),
            ~(besides & (reviews_t.c.status == ReviewStatus.QUEUED)),
        )
    )


def _timestamp(value: str | None) -> datetime | None:
    # GitHub sends ISO 8601 ("2026-10-12T17:00:00Z"); asyncpg wants a datetime.
    return datetime.fromisoformat(value) if value else None
