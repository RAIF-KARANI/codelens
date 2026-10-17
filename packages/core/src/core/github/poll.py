"""Learning about installations, pushes and pull requests by asking GitHub,
instead of waiting for its webhooks (planning-v2.md D145).

A webhook needs a public URL, so on a laptop it needs a relay (smee.io), which
a third party can read, and an event sent while the machine is off is lost.
The poller asks instead: every minute or so, per installation, which repos it
covers, where each default branch is, and which pull requests are open. On
the first look after a start it sees everything as new, so it catches up on
whatever happened while it was stopped.

What it sees becomes the same events a webhook delivers, with the same
payload shapes (GitHub's REST objects are the ones its webhooks carry), and
goes through the same intake, `webhooks.apply_event`. The allowlist, the
hourly cap, superseding and dedupe are therefore the same code for both
(D119, D120, D103), and a webhook and the poller seeing one push queue one
review.

It remembers, in memory only, what it last saw: each open PR's head, and the
default-branch heads it queued an index for. That is what turns "still open
at the same commit" into no event, so a failed review isn't retried every
minute; like a webhook, the next push or a reopen retries it. Lists are
fetched with their ETags: an unchanged one is a 304 with no body.
"""

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.db.models import Installation, Repo, Review
from core.enums import RepoStatus
from core.github.client import GitHubClient
from core.github.webhooks import EventOutcome, Policy, apply_event

logger = logging.getLogger(__name__)

installations_t = Installation.__table__
repos_t = Repo.__table__
reviews_t = Review.__table__

# Requests one installation's polling may spend in an hour: half of what
# GitHub allows an installation (5,000), so the workers' own calls (fetching,
# posting reviews) always have room.
HOURLY_BUDGET = 2500

# A bare commit sha as text, instead of the whole commit as JSON.
SHA_ONLY = "application/vnd.github.sha"


class Tokens(Protocol):
    """An installation token per installation: auth.InstallationTokens."""

    async def get(self, installation_id: int) -> str: ...


class Jobs(Protocol):
    """Where the jobs an event calls for go: api.queue.JobQueue in production."""

    async def index_repo(self, repo_id: int) -> None: ...

    async def review(self, review_id: int, base: str, *, title: str | None = None) -> None: ...


@dataclass
class Cycle:
    """What one look at GitHub found and did."""

    events: list[str] = field(default_factory=list)  # "pull_request synchronize octo/api#7"
    indexed: list[int] = field(default_factory=list)  # repo ids queued for indexing
    reviews: list[int] = field(default_factory=list)  # review ids queued
    requests: int = 0
    not_modified: int = 0
    # Per installation id: X-RateLimit-Remaining after its last request.
    rate_remaining: dict[int, int] = field(default_factory=dict)
    # Per installation id: the requests one cycle costs it (for the interval).
    cost: dict[int, int] = field(default_factory=dict)


class Poller:
    def __init__(
        self,
        engine: AsyncEngine,
        client: GitHubClient,
        app_jwt: Callable[[], str],
        tokens: Tokens,
        policy: Policy,
        jobs: Jobs,
        *,
        interval_s: int = 60,
    ) -> None:
        self._engine = engine
        self._client = client
        self._app_jwt = app_jwt
        self._tokens = tokens
        self._policy = policy
        self._jobs = jobs
        self._base_interval = interval_s
        self.interval_s = interval_s
        # URL -> (ETag, the whole answer, all pages): what a 304 stands for.
        self._cache: dict[str, tuple[str, Any]] = {}
        # Repo (GitHub id) -> PR number -> (head sha, draft), as last seen.
        self._prs: dict[int, dict[int, tuple[str, bool]]] = {}
        # Repo (GitHub id) -> PR numbers seen open, then closed: open again
        # is a reopen.
        self._closed: dict[int, set[int]] = {}
        # Repo (GitHub id) -> the default-branch head an index was queued for.
        self._indexed_head: dict[int, str] = {}

    async def poll_once(self) -> Cycle:
        """One look at every installation. Each event is applied in its own
        transaction and its jobs queued after it commits (D98)."""
        cycle = Cycle()
        listed = await self._get_list(cycle, "/app/installations?per_page=100", self._app_jwt())
        known = {
            row.github_installation_id: row
            for row in await self._rows(
                select(
                    installations_t.c.id,
                    installations_t.c.github_installation_id,
                    installations_t.c.account_login,
                    installations_t.c.account_id,
                    installations_t.c.repository_selection,
                    installations_t.c.suspended_at,
                )
            )
        }
        for installation in listed:
            account = installation["account"]
            if not self._policy.allows(account["login"], account["id"]):
                continue  # nothing stored, nothing asked (D119)
            await self._installation(cycle, installation, known.get(installation["id"]))
        # Installed before, gone now: what an `installation deleted` says.
        listed_ids = {i["id"] for i in listed}
        for github_id, row in known.items():
            if github_id not in listed_ids:
                payload = {
                    "installation": {
                        "id": github_id,
                        "account": {"login": row.account_login, "id": row.account_id},
                    }
                }
                await self._apply(cycle, "installation", "deleted", payload, f"#{github_id}")
        self.interval_s = self._next_interval(cycle)
        return cycle

    async def _installation(
        self, cycle: Cycle, installation: dict[str, Any], row: Any | None
    ) -> None:
        state = {"installation": installation}
        name = f"{installation['account']['login']} #{installation['id']}"
        suspended = installation.get("suspended_at") is not None
        if suspended:
            # Its state, so nothing acts on it (D130); no token: GitHub refuses one.
            if row is None or row.suspended_at is None:
                await self._apply(cycle, "installation", "suspend", state, name)
            return
        token = await self._tokens.get(installation["id"])
        page = await self._get_list(
            cycle,
            "/installation/repositories?per_page=100",
            token,
            key="repositories",
            installation_id=installation["id"],
        )
        if row is None:
            # New: the installation and its repos, each indexed.
            await self._apply(
                cycle, "installation", "created", {**state, "repositories": page}, name
            )
        else:
            if row.suspended_at is not None or (
                row.repository_selection != installation.get("repository_selection")
            ):
                await self._apply(cycle, "installation", "unsuspend", state, name)
            attached = {
                r.github_id
                for r in await self._rows(
                    select(repos_t.c.github_id).where(repos_t.c.installation_id == row.id)
                )
            }
            added = [r for r in page if r["id"] not in attached]
            removed = [{"id": gid} for gid in attached - {r["id"] for r in page}]
            if added or removed:
                payload = {**state, "repositories_added": added, "repositories_removed": removed}
                await self._apply(cycle, "installation_repositories", "added", payload, name)
        repos = {
            r.github_id: r
            for r in await self._rows(
                select(
                    repos_t.c.id,
                    repos_t.c.github_id,
                    repos_t.c.status,
                    repos_t.c.last_indexed_sha,
                    repos_t.c.created_at,
                )
                .join(installations_t, installations_t.c.id == repos_t.c.installation_id)
                .where(installations_t.c.github_installation_id == installation["id"])
            )
        }
        cycle.cost[installation["id"]] = 1 + 2 * len(page)
        for repository in page:
            if repository["id"] in repos:
                row = repos[repository["id"]]
                await self._default_branch(cycle, installation["id"], repository, row, token)
                await self._pull_requests(cycle, installation["id"], repository, row, token)

    async def _default_branch(
        self, cycle: Cycle, installation_id: int, repository: dict[str, Any], row: Any, token: str
    ) -> None:
        """A push to the default branch, if its head isn't what was indexed and
        no index is under way or already queued for that head."""
        branch = repository["default_branch"]
        path = f"/repos/{repository['full_name']}/commits/{quote(branch, safe='')}"
        head = await self._get(cycle, path, token, accept=SHA_ONLY, installation_id=installation_id)
        if not isinstance(head, str) or head == row.last_indexed_sha:
            return
        if row.status in (RepoStatus.PENDING, RepoStatus.INDEXING):
            return  # an index is coming; look again once it's done
        if self._indexed_head.get(repository["id"]) == head:
            return  # queued once already (it may have failed: the next push retries)
        payload = {
            "ref": f"refs/heads/{branch}",
            "after": head,
            "installation": {"id": installation_id},
            "repository": repository,
        }
        await self._apply(cycle, "push", None, payload, f"{repository['full_name']}@{head[:7]}")
        self._indexed_head[repository["id"]] = head

    async def _pull_requests(
        self, cycle: Cycle, installation_id: int, repository: dict[str, Any], row: Any, token: str
    ) -> None:
        path = f"/repos/{repository['full_name']}/pulls?state=open&per_page=100"
        pulls = await self._get_list(cycle, path, token, installation_id=installation_id)
        first_look = repository["id"] not in self._prs
        seen = self._prs.setdefault(repository["id"], {})
        reviewed: set[int] = set()
        if first_look:
            reviewed = {
                r.pr_number
                for r in await self._rows(
                    select(reviews_t.c.pr_number).where(reviews_t.c.repo_id == row.id).distinct()
                )
            }
        closed = self._closed.setdefault(repository["id"], set())
        open_now = set()
        for pr in pulls:
            number, now = pr["number"], (pr["head"]["sha"], bool(pr.get("draft")))
            open_now.add(number)
            before = seen.get(number)
            if before == now:
                continue
            if first_look and number not in reviewed and _opened_before(pr, row.created_at):
                # Open before CodeLens knew the repo, and never reviewed: a
                # webhook setup wouldn't review it until its next push, so
                # neither does this one. (Opened while it was stopped: it is.)
                seen[number] = now
                cycle.events.append(f"pull_request {repository['full_name']}#{number}: baseline")
                continue
            if before is None:
                action = "reopened" if number in closed else "opened"
            elif before[1] and not now[1]:
                action = "ready_for_review"
            else:
                action = "synchronize"
            payload = {
                "action": action,
                "number": number,
                "installation": {"id": installation_id},
                "repository": repository,
                "pull_request": pr,
            }
            name = f"{repository['full_name']}#{number}"
            await self._apply(cycle, "pull_request", action, payload, name)
            seen[number] = now
            closed.discard(number)
        for number in set(seen) - open_now:
            del seen[number]
            closed.add(number)

    async def _apply(
        self, cycle: Cycle, event: str, action: str | None, payload: dict[str, Any], name: str
    ) -> EventOutcome:
        """The webhook intake, then the jobs: as POST /webhooks/github does."""
        async with self._engine.begin() as conn:
            outcome = await apply_event(conn, event, action, payload, self._policy)
        cycle.events.append(f"{event} {action or ''} {name}: {outcome.status}".replace("  ", " "))
        for repo_id in outcome.index:
            await self._jobs.index_repo(repo_id)
            cycle.indexed.append(repo_id)
        for review in outcome.reviews:
            await self._jobs.review(review.review_id, review.base_sha, title=review.title)
            cycle.reviews.append(review.review_id)
        return outcome

    async def _get(
        self,
        cycle: Cycle,
        path: str,
        token: str,
        *,
        accept: str | None = None,
        installation_id: int | None = None,
    ) -> Any:
        """One resource, conditionally: a 304 answers with what was cached."""
        cached = self._cache.get(path)
        answer = await self._client.get_conditional(
            path, token, etag=cached[0] if cached else None, accept=accept
        )
        self._count(cycle, answer.not_modified, answer.rate_remaining, installation_id)
        if answer.not_modified and cached:
            return cached[1]
        if answer.etag:
            self._cache[path] = (answer.etag, answer.data)
        return answer.data

    async def _get_list(
        self,
        cycle: Cycle,
        path: str,
        token: str,
        *,
        key: str | None = None,
        installation_id: int | None = None,
    ) -> list[Any]:
        """Every page of a list. The first page is asked conditionally: a 304
        means the list is as it was (GitHub changes page 1's ETag whenever
        the list changes); otherwise the rest is fetched page by page."""
        cached = self._cache.get(path)
        first = await self._client.get_conditional(path, token, etag=cached[0] if cached else None)
        self._count(cycle, first.not_modified, first.rate_remaining, installation_id)
        if first.not_modified and cached:
            return list(cached[1])
        items = list(first.data[key] if key else first.data)
        next_url = first.next_url
        while next_url:
            page = await self._client.get_conditional(next_url, token)
            self._count(cycle, False, page.rate_remaining, installation_id)
            items += page.data[key] if key else page.data
            next_url = page.next_url
        if first.etag:
            self._cache[path] = (first.etag, items)
        return items

    @staticmethod
    def _count(
        cycle: Cycle, not_modified: bool, remaining: int | None, installation_id: int | None
    ) -> None:
        cycle.requests += 1
        cycle.not_modified += not_modified
        if installation_id is not None and remaining is not None:
            cycle.rate_remaining[installation_id] = remaining

    def _next_interval(self, cycle: Cycle) -> int:
        """The configured interval, or longer when one installation's repos
        would cost more than HOURLY_BUDGET requests an hour at that pace."""
        busiest = max(cycle.cost.values(), default=0)
        return max(self._base_interval, math.ceil(busiest * 3600 / HOURLY_BUDGET))

    async def _rows(self, statement: Any) -> list[Any]:
        async with self._engine.connect() as conn:
            return list(await conn.execute(statement))


def _opened_before(pr: dict[str, Any], moment: datetime) -> bool:
    """Was the pull request opened before `moment`? GitHub writes
    "2026-10-13T09:00:00Z"; a missing date counts as new."""
    created = pr.get("created_at")
    return isinstance(created, str) and datetime.fromisoformat(created) < moment
