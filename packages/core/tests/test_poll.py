"""The poller (D145) against a fake GitHub and the real test database: what
it sees becomes the webhook intake's events, each producing exactly one job,
and nothing when nothing changed."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from core.accounts import Accounts
from core.db import create_engine
from core.github.client import GitHubClient
from core.github.poll import HOURLY_BUDGET, Poller
from core.github.webhooks import EventOutcome, Policy, apply_event

pytestmark = pytest.mark.integration

INSTALLATION = 99
REPO = {"id": 1, "name": "api", "full_name": "octo/api", "default_branch": "main"}
BASE = "b" * 40
# When test PRs were opened: after the repo row (a new PR), or before it.
LATER = "2099-01-01T00:00:00Z"
EARLIER = "2020-01-01T00:00:00Z"


def pr(
    number: int = 7, head: str = "a" * 40, *, draft: bool = False, created: str = LATER
) -> dict[str, Any]:
    """A pull request as GET /repos/{repo}/pulls lists it (and a webhook carries it)."""
    return {
        "number": number,
        "created_at": created,
        "title": f"PR {number}",
        "state": "open",
        "draft": draft,
        "head": {"sha": head},
        "base": {"sha": BASE, "ref": "main"},
    }


class FakeGitHub:
    """The four endpoints the poller reads, answering 304 to a matching ETag,
    the way GitHub does."""

    def __init__(self, login: str = "octo") -> None:
        self.installations: list[dict[str, Any]] = [
            {
                "id": INSTALLATION,
                "account": {"login": login, "id": 7, "type": "User"},
                "repository_selection": "selected",
                "suspended_at": None,
            }
        ]
        self.repos: list[dict[str, Any]] = [dict(REPO)]
        self.heads: dict[str, str] = {"octo/api": "c" * 40}
        self.pulls: dict[str, list[dict[str, Any]]] = {"octo/api": []}
        self.page_size = 100
        self.requests: list[str] = []
        self.not_modified = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        page = int(request.url.params.get("page", "1"))
        link = None
        if path == "/app/installations":
            body: Any = self.installations
        elif path == "/installation/repositories":
            start = (page - 1) * self.page_size
            body = {
                "total_count": len(self.repos),
                "repositories": self.repos[start : start + self.page_size],
            }
            if start + self.page_size < len(self.repos):
                link = f'<{request.url.copy_merge_params({"page": page + 1})}>; rel="next"'
        elif path.endswith("/pulls"):
            body = self.pulls["/".join(path.split("/")[2:4])]
        elif "/commits/" in path:
            sha = self.heads["/".join(path.split("/")[2:4])]
            return self._answer(request, sha.encode(), "text/plain", link)
        else:
            return httpx.Response(404, json={"message": "Not Found"})
        return self._answer(request, json.dumps(body).encode(), "application/json", link)

    def _answer(
        self, request: httpx.Request, body: bytes, content_type: str, link: str | None
    ) -> httpx.Response:
        etag = '"' + hashlib.sha1(body).hexdigest() + '"'
        headers = {"ETag": etag, "X-RateLimit-Remaining": "4990"}
        if request.headers.get("If-None-Match") == etag:
            self.not_modified += 1
            return httpx.Response(304, headers=headers)
        if link:
            headers["Link"] = link
        return httpx.Response(200, content=body, headers={**headers, "Content-Type": content_type})


class Tokens:
    async def get(self, installation_id: int) -> str:
        return f"token-{installation_id}"


class Jobs:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, int]] = []

    async def index_repo(self, repo_id: int) -> None:
        self.jobs.append(("index", repo_id))

    async def review(self, review_id: int, base: str, *, title: str | None = None) -> None:
        self.jobs.append(("review", review_id))


POLICY = Policy(Accounts.parse("octo"), reviews_per_hour=0)


def scenario(
    database_url: str,
    body: Callable[
        [Callable[[], Awaitable[list[tuple[str, int]]]], FakeGitHub, Any], Awaitable[None]
    ],
    *,
    github: FakeGitHub | None = None,
    policy: Policy = POLICY,
) -> None:
    """Runs `body(poll, github, sql)` in one event loop: `poll()` is one cycle
    of a poller and returns the jobs that cycle queued."""
    fake = github or FakeGitHub()

    async def go() -> None:
        engine = create_engine(database_url, pooled=False)
        client = GitHubClient(transport=httpx.MockTransport(fake.handler))
        jobs = Jobs()
        poller = Poller(engine, client, lambda: "app-jwt", Tokens(), policy, jobs)

        async def poll() -> list[tuple[str, int]]:
            before = len(jobs.jobs)
            await poller.poll_once()
            return jobs.jobs[before:]

        async def sql(statement: str, **params: Any) -> list[Any]:
            async with engine.begin() as conn:
                result = await conn.execute(text(statement), params)
                return list(result) if result.returns_rows else []

        try:
            await body(poll, fake, sql)
        finally:
            await client.aclose()
            await engine.dispose()

    asyncio.run(go())


def test_an_installation_and_its_repos_are_mirrored_and_indexed_once(
    migrated_database_url: str,
) -> None:
    async def body(poll: Any, github: FakeGitHub, sql: Any) -> None:
        first = await poll()
        [repo] = await sql("SELECT id, github_id, full_name, status FROM repos")
        assert (repo.github_id, repo.full_name) == (1, "octo/api")
        assert first == [("index", repo.id)]
        [installation] = await sql(
            "SELECT github_installation_id, account_login FROM installations"
        )
        assert tuple(installation) == (INSTALLATION, "octo")
        # Nothing changed: nothing queued, and the lists came back 304.
        github.not_modified = 0
        assert await poll() == []
        assert github.not_modified >= 2

    scenario(migrated_database_url, body)


def test_a_pull_request_opened_pushed_and_reopened(migrated_database_url: str) -> None:
    async def body(poll: Any, github: FakeGitHub, sql: Any) -> None:
        await poll()  # installed, indexed
        github.pulls["octo/api"] = [pr(head="a" * 40)]
        opened = await poll()
        [review] = await sql("SELECT id, status, head_sha FROM reviews")
        assert opened == [("review", review.id)]
        assert await poll() == []  # still open at the same commit

        github.pulls["octo/api"] = [pr(head="d" * 40)]  # a push
        pushed = await poll()
        rows = await sql("SELECT id, status, head_sha FROM reviews ORDER BY id")
        assert [(r.head_sha[0], r.status) for r in rows] == [("a", "superseded"), ("d", "queued")]
        assert pushed == [("review", rows[1].id)]

        github.pulls["octo/api"] = []  # closed
        assert await poll() == []
        # Reopened at a commit already queued: nothing new, as with a webhook.
        github.pulls["octo/api"] = [pr(head="d" * 40)]
        assert await poll() == []
        # Its review failed meanwhile; closed and reopened, it's retried once.
        await sql("UPDATE reviews SET status = 'failed' WHERE id = :id", id=rows[1].id)
        github.pulls["octo/api"] = []
        await poll()
        github.pulls["octo/api"] = [pr(head="d" * 40)]
        reopened = await poll()
        assert len(reopened) == 1 and reopened[0][0] == "review"
        assert await poll() == []

    scenario(migrated_database_url, body)


def test_a_failed_review_isnt_retried_every_minute(migrated_database_url: str) -> None:
    async def body(poll: Any, github: FakeGitHub, sql: Any) -> None:
        await poll()
        github.pulls["octo/api"] = [pr()]
        await poll()
        await sql("UPDATE reviews SET status = 'failed'")
        # Still open, same commit: no event, so no retry (a webhook would
        # need a push or a reopen too).
        for _ in range(3):
            assert await poll() == []
        assert len(await sql("SELECT id FROM reviews")) == 1

    scenario(migrated_database_url, body)


def test_pull_requests_open_before_the_install_wait_for_a_push(
    migrated_database_url: str,
) -> None:
    # A webhook setup reviews nothing until the PR's next push; a first poll
    # must not review every PR that happens to be open (the live probe would
    # have re-reviewed codelens-demo #1-#3).
    github = FakeGitHub()
    github.pulls["octo/api"] = [pr(7, created=EARLIER)]

    async def body(poll: Any, github: FakeGitHub, sql: Any) -> None:
        assert [job[0] for job in await poll()] == ["index"]
        assert await sql("SELECT id FROM reviews") == []
        github.pulls["octo/api"] = [pr(7, "d" * 40, created=EARLIER)]  # pushed
        assert [job[0] for job in await poll()] == ["review"]

    scenario(migrated_database_url, body, github=github)


def test_a_draft_waits_until_it_is_ready(migrated_database_url: str) -> None:
    async def body(poll: Any, github: FakeGitHub, sql: Any) -> None:
        await poll()
        github.pulls["octo/api"] = [pr(draft=True)]
        assert await poll() == []
        github.pulls["octo/api"] = [pr(draft=False)]
        ready = await poll()
        assert [job[0] for job in ready] == ["review"]

    scenario(migrated_database_url, body)


def test_the_default_branch_moving_reindexes_once(migrated_database_url: str) -> None:
    async def body(poll: Any, github: FakeGitHub, sql: Any) -> None:
        await poll()
        [repo] = await sql("SELECT id FROM repos")
        # The first index finished at the current head.
        await sql(
            "UPDATE repos SET status = 'ready', last_indexed_sha = :sha WHERE id = :id",
            sha="c" * 40,
            id=repo.id,
        )
        assert await poll() == []
        github.heads["octo/api"] = "e" * 40  # a push to main
        assert await poll() == [("index", repo.id)]
        await sql("UPDATE repos SET status = 'pending' WHERE id = :id", id=repo.id)
        assert await poll() == []  # under way
        # It failed: no retry until the branch moves again.
        await sql("UPDATE repos SET status = 'failed' WHERE id = :id", id=repo.id)
        assert await poll() == []
        github.heads["octo/api"] = "f" * 40
        assert await poll() == [("index", repo.id)]

    scenario(migrated_database_url, body)


def test_a_restart_catches_up_on_what_happened_meanwhile(migrated_database_url: str) -> None:
    github = FakeGitHub()
    github.pulls["octo/api"] = [pr(7, "a" * 40), pr(8, "1" * 40)]

    async def before(poll: Any, github: FakeGitHub, sql: Any) -> None:
        await poll()
        await sql("UPDATE repos SET status = 'ready', last_indexed_sha = :sha", sha="c" * 40)

    scenario(migrated_database_url, before, github=github)
    # Stopped. Meanwhile: #8 pushed, #9 opened, main moved.
    github.pulls["octo/api"] = [pr(7, "a" * 40), pr(8, "2" * 40), pr(9, "3" * 40)]
    github.heads["octo/api"] = "e" * 40

    async def after(poll: Any, github: FakeGitHub, sql: Any) -> None:
        jobs = await poll()  # a new poller: everything looks new
        [repo] = await sql("SELECT id FROM repos")
        reviews = await sql("SELECT id, pr_number, head_sha FROM reviews WHERE status = 'queued'")
        assert sorted((r.pr_number, r.head_sha[0]) for r in reviews) == [
            (7, "a"),
            (8, "2"),
            (9, "3"),
        ]
        # #7 was already queued before the stop: only #8, #9 and main are new.
        new = {r.id for r in reviews if r.pr_number != 7}
        assert sorted(jobs) == sorted([("index", repo.id)] + [("review", i) for i in new])

    scenario(migrated_database_url, after, github=github)


def test_a_webhook_and_the_poller_seeing_one_push_queue_one_review(
    migrated_database_url: str,
) -> None:
    async def body(poll: Any, github: FakeGitHub, sql: Any) -> None:
        await poll()
        payload = {
            "action": "synchronize",
            "number": 7,
            "installation": {"id": INSTALLATION},
            "repository": REPO,
            "pull_request": pr(),
        }
        engine = create_engine(migrated_database_url, pooled=False)
        try:
            async with engine.begin() as conn:
                outcome: EventOutcome = await apply_event(
                    conn, "pull_request", "synchronize", payload, POLICY
                )
        finally:
            await engine.dispose()
        assert len(outcome.reviews) == 1
        github.pulls["octo/api"] = [pr()]
        assert await poll() == []
        assert len(await sql("SELECT id FROM reviews")) == 1

    scenario(migrated_database_url, body)


def test_concurrent_intakes_of_one_push_queue_one_review(migrated_database_url: str) -> None:
    # Without the per-PR lock, both would see no review and insert one each.
    async def body(poll: Any, github: FakeGitHub, sql: Any) -> None:
        await poll()
        payload = {
            "action": "synchronize",
            "number": 7,
            "installation": {"id": INSTALLATION},
            "repository": REPO,
            "pull_request": pr(),
        }
        engine = create_engine(migrated_database_url, pooled=False)
        first_in = asyncio.Event()

        async def intake(hold: bool) -> int:
            async with engine.begin() as conn:
                outcome = await apply_event(conn, "pull_request", "synchronize", payload, POLICY)
                if hold:
                    first_in.set()
                    await asyncio.sleep(0.5)  # the other one must wait for this commit
                return len(outcome.reviews)

        async def second() -> int:
            await first_in.wait()
            return await intake(False)

        try:
            queued = await asyncio.gather(intake(True), second())
        finally:
            await engine.dispose()
        assert sorted(queued) == [0, 1]
        assert len(await sql("SELECT id FROM reviews")) == 1

    scenario(migrated_database_url, body)


def test_a_stranger_installation_is_never_asked_about(migrated_database_url: str) -> None:
    github = FakeGitHub(login="stranger")
    github.pulls["octo/api"] = [pr()]

    async def body(poll: Any, github: FakeGitHub, sql: Any) -> None:
        assert await poll() == []
        assert await sql("SELECT id FROM installations") == []
        assert github.requests == ["/app/installations"]

    scenario(migrated_database_url, body, github=github)


def test_repos_and_installations_that_go_away(migrated_database_url: str) -> None:
    async def body(poll: Any, github: FakeGitHub, sql: Any) -> None:
        github.repos.append({**REPO, "id": 2, "name": "web", "full_name": "octo/web"})
        github.heads["octo/web"] = "c" * 40
        github.pulls["octo/web"] = []
        await poll()
        assert len(await sql("SELECT id FROM repos WHERE installation_id IS NOT NULL")) == 2
        github.repos.pop()  # removed from the installation
        await poll()
        rows = await sql("SELECT full_name, installation_id FROM repos ORDER BY github_id")
        assert [(r.full_name, r.installation_id is None) for r in rows] == [
            ("octo/api", False),
            ("octo/web", True),
        ]
        github.installations.clear()  # uninstalled
        await poll()
        assert await sql("SELECT id FROM installations") == []

    scenario(migrated_database_url, body)


def test_a_suspended_installation_gets_no_token(migrated_database_url: str) -> None:
    async def body(poll: Any, github: FakeGitHub, sql: Any) -> None:
        await poll()
        github.installations[0]["suspended_at"] = "2026-10-17T10:00:00Z"
        github.pulls["octo/api"] = [pr()]
        github.requests.clear()
        assert await poll() == []
        assert github.requests == ["/app/installations"]
        [row] = await sql("SELECT suspended_at FROM installations")
        assert row.suspended_at is not None

    scenario(migrated_database_url, body)


def test_every_page_of_repositories_is_read(migrated_database_url: str) -> None:
    github = FakeGitHub()
    github.page_size = 1
    github.repos.append({**REPO, "id": 2, "name": "web", "full_name": "octo/web"})
    github.heads["octo/web"] = "c" * 40
    github.pulls["octo/web"] = []

    async def body(poll: Any, github: FakeGitHub, sql: Any) -> None:
        await poll()
        assert len(await sql("SELECT id FROM repos")) == 2

    scenario(migrated_database_url, body, github=github)


def test_many_repos_stretch_the_interval() -> None:
    poller = Poller(None, None, lambda: "", Tokens(), POLICY, Jobs(), interval_s=60)  # type: ignore[arg-type]
    from core.github.poll import Cycle

    assert poller._next_interval(Cycle(cost={1: 1 + 2 * 10})) == 60
    # 1 + 2 * 100 requests a cycle: at 60 s that's 12,060 an hour.
    assert poller._next_interval(Cycle(cost={1: 201})) == -(-201 * 3600 // HOURLY_BUDGET)
