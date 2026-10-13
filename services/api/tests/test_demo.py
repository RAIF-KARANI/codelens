"""A demo instance (DEMO_MODE, planning.md §15): read-only, on Postgres alone,
and showing its (public, exported) repos to every signed-in user (D115)."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.auth import issue_token
from api.main import create_app
from core.config import Settings

pytestmark = pytest.mark.integration

Sql = Callable[..., list[Any]]

# Nothing listens here: a demo instance must never need Redis.
NO_REDIS = "redis://127.0.0.1:1/0"


@pytest.fixture
def demo(settings: Settings) -> Settings:
    return settings.model_copy(update={"demo_mode": True, "redis_url": NO_REDIS})


@pytest.fixture
def finished_review(sql: Sql) -> tuple[int, int]:
    [repo] = sql(
        "INSERT INTO repos (full_name, source) VALUES ('demo/sqlite-utils', 'github') RETURNING id"
    )
    review_id = sql(
        "INSERT INTO reviews (repo_id, source, pr_number, title, head_sha, status, finished_at)"
        " VALUES (:repo, 'github', 1, 'Add a feature', 'abc', 'completed', now()) RETURNING id",
        repo=repo.id,
    )[0].id
    for seq, type_ in enumerate(("stage", "done", "end"), start=1):
        sql(
            "INSERT INTO review_events (review_id, seq, type, payload)"
            " VALUES (:id, :seq, :type, :payload)",
            id=review_id,
            seq=seq,
            type=type_,
            payload=json.dumps({}),
        )
    return repo.id, review_id


def stranger(settings: Settings) -> dict[str, str]:
    """A signed-in GitHub user with no installation and not an admin."""
    token = issue_token(settings.api_jwt_secret.get_secret_value(), "7", "visitor")
    return {"Authorization": f"Bearer {token}"}


def test_a_demo_instance_runs_on_postgres_alone(
    demo: Settings, finished_review: tuple[int, int]
) -> None:
    _, review_id = finished_review
    with TestClient(create_app(demo)) as c:
        health = c.get("/health")
        events = c.get(f"/api/v1/reviews/{review_id}/events", headers=stranger(demo))

    assert health.status_code == 200
    assert health.json()["checks"]["redis"] is None  # not used, so not checked
    # The stored timeline replays and ends, with no Redis doorbell.
    assert [line for line in events.text.splitlines() if line.startswith("event:")] == [
        "event: stage",
        "event: done",
        "event: end",
    ]


def test_every_signed_in_user_sees_the_demo_repos(
    demo: Settings, settings: Settings, finished_review: tuple[int, int]
) -> None:
    repo_id, review_id = finished_review
    with TestClient(create_app(demo)) as c:
        repos = c.get("/api/v1/repos", headers=stranger(demo)).json()
        review = c.get(f"/api/v1/reviews/{review_id}", headers=stranger(demo))
        anonymous = c.get("/api/v1/repos")
    with TestClient(create_app(settings)) as c:
        outside_demo = c.get("/api/v1/repos", headers=stranger(settings)).json()

    assert [r["id"] for r in repos] == [repo_id]
    assert review.json()["title"] == "Add a feature"
    assert anonymous.status_code == 401  # still signed in only (Q7)
    assert outside_demo == []  # the rule is the demo's alone (D111 elsewhere)


def test_a_demo_instance_writes_nothing(demo: Settings, finished_review: tuple[int, int]) -> None:
    repo_id, _ = finished_review
    with TestClient(create_app(demo)) as c:
        index = c.post(f"/api/v1/repos/{repo_id}/index", headers=stranger(demo))
    assert index.status_code == 403
    assert index.json()["error"]["code"] == "demo_mode"
