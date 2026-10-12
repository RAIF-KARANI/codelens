"""Auth, the demo-mode guard and the error shape. No database: auth runs as a
router dependency, before any route touches one."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from api.auth import issue_token
from api.main import create_app
from core.config import Settings

# At least 32 bytes: PyJWT warns about shorter HMAC keys, and Settings rejects them.
SECRET = "unit-secret-unit-secret-unit-secr"
OTHER_SECRET = "another-secret-another-secret-ano"


@contextmanager
def client(**overrides: object) -> Iterator[TestClient]:
    values: dict[str, object] = {
        # Never reached: every request here stops before the database.
        "database_url": "postgresql+asyncpg://user:pass@127.0.0.1:1/db",
        "redis_url": "redis://127.0.0.1:1/0",
        "api_jwt_secret": SECRET,
        **overrides,
    }
    # `with` runs the app's lifespan, which creates the (unconnected) clients.
    with TestClient(create_app(Settings(_env_file=None, **values))) as c:
        yield c


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def error(response: object) -> dict[str, object]:
    body = response.json()  # type: ignore[attr-defined]
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    return body["error"]


def test_missing_token_is_401_with_a_challenge() -> None:
    with client() as c:
        response = c.get("/api/v1/repos")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert error(response)["code"] == "unauthenticated"


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        (issue_token(OTHER_SECRET, "1", "x"), "InvalidSignatureError"),
        (issue_token(SECRET, "1", "x", ttl=timedelta(seconds=-1)), "ExpiredSignatureError"),
        # "alg: none" is the classic forgery: an unsigned token.
        (jwt.encode({"sub": "1", "exp": 4102444800}, None, algorithm="none"), "InvalidAlgorithm"),
        (jwt.encode({"sub": "1"}, SECRET, algorithm="HS256"), "MissingRequiredClaimError"),
        ("not-a-jwt", "DecodeError"),
    ],
    ids=lambda value: value if value[0].isupper() else "",
)
def test_bad_tokens_are_401(token: str, reason: str) -> None:
    with client() as c:
        response = c.get("/api/v1/repos", headers=bearer(token))
    assert response.status_code == 401
    body = error(response)
    assert body["code"] == "invalid_token"
    assert str(body["message"]).startswith(reason)


def test_unset_secret_fails_closed() -> None:
    with client(api_jwt_secret="") as c:
        response = c.get("/api/v1/repos", headers=bearer(issue_token(SECRET, "1", "x")))
    assert response.status_code == 503
    assert error(response)["code"] == "auth_not_configured"


def test_demo_mode_blocks_mutations() -> None:
    headers = bearer(issue_token(SECRET, "1", "x"))
    body = {"repo_id": 1, "base": "a", "head": "b"}
    with client(demo_mode=True) as demo:
        review = demo.post("/api/v1/reviews", headers=headers, json=body)
        index = demo.post("/api/v1/repos/1/index", headers=headers)
    for response in (review, index):
        assert response.status_code == 403
        assert error(response)["code"] == "demo_mode"


def test_framework_errors_use_the_same_shape() -> None:
    with client() as c:
        unknown = c.get("/api/v1/nope")
        invalid = c.get("/api/v1/repos/abc", headers=bearer(issue_token(SECRET, "1", "x")))
    assert unknown.status_code == 404
    assert error(unknown)["code"] == "not_found"

    assert invalid.status_code == 422
    body = error(invalid)
    assert body["code"] == "invalid_request"
    assert body["details"]["errors"][0]["loc"] == ["path", "repo_id"]  # type: ignore[index]
