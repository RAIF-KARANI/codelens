"""POST /webhooks/github: signature checks first (no database needed), then
delivery dedupe and installation mirroring against a real database."""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from api.main import create_app
from core.config import Settings
from core.github.webhooks import sign

URL = "/webhooks/github"
SECRET = "unit-webhook-secret"

Sql = Callable[..., list[Any]]


@contextmanager
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
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
def test_other_events_are_recorded_until_step_3(settings: Settings, sql: Sql) -> None:
    secret = settings.github_webhook_secret.get_secret_value()
    payload = {"action": "opened", "installation": {"id": 99}, "number": 1}
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
