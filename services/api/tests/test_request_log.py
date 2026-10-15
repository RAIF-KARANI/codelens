"""The API's own access log and request ids (api/middleware.py). No database
needed: a request without a token is answered before one is used."""

import logging
import re

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from core.config import Settings

OFFLINE = Settings(
    _env_file=None,
    database_url="postgresql+asyncpg://user:pass@127.0.0.1:1/db",
    redis_url="redis://127.0.0.1:1/0",
    api_jwt_secret="x" * 32,
)


def access_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "api.access"]


def test_each_request_gets_an_id_and_one_line(caplog: pytest.LogCaptureFixture) -> None:
    with TestClient(create_app(OFFLINE)) as client, caplog.at_level(logging.INFO):
        response = client.get("/api/v1/repos")

    request_id = response.headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    [line] = access_lines(caplog)
    assert line.getMessage() == "GET /api/v1/repos 401"
    assert (line.method, line.path, line.status) == ("GET", "/api/v1/repos", 401)  # type: ignore[attr-defined]
    assert line.duration_ms >= 0  # type: ignore[attr-defined]
    assert line.request_id == request_id  # type: ignore[attr-defined]


def test_a_callers_id_is_kept_if_it_is_plain(caplog: pytest.LogCaptureFixture) -> None:
    with TestClient(create_app(OFFLINE)) as client, caplog.at_level(logging.INFO):
        kept = client.get("/api/v1/repos", headers={"X-Request-ID": "dash-42.a_b"})
        # Anything else could forge or break log lines: a fresh id instead.
        replaced = client.get("/api/v1/repos", headers={"X-Request-ID": "x\tfake=1" + "y" * 70})

    assert kept.headers["x-request-id"] == "dash-42.a_b"
    assert re.fullmatch(r"[0-9a-f]{32}", replaced.headers["x-request-id"])


def test_passing_health_probes_are_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    # Here /health fails (no database), so its line is kept: a failing probe
    # is worth seeing. A passing one every few seconds isn't.
    with TestClient(create_app(OFFLINE)) as client, caplog.at_level(logging.INFO):
        client.get("/health")
    [line] = access_lines(caplog)
    assert line.status == 503  # type: ignore[attr-defined]
