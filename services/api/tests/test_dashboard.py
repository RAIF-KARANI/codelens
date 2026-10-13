"""The API the dashboard reads (Phase 6 step 1): access checks, and a review's
diff, trace, graph, code nodes and 👍/👎. Real Postgres, fake models."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import jwt
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
def reviewed(shop: Any, sql: Sql, worker_on_fakes: Any) -> int:
    """A finished review of the shop branch (fake model), via the worker."""
    from worker.tasks import review

    review_id = sql(
        "INSERT INTO reviews (repo_id, source, head_sha, status)"
        " VALUES (:repo, 'cli', :head, 'queued') RETURNING id",
        repo=shop.repo_id,
        head=shop.head_sha,
    )[0].id
    review.apply(args=(review_id, shop.base_sha)).get()
    return review_id


# --- access (D111)


def test_without_installations_a_user_sees_nothing(
    settings: Settings, reviewed: int, shop: Any, sql: Sql
) -> None:
    node_id = sql("SELECT id FROM code_nodes LIMIT 1")[0].id
    finding_id = sql("SELECT id FROM findings LIMIT 1")[0].id
    headers = bearer(settings, "stranger")
    with api(settings) as c:
        assert c.get("/api/v1/repos", headers=headers).json() == []
        for url in (
            f"/api/v1/repos/{shop.repo_id}",
            f"/api/v1/repos/{shop.repo_id}/reviews",
            f"/api/v1/reviews/{reviewed}",
            f"/api/v1/reviews/{reviewed}/diff",
            f"/api/v1/reviews/{reviewed}/trace",
            f"/api/v1/reviews/{reviewed}/graph",
            f"/api/v1/reviews/{reviewed}/events",
            f"/api/v1/nodes/{node_id}",
        ):
            response = c.get(url, headers=headers)
            # A 404 like a missing one: a private repo's existence doesn't leak.
            assert (url, response.status_code) == (url, 404)
        vote = c.post(f"/api/v1/findings/{finding_id}/feedback", headers=headers, json={"vote": 1})
        assert vote.status_code == 404


def test_an_installation_grants_its_repos_only(
    settings: Settings, shop: Any, sql: Sql, auth_headers: dict[str, str]
) -> None:
    [inst] = sql(
        "INSERT INTO installations (github_installation_id, account_id, account_login,"
        " account_type) VALUES (777, 1, 'octo', 'User') RETURNING id"
    )
    [github_repo] = sql(
        "INSERT INTO repos (github_id, full_name, source, installation_id)"
        " VALUES (5, 'octo/app', 'github', :inst) RETURNING id",
        inst=inst.id,
    )
    with api(settings) as c:
        member = c.get("/api/v1/repos", headers=bearer(settings, "octo", (777,))).json()
        other = c.get("/api/v1/repos", headers=bearer(settings, "someone", (999,))).json()
        admin = c.get("/api/v1/repos", headers=bearer(settings, "Tester")).json()  # any case

    assert [r["full_name"] for r in member] == ["octo/app"]  # not local/shop
    assert other == []
    assert {r["id"] for r in admin} == {github_repo.id, shop.repo_id}


def test_a_malformed_installations_claim_is_rejected(settings: Settings) -> None:
    secret = settings.api_jwt_secret.get_secret_value()
    token = jwt.encode(
        {"sub": "1", "login": "x", "installations": ["all"], "exp": 4102444800},
        secret,
        algorithm="HS256",
    )
    with api(settings) as c:
        response = c.get("/api/v1/repos", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


# --- what was reviewed, and why


def test_diff_trace_graph_and_nodes(
    settings: Settings, reviewed: int, auth_headers: dict[str, str]
) -> None:
    with api(settings) as c:
        diff = c.get(f"/api/v1/reviews/{reviewed}/diff", headers=auth_headers).json()
        trace = c.get(f"/api/v1/reviews/{reviewed}/trace", headers=auth_headers).json()
        graph = c.get(f"/api/v1/reviews/{reviewed}/graph", headers=auth_headers).json()
        seed = next(n for n in graph["nodes"] if n["is_seed"])
        node = c.get(f"/api/v1/nodes/{seed['node_id']}", headers=auth_headers).json()

    # The diff and the changed node, stored with the review (D110).
    assert "+    if percent > 100:" in diff["diff"]
    assert [(s["qualified_name"], s["change"]) for s in diff["seeds"]] == [
        ("shop.pricing.apply_discount", "modified")
    ]
    # The trace: every retrieved candidate per chunk, with the packing budgets.
    assert set(trace["budgets"]) == {"diff", "changed", "context"}
    [chunk] = trace["chunks"]
    assert chunk["items"] and all("vector_similarity" in i["stage_scores"] for i in chunk["items"])
    # The graph: the seed, its callers (with_tax calls apply_discount), and the
    # edges between them.
    assert seed["qualified_name"] == "shop.pricing.apply_discount"
    names = {n["qualified_name"]: n for n in graph["nodes"]}
    assert names["shop.pricing.with_tax"]["depth"] == 1
    ids = {n["qualified_name"]: n["id"] for n in graph["nodes"]}
    assert {
        "source": ids["shop.pricing.with_tax"],
        "target": ids["shop.pricing.apply_discount"],
        "kind": "calls",
    } in graph["edges"]
    assert any(n["in_context"] for n in graph["nodes"])
    # A node, for the evidence drawer.
    assert node["path"] == "shop/pricing.py"
    assert node["code"].startswith("def apply_discount")


def test_an_old_review_has_no_stored_diff(
    settings: Settings, shop: Any, sql: Sql, auth_headers: dict[str, str]
) -> None:
    review_id = sql(
        "INSERT INTO reviews (repo_id, source, head_sha, status)"
        " VALUES (:repo, 'cli', 'abc', 'completed') RETURNING id",
        repo=shop.repo_id,
    )[0].id
    with api(settings) as c:
        response = c.get(f"/api/v1/reviews/{review_id}/diff", headers=auth_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_recorded"


# --- 👍/👎


def test_feedback_is_one_changeable_vote_per_user(
    settings: Settings, reviewed: int, sql: Sql, auth_headers: dict[str, str]
) -> None:
    finding_id = sql(
        "SELECT id FROM findings WHERE review_id = :id AND status = 'posted'", id=reviewed
    )[0].id
    url = f"/api/v1/findings/{finding_id}/feedback"
    other = bearer(settings, "tester2")  # not an operator: can't see the repo
    with api(settings) as c:
        up = c.post(url, headers=auth_headers, json={"vote": 1}).json()
        down = c.post(url, headers=auth_headers, json={"vote": -1}).json()  # changes it
        detail = c.get(f"/api/v1/reviews/{reviewed}", headers=auth_headers).json()
        cleared = c.delete(url, headers=auth_headers).json()
        invalid = c.post(url, headers=auth_headers, json={"vote": 0})
        hidden = c.post(url, headers=other, json={"vote": 1})

    assert up == {"finding_id": finding_id, "votes": {"up": 1, "down": 0}, "my_vote": 1}
    assert down["votes"] == {"up": 0, "down": 1}
    [finding] = [f for f in detail["findings"] if f["id"] == finding_id]
    assert (finding["votes"], finding["my_vote"]) == ({"up": 0, "down": 1}, -1)
    assert cleared == {"finding_id": finding_id, "votes": {"up": 0, "down": 0}, "my_vote": None}
    assert invalid.status_code == 422
    assert hidden.status_code == 404


def test_demo_mode_blocks_feedback(
    settings: Settings, reviewed: int, sql: Sql, auth_headers: dict[str, str]
) -> None:
    finding_id = sql("SELECT id FROM findings LIMIT 1")[0].id
    demo = settings.model_copy(update={"demo_mode": True})
    with api(demo) as c:
        response = c.post(
            f"/api/v1/findings/{finding_id}/feedback", headers=auth_headers, json={"vote": 1}
        )
    assert response.status_code == 403
