"""The overview's API (Phase 10 step 3): reviews across repos and the stats,
each limited to the repos the caller may see (D111), eval runs left out unless
asked for (D155). Real Postgres."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.auth import issue_token
from api.main import create_app
from core.config import Settings

pytestmark = pytest.mark.integration

Sql = Callable[..., list[Any]]


@contextmanager
def api(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client


def bearer(settings: Settings, login: str, installations: tuple[int, ...] = ()) -> dict[str, str]:
    secret = settings.api_jwt_secret.get_secret_value()
    token = issue_token(secret, "42", login, installations=installations)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(sql: Sql) -> dict[str, int]:
    """Two GitHub repos under different installations. octo/app has: today, a
    completed review (30 s, 2 posted findings and 1 dropped), a failed one, a
    queued one and an eval run; and a completed one 10 days ago. other/lib has
    one review today."""
    [octo, other] = sql(
        "INSERT INTO installations (github_installation_id, account_id, account_login,"
        " account_type) VALUES (777, 1, 'octo', 'User'), (888, 2, 'other', 'User')"
        " RETURNING id"
    )
    [app] = sql(
        "INSERT INTO repos (github_id, full_name, source, installation_id, status)"
        " VALUES (5, 'octo/app', 'github', :inst, 'ready') RETURNING id",
        inst=octo.id,
    )
    [lib] = sql(
        "INSERT INTO repos (github_id, full_name, source, installation_id, status)"
        " VALUES (6, 'other/lib', 'github', :inst, 'indexing') RETURNING id",
        inst=other.id,
    )

    def review(
        repo: int, status: str, source: str = "github", age: timedelta = timedelta(minutes=1)
    ) -> int:
        created = datetime.now(UTC) - age
        finished = created + timedelta(seconds=30) if status in ("completed", "failed") else None
        return sql(
            "INSERT INTO reviews (repo_id, source, pr_number, title, head_sha, status,"
            " created_at, finished_at)"
            " VALUES (:repo, :source, 1, 'A change', 'abc', :status, :created, :finished)"
            " RETURNING id",
            repo=repo,
            source=source,
            status=status,
            created=created,
            finished=finished,
        )[0].id

    old = review(app.id, "completed", age=timedelta(days=10))
    done = review(app.id, "completed")
    failed = review(app.id, "failed")
    evaluated = review(app.id, "completed", source="eval")
    queued = review(app.id, "queued")
    elsewhere = review(lib.id, "completed")
    for severity, status in (
        ("critical", "posted"),
        ("warning", "posted"),
        ("critical", "dropped"),
    ):
        sql(
            "INSERT INTO findings (review_id, file_path, line_start, line_end, severity,"
            " category, title, explanation, evidence_node_ids, status)"
            " VALUES (:review, 'a.py', 1, 1, :severity, 'bug', 't', 'e', '{}', :status)",
            review=done,
            severity=severity,
            status=status,
        )
    return {
        "app": app.id,
        "lib": lib.id,
        "old": old,
        "done": done,
        "failed": failed,
        "eval": evaluated,
        "queued": queued,
        "elsewhere": elsewhere,
    }


def test_reviews_across_repos_are_the_visible_ones_newest_first(
    settings: Settings, world: dict[str, int]
) -> None:
    with api(settings) as c:
        page = c.get("/api/v1/reviews", headers=bearer(settings, "octo", (777,))).json()
        stranger = c.get("/api/v1/reviews", headers=bearer(settings, "nobody")).json()

    ids = [r["id"] for r in page["items"]]
    # Not other/lib's, and not the eval run (D155).
    assert ids == [world["queued"], world["failed"], world["done"], world["old"]]
    assert {r["repo_full_name"] for r in page["items"]} == {"octo/app"}
    done = next(r for r in page["items"] if r["id"] == world["done"])
    assert done["counts"] == {"critical": 1, "warning": 1, "suggestion": 0}  # posted only
    assert page["next_cursor"] is None
    assert stranger == {"items": [], "next_cursor": None}


def test_reviews_filter_page_and_include_evals(settings: Settings, world: dict[str, int]) -> None:
    headers = bearer(settings, "octo", (777,))
    with api(settings) as c:
        evals = c.get("/api/v1/reviews?include_eval=true", headers=headers).json()
        completed = c.get("/api/v1/reviews?status=completed", headers=headers).json()
        first = c.get("/api/v1/reviews?limit=2", headers=headers).json()
        second = c.get(
            f"/api/v1/reviews?limit=2&cursor={first['next_cursor']}", headers=headers
        ).json()

    assert world["eval"] in [r["id"] for r in evals["items"]]
    assert [r["id"] for r in completed["items"]] == [world["done"], world["old"]]
    assert [r["id"] for r in first["items"]] == [world["queued"], world["failed"]]
    assert [r["id"] for r in second["items"]] == [world["done"], world["old"]]
    assert second["next_cursor"] is None


def test_stats_count_the_window_for_visible_repos(
    settings: Settings, world: dict[str, int]
) -> None:
    with api(settings) as c:
        stats = c.get("/api/v1/stats?days=7", headers=bearer(settings, "octo", (777,))).json()

    # Today's three (not the 10-day-old one, the eval run, or other/lib's).
    assert stats["reviews"] == {"total": 3, "completed": 1, "failed": 1, "superseded": 0}
    assert stats["findings"] == {"critical": 1, "warning": 1, "suggestion": 0}
    assert stats["median_seconds"] == pytest.approx(30.0)
    assert stats["in_progress"] == 1
    assert stats["repos"] == {"total": 1, "ready": 1, "indexing": 0, "pending": 0, "failed": 0}
    assert len(stats["daily"]) == 7
    assert stats["daily"][-1]["reviews"] == 3
    assert stats["daily"][-1]["findings"] == 2
    assert sum(d["reviews"] for d in stats["daily"][:-1]) == 0


def test_stats_include_evals_and_other_repos_only_when_they_should(
    settings: Settings, world: dict[str, int], auth_headers: dict[str, str]
) -> None:
    with api(settings) as c:
        with_evals = c.get(
            "/api/v1/stats?include_eval=true", headers=bearer(settings, "octo", (777,))
        ).json()
        admin = c.get("/api/v1/stats", headers=auth_headers).json()
        stranger = c.get("/api/v1/stats", headers=bearer(settings, "nobody")).json()
        too_long = c.get("/api/v1/stats?days=0", headers=auth_headers)

    assert with_evals["reviews"]["total"] == 4
    # The admin sees both repos (and the shop fixture's, if any, has no reviews).
    assert admin["reviews"]["total"] == 4
    assert admin["repos"]["indexing"] == 1
    assert stranger["reviews"]["total"] == 0
    assert stranger["median_seconds"] is None
    assert stranger["repos"]["total"] == 0
    assert too_long.status_code == 422
