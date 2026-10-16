"""POST /webhooks/github: signature checks first (no database needed), then
delivery dedupe and installation mirroring against a real database."""

import asyncio
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from httpx import Response

from api.dependencies import get_queue
from api.errors import ApiError
from api.main import create_app
from api.queue import QueueUnavailable
from api.routes.webhooks import _read_body
from core.config import Settings
from core.github.webhooks import sign

URL = "/webhooks/github"
SECRET = "unit-webhook-secret"

Sql = Callable[..., list[Any]]


class RecordingQueue:
    def __init__(self, *, down: bool = False) -> None:
        self.jobs: list[tuple[Any, ...]] = []
        self.down = down

    async def index_repo(self, repo_id: int) -> None:
        if self.down:
            raise QueueUnavailable("connection refused")
        self.jobs.append(("index_repo", repo_id))

    async def review(self, review_id: int, base: str, *, title: str | None = None) -> None:
        if self.down:
            raise QueueUnavailable("connection refused")
        self.jobs.append(("review", review_id, base, title))


@contextmanager
def client(settings: Settings, queue: RecordingQueue | None = None) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_queue] = lambda: queue or RecordingQueue()
    with TestClient(app) as c:
        yield c


def deliver(
    c: TestClient,
    secret: str,
    event: str,
    payload: dict[str, Any],
    *,
    delivery: str = "d-1",
    signature: str | None = None,
) -> Response:
    body = json.dumps(payload).encode()
    return c.post(
        URL,
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": signature or sign(secret, body),
            "Content-Type": "application/json",
        },
    )


def installation(action: str, *, id_: int = 99, suspended_at: str | None = None) -> dict:
    return {
        "action": action,
        "installation": {
            "id": id_,
            "account": {"id": 7, "login": "octo", "type": "User"},
            "repository_selection": "selected",
            "suspended_at": suspended_at,
        },
    }


# --- no database: everything here is refused before the session is used


def offline(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://user:pass@127.0.0.1:1/db",
        "redis_url": "redis://127.0.0.1:1/0",
        "github_webhook_secret": SECRET,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def test_a_bad_signature_is_401() -> None:
    with client(offline()) as c:
        forged = deliver(c, "guessed-secret", "ping", {"zen": "x"})
        unsigned = c.post(URL, content=b"{}", headers={"X-GitHub-Event": "ping"})
    for response in (forged, unsigned):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_signature"


def test_an_unconfigured_secret_fails_closed() -> None:
    with client(offline(github_webhook_secret="")) as c:
        response = deliver(c, "", "ping", {})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "webhook_not_configured"


def test_demo_mode_refuses_webhooks() -> None:
    with client(offline(demo_mode=True)) as c:
        response = deliver(c, SECRET, "ping", {})
    assert response.status_code == 403


def test_signed_but_malformed_deliveries_are_400() -> None:
    with client(offline()) as c:
        body = b"not json"
        bad_json = c.post(
            URL,
            content=body,
            headers={
                "X-GitHub-Event": "ping",
                "X-GitHub-Delivery": "d",
                "X-Hub-Signature-256": sign(SECRET, body),
            },
        )
        no_delivery_id = c.post(
            URL,
            content=b"{}",
            headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sign(SECRET, b"{}")},
        )
    assert (bad_json.status_code, bad_json.json()["error"]["code"]) == (400, "invalid_payload")
    assert no_delivery_id.json()["error"]["code"] == "missing_header"


# --- against the test database


@pytest.mark.integration
def test_installation_lifecycle_is_mirrored(settings: Settings, sql: Sql) -> None:
    secret = settings.github_webhook_secret.get_secret_value()
    with client(settings) as c:
        created = deliver(c, secret, "installation", installation("created"), delivery="d-1")
        [row] = sql("SELECT * FROM installations")
        assert (row.github_installation_id, row.account_login, row.account_type) == (
            99,
            "octo",
            "User",
        )
        assert row.repository_selection == "selected"
        assert row.suspended_at is None

        sql("INSERT INTO repos (full_name, installation_id) VALUES ('octo/app', :id)", id=row.id)
        deliver(
            c,
            secret,
            "installation",
            installation("suspend", suspended_at="2026-10-12T17:00:00Z"),
            delivery="d-2",
        )
        assert sql("SELECT suspended_at FROM installations")[0].suspended_at is not None

        deleted = deliver(c, secret, "installation", installation("deleted"), delivery="d-3")

    assert created.status_code == 202
    assert created.json() == {"delivery_id": "d-1", "event": "installation", "status": "processed"}
    assert deleted.json()["status"] == "processed"
    assert sql("SELECT id FROM installations") == []
    # The repo stays, detached from the gone installation.
    assert sql("SELECT installation_id FROM repos WHERE full_name = 'octo/app'")[0][0] is None


@pytest.mark.integration
def test_a_redelivery_is_handled_once(settings: Settings, sql: Sql) -> None:
    secret = settings.github_webhook_secret.get_secret_value()
    with client(settings) as c:
        first = deliver(c, secret, "installation", installation("created"), delivery="same")
        again = deliver(c, secret, "installation", installation("created"), delivery="same")

    assert first.json()["status"] == "processed"
    assert again.json()["status"] == "duplicate"
    [delivery] = sql("SELECT * FROM webhook_deliveries")
    assert (delivery.event, delivery.action, delivery.installation_id) == (
        "installation",
        "created",
        99,
    )
    assert delivery.processed_at is not None


@pytest.mark.integration
def test_a_delivery_that_failed_halfway_is_handled_again(settings: Settings, sql: Sql) -> None:
    # Recorded but never marked processed: e.g. a crash after the insert.
    sql(
        "INSERT INTO webhook_deliveries (delivery_id, event, action)"
        " VALUES ('retry', 'installation', 'created')"
    )
    secret = settings.github_webhook_secret.get_secret_value()
    with client(settings) as c:
        response = deliver(c, secret, "installation", installation("created"), delivery="retry")

    assert response.json()["status"] == "processed"
    assert len(sql("SELECT id FROM installations")) == 1


@pytest.mark.integration
def test_events_with_nothing_to_do_are_recorded(settings: Settings, sql: Sql) -> None:
    # A pull request on a repo the App doesn't cover: recorded, no work.
    secret = settings.github_webhook_secret.get_secret_value()
    payload = pr_event("opened", repo_github_id=12345)
    with client(settings) as c:
        response = deliver(c, secret, "pull_request", payload, delivery="pr-1")

    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    [delivery] = sql("SELECT event, action, installation_id, processed_at FROM webhook_deliveries")
    assert (delivery.event, delivery.action, delivery.installation_id) == (
        "pull_request",
        "opened",
        99,
    )
    assert delivery.processed_at is not None


# --- repositories and pushes (step 3a)


def repo_payload(github_id: int, full_name: str) -> dict[str, Any]:
    return {"id": github_id, "name": full_name.split("/")[1], "full_name": full_name}


@pytest.mark.integration
def test_a_new_installation_indexes_its_repositories(settings: Settings, sql: Sql) -> None:
    secret = settings.github_webhook_secret.get_secret_value()
    payload = {
        **installation("created"),
        "repositories": [repo_payload(1, "octo/api"), repo_payload(2, "octo/web")],
    }
    queue = RecordingQueue()
    with client(settings, queue) as c:
        response = deliver(c, secret, "installation", payload)

    assert response.json()["status"] == "processed"
    repos = sql(
        "SELECT r.id, r.github_id, r.full_name, r.source, i.github_installation_id"
        " FROM repos r JOIN installations i ON i.id = r.installation_id ORDER BY r.github_id"
    )
    assert [(r.github_id, r.full_name, r.source, r.github_installation_id) for r in repos] == [
        (1, "octo/api", "github", 99),
        (2, "octo/web", "github", 99),
    ]
    assert queue.jobs == [("index_repo", r.id) for r in repos]


@pytest.mark.integration
def test_repositories_added_and_removed(settings: Settings, sql: Sql) -> None:
    secret = settings.github_webhook_secret.get_secret_value()
    queue = RecordingQueue()
    with client(settings, queue) as c:
        deliver(
            c,
            secret,
            "installation",
            {**installation("created"), "repositories": [repo_payload(1, "octo/api")]},
            delivery="d-1",
        )
        changed = deliver(
            c,
            secret,
            "installation_repositories",
            {
                **installation("added"),
                "repositories_added": [repo_payload(2, "octo/web")],
                "repositories_removed": [repo_payload(1, "octo/api")],
            },
            delivery="d-2",
        )

    assert changed.json()["status"] == "processed"
    rows = {
        r.full_name: r
        for r in sql("SELECT id, full_name, installation_id FROM repos ORDER BY github_id")
    }
    assert rows["octo/api"].installation_id is None  # detached, data kept
    assert rows["octo/web"].installation_id is not None
    assert queue.jobs == [("index_repo", rows["octo/api"].id), ("index_repo", rows["octo/web"].id)]


@pytest.mark.integration
def test_a_push_to_the_default_branch_reindexes(settings: Settings, sql: Sql) -> None:
    secret = settings.github_webhook_secret.get_secret_value()

    def push(ref: str, *, repo_id: int = 1, deleted: bool = False) -> dict[str, Any]:
        return {
            "ref": ref,
            "deleted": deleted,
            "installation": {"id": 99},
            "repository": {**repo_payload(repo_id, "octo/api"), "default_branch": "trunk"},
        }

    queue = RecordingQueue()
    with client(settings, queue) as c:
        deliver(
            c,
            secret,
            "installation",
            {**installation("created"), "repositories": [repo_payload(1, "octo/api")]},
            delivery="d-0",
        )
        queue.jobs.clear()
        default = deliver(c, secret, "push", push("refs/heads/trunk"), delivery="d-1")
        feature = deliver(c, secret, "push", push("refs/heads/feature"), delivery="d-2")
        deleted = deliver(c, secret, "push", push("refs/heads/trunk", deleted=True), delivery="d-3")
        unknown = deliver(c, secret, "push", push("refs/heads/trunk", repo_id=7), delivery="d-4")

    [repo] = sql("SELECT id, default_branch FROM repos")
    assert default.json()["status"] == "processed"
    assert [r.json()["status"] for r in (feature, deleted, unknown)] == ["ignored"] * 3
    assert repo.default_branch == "trunk"  # learned from the push
    assert queue.jobs == [("index_repo", repo.id)]


@pytest.mark.integration
def test_jobs_lost_to_a_broker_outage_come_back_on_redelivery(settings: Settings, sql: Sql) -> None:
    secret = settings.github_webhook_secret.get_secret_value()
    payload = {**installation("created"), "repositories": [repo_payload(1, "octo/api")]}

    with client(settings, RecordingQueue(down=True)) as c:
        failed = deliver(c, secret, "installation", payload, delivery="same")
    assert failed.status_code == 503
    # The state is saved, but the delivery isn't marked processed...
    assert sql("SELECT processed_at FROM webhook_deliveries")[0].processed_at is None

    # ...so GitHub's "Redeliver" queues the job instead of being skipped.
    queue = RecordingQueue()
    with client(settings, queue) as c:
        redelivered = deliver(c, secret, "installation", payload, delivery="same")
    assert redelivered.json()["status"] == "processed"
    assert len(queue.jobs) == 1
    assert sql("SELECT processed_at FROM webhook_deliveries")[0].processed_at is not None


# --- pull requests (step 3b)


def pr_event(
    action: str,
    *,
    head: str = "a" * 40,
    draft: bool = False,
    state: str = "open",
    repo_github_id: int = 1,
    number: int = 7,
) -> dict[str, Any]:
    return {
        "action": action,
        "number": number,
        "installation": {"id": 99},
        "repository": repo_payload(repo_github_id, "octo/api"),
        "pull_request": {
            "number": number,
            "title": "Make @admin happy",
            "state": state,
            "draft": draft,
            "head": {"sha": head},
            "base": {"sha": "b" * 40, "ref": "main"},
        },
    }


@pytest.fixture
def installed(settings: Settings) -> str:
    """octo/api installed (repo github id 1); returns the webhook secret."""
    secret = settings.github_webhook_secret.get_secret_value()
    payload = {**installation("created"), "repositories": [repo_payload(1, "octo/api")]}
    with client(settings) as c:
        deliver(c, secret, "installation", payload, delivery="install")
    return secret


@pytest.mark.integration
def test_a_pull_request_queues_a_review(settings: Settings, sql: Sql, installed: str) -> None:
    queue = RecordingQueue()
    with client(settings, queue) as c:
        response = deliver(c, installed, "pull_request", pr_event("opened"), delivery="pr")

    assert response.json()["status"] == "processed"
    [review] = sql("SELECT id, source, pr_number, title, head_sha, status FROM reviews")
    assert (review.source, review.pr_number, review.head_sha, review.status) == (
        "github",
        7,
        "a" * 40,
        "queued",
    )
    assert review.title == "Make @admin happy"  # kept for the dashboard (Q19)
    # The base is the PR's base tip; the worker diffs against its merge-base.
    assert queue.jobs == [("review", review.id, "b" * 40, "Make @admin happy")]


@pytest.mark.integration
@pytest.mark.parametrize(
    "event",
    [
        pr_event("opened", draft=True),  # drafts wait for ready_for_review
        pr_event("closed", state="closed"),
        pr_event("edited"),  # title or description only: no new code
        pr_event("labeled"),
    ],
    ids=["draft", "closed", "edited", "labeled"],
)
def test_pull_request_events_without_new_code_are_ignored(
    settings: Settings, sql: Sql, installed: str, event: dict[str, Any]
) -> None:
    queue = RecordingQueue()
    with client(settings, queue) as c:
        response = deliver(c, installed, "pull_request", event, delivery="pr")

    assert response.json()["status"] == "ignored"
    assert queue.jobs == []
    assert sql("SELECT id FROM reviews") == []


@pytest.mark.integration
def test_a_head_is_reviewed_once_and_newer_pushes_supersede(
    settings: Settings, sql: Sql, installed: str
) -> None:
    queue = RecordingQueue()
    with client(settings, queue) as c:
        deliver(c, installed, "pull_request", pr_event("opened"), delivery="1")
        # Reopened without new commits (or a new delivery of the same push).
        same = deliver(c, installed, "pull_request", pr_event("reopened"), delivery="2")
        newer = deliver(
            c, installed, "pull_request", pr_event("synchronize", head="c" * 40), delivery="3"
        )

    assert same.json()["status"] == "ignored"
    assert newer.json()["status"] == "processed"
    rows = sql("SELECT head_sha, status, error FROM reviews ORDER BY id")
    assert [(r.head_sha[0], r.status) for r in rows] == [("a", "superseded"), ("c", "queued")]
    assert "superseded by a push of ccccccc" in rows[0].error
    assert [job[0] for job in queue.jobs] == ["review", "review"]


# --- whose installations, and how much (Phase 7 step 4a: D119, D120)


def allowing(settings: Settings, accounts: str, **overrides: object) -> Settings:
    return settings.model_copy(update={"github_allowed_accounts": accounts, **overrides})


@pytest.mark.integration
@pytest.mark.parametrize("accounts", ["someone-else", ""], ids=["not-listed", "unset"])
def test_installations_by_other_accounts_are_ignored(
    settings: Settings, sql: Sql, accounts: str
) -> None:
    # Unset fails closed: nobody, not everybody.
    secret = settings.github_webhook_secret.get_secret_value()
    payload = {**installation("created"), "repositories": [repo_payload(1, "octo/api")]}
    queue = RecordingQueue()
    with client(allowing(settings, accounts), queue) as c:
        response = deliver(c, secret, "installation", payload)

    assert response.json()["status"] == "ignored"
    assert sql("SELECT id FROM installations") == []
    assert sql("SELECT id FROM repos") == []
    assert queue.jobs == []
    # Still logged as delivered, so GitHub's redelivery doesn't repeat it.
    assert sql("SELECT processed_at FROM webhook_deliveries")[0].processed_at is not None


@pytest.mark.integration
def test_accounts_match_whatever_the_case(settings: Settings, sql: Sql) -> None:
    secret = settings.github_webhook_secret.get_secret_value()
    with client(allowing(settings, " Someone , OCTO ")) as c:
        response = deliver(c, secret, "installation", installation("created"))
    assert response.json()["status"] == "processed"
    assert len(sql("SELECT id FROM installations")) == 1


@pytest.mark.integration
def test_an_account_taken_off_the_list_stops_at_once(
    settings: Settings, sql: Sql, installed: str
) -> None:
    # octo/api was installed while octo was listed; now the list moves on.
    push = {
        "ref": "refs/heads/main",
        "installation": {"id": 99},
        "repository": {**repo_payload(1, "octo/api"), "default_branch": "main"},
    }
    queue = RecordingQueue()
    with client(allowing(settings, "someone-else"), queue) as c:
        pr = deliver(c, installed, "pull_request", pr_event("opened"), delivery="pr")
        pushed = deliver(c, installed, "push", push, delivery="push")
        suspended = deliver(
            c,
            installed,
            "installation",
            installation("suspend", suspended_at="2026-10-15T09:00:00Z"),
            delivery="suspend",
        )
        uninstalled = deliver(c, installed, "installation", installation("deleted"), delivery="del")

    assert [r.json()["status"] for r in (pr, pushed, suspended)] == ["ignored"] * 3
    assert queue.jobs == []
    assert sql("SELECT id FROM reviews") == []
    # An uninstall is still mirrored: dropping what it leaves is always safe.
    assert uninstalled.json()["status"] == "processed"
    assert sql("SELECT id FROM installations") == []


@pytest.mark.integration
def test_a_suspended_installation_gets_no_work_until_unsuspended(
    settings: Settings, sql: Sql, installed: str
) -> None:
    # suspended_at was stored but never read (Phase 9 step 4, D130): pushes and
    # pull requests kept queueing work for a suspended installation.
    push = {
        "ref": "refs/heads/main",
        "installation": {"id": 99},
        "repository": {**repo_payload(1, "octo/api"), "default_branch": "main"},
    }
    queue = RecordingQueue()
    with client(allowing(settings, "octo"), queue) as c:
        deliver(
            c,
            installed,
            "installation",
            installation("suspend", suspended_at="2026-10-16T09:00:00Z"),
            delivery="suspend",
        )
        pr_while_suspended = deliver(
            c, installed, "pull_request", pr_event("opened"), delivery="pr1"
        )
        push_while_suspended = deliver(c, installed, "push", push, delivery="push1")
        deliver(c, installed, "installation", installation("unsuspend"), delivery="unsuspend")
        # The next push after unsuspending. (Not the same body again: that is
        # a replay, refused whatever its delivery id, D134.)
        pr_after = deliver(c, installed, "pull_request", pr_event("synchronize"), delivery="pr2")

    assert pr_while_suspended.json()["status"] == "ignored"
    assert push_while_suspended.json()["status"] == "ignored"
    assert pr_after.json()["status"] == "processed"
    assert [job[0] for job in queue.jobs] == ["review"]


@pytest.mark.integration
def test_reviews_over_the_hourly_limit_are_recorded_not_queued(
    settings: Settings, sql: Sql, installed: str
) -> None:
    queue = RecordingQueue()
    with client(allowing(settings, "octo", github_reviews_per_hour=2), queue) as c:
        for n in (1, 2, 3):
            deliver(c, installed, "pull_request", pr_event("opened", number=n), delivery=f"pr{n}")

    rows = sql("SELECT pr_number, status, error, finished_at FROM reviews ORDER BY pr_number")
    assert [(r.pr_number, r.status) for r in rows] == [(1, "queued"), (2, "queued"), (3, "failed")]
    assert "octo started 2 reviews in the last hour" in rows[2].error
    assert "GITHUB_REVIEWS_PER_HOUR=2" in rows[2].error
    assert rows[2].finished_at is not None
    assert [job[0] for job in queue.jobs] == ["review", "review"]


@pytest.mark.integration
def test_a_newer_push_to_the_same_pr_is_not_held_back_by_the_one_it_replaces(
    settings: Settings, sql: Sql, installed: str
) -> None:
    queue = RecordingQueue()
    with client(allowing(settings, "octo", github_reviews_per_hour=1), queue) as c:
        deliver(c, installed, "pull_request", pr_event("opened"), delivery="1")
        newer = deliver(
            c, installed, "pull_request", pr_event("synchronize", head="c" * 40), delivery="2"
        )

    assert newer.json()["status"] == "processed"
    rows = sql("SELECT head_sha, status FROM reviews ORDER BY id")
    assert [(r.head_sha[0], r.status) for r in rows] == [("a", "superseded"), ("c", "queued")]


@pytest.mark.integration
def test_a_held_back_commit_is_reviewed_once_the_hour_has_room(
    settings: Settings, sql: Sql, installed: str
) -> None:
    capped = allowing(settings, "octo", github_reviews_per_hour=1)
    queue = RecordingQueue()
    with client(capped, queue) as c:
        deliver(c, installed, "pull_request", pr_event("opened", number=1), delivery="1")
        held = deliver(c, installed, "pull_request", pr_event("opened", number=2), delivery="2")
        # PR 1's review ran two hours ago...
        sql(
            "UPDATE reviews SET status = 'completed', started_at = now() - interval '2 hours'"
            " WHERE pr_number = 1"
        )
        # ...so reopening PR 2 (same commit) reviews it: a failed review may be retried.
        retried = deliver(
            c, installed, "pull_request", pr_event("reopened", number=2), delivery="3"
        )

    assert held.json()["status"] == "processed"
    assert retried.json()["status"] == "processed"
    rows = sql("SELECT status FROM reviews WHERE pr_number = 2 ORDER BY id")
    assert [r.status for r in rows] == ["failed", "queued"]
    assert [job[0] for job in queue.jobs] == ["review", "review"]


@pytest.mark.integration
def test_no_cap_when_the_limit_is_zero(settings: Settings, sql: Sql, installed: str) -> None:
    queue = RecordingQueue()
    with client(allowing(settings, "octo", github_reviews_per_hour=0), queue) as c:
        for n in range(1, 13):
            deliver(c, installed, "pull_request", pr_event("opened", number=n), delivery=f"{n}")
    assert len(queue.jobs) == 12


# --- Phase 9 step 8 (D134): what an unauthenticated sender or a replay can do


def test_a_body_past_the_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # The body was read whole, of any size, before the signature check.
    monkeypatch.setattr("api.routes.webhooks.MAX_BODY", 1024)
    with client(offline()) as c:
        declared = c.post(URL, content=b"x" * 4096, headers={"X-GitHub-Event": "ping"})
        small = c.post(URL, content=b"{}", headers={"X-GitHub-Event": "ping"})

    assert declared.status_code == 413
    assert declared.json()["error"]["code"] == "payload_too_large"
    assert small.status_code == 401  # under the cap: on to the signature check


def test_a_body_without_a_length_is_cut_off_while_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A chunked upload declares no length; reading stops once past the cap.
    monkeypatch.setattr("api.routes.webhooks.MAX_BODY", 1024)
    received: list[int] = []

    async def receive() -> dict[str, Any]:
        received.append(1)
        return {"type": "http.request", "body": b"x" * 512, "more_body": len(received) < 64}

    request = Request({"type": "http", "method": "POST", "headers": []}, receive)
    with pytest.raises(ApiError) as refused:
        asyncio.run(_read_body(request))

    assert refused.value.status_code == 413
    assert len(received) == 3  # 1536 bytes, then it stopped asking


@pytest.mark.integration
def test_a_replayed_body_under_a_new_delivery_id_is_a_duplicate(
    settings: Settings, sql: Sql, installed: str
) -> None:
    # The audit's replay: PR opened at head a, then pushed to c; c is
    # reviewed. Re-sending the old "opened" body under a fresh id queued a
    # new review of the stale head a.
    queue = RecordingQueue()
    with client(settings, queue) as c:
        deliver(c, installed, "pull_request", pr_event("opened"), delivery="d-1")
        deliver(
            c, installed, "pull_request", pr_event("synchronize", head="c" * 40), delivery="d-2"
        )
        sql("UPDATE reviews SET status = 'completed' WHERE head_sha = :c", c="c" * 40)
        replay = deliver(c, installed, "pull_request", pr_event("opened"), delivery="d-replay")
        # And an uninstall undone by replaying the "created" body.
        uninstall = installation("deleted")
        deliver(c, installed, "installation", uninstall, delivery="d-uninstall")
        payload = {**installation("created"), "repositories": [repo_payload(1, "octo/api")]}
        reinstall = deliver(c, installed, "installation", payload, delivery="d-reinstall")

    assert replay.json()["status"] == "duplicate"
    assert reinstall.json()["status"] == "duplicate"
    rows = sql("SELECT head_sha, status FROM reviews ORDER BY id")
    assert [(r.head_sha[0], r.status) for r in rows] == [("a", "superseded"), ("c", "completed")]
    assert [job[0] for job in queue.jobs] == ["review", "review"]  # nothing queued for them
    assert sql("SELECT id FROM installations") == []  # still uninstalled
    # The same id (GitHub's own redelivery) still finds its row, as before.
    with client(settings, queue) as c:
        again = deliver(c, installed, "pull_request", pr_event("opened"), delivery="d-1")
    assert again.json()["status"] == "duplicate"
