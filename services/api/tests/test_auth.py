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


def test_a_pinned_admin_needs_its_user_id_not_only_its_login() -> None:
    # Phase 9 step 10 (D136): admin came from the token's login claim alone,
    # so whoever registered a configured admin's old login became admin.
    from fastapi.security import HTTPAuthorizationCredentials

    from api.auth import current_user

    settings = Settings(_env_file=None, api_jwt_secret=SECRET, api_admin_logins="boss:1001,old")

    def user(sub: str, login: str) -> object:
        token = issue_token(SECRET, sub, login)
        return current_user(
            settings, HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )

    assert user("1001", "boss").is_admin  # type: ignore[attr-defined]
    assert user("1001", "renamed-boss").is_admin is False  # type: ignore[attr-defined]
    assert user("999999", "BOSS").is_admin is False  # type: ignore[attr-defined]
    assert user("5", "old").is_admin  # type: ignore[attr-defined]  # unpinned: by login


def test_a_non_admin_is_noted_once_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    # D137 accepts per-installation visibility while the owner is the only
    # user; the log says so the first time anyone else signs in (D192).
    from fastapi.security import HTTPAuthorizationCredentials

    from api import auth

    settings = Settings(_env_file=None, api_jwt_secret=SECRET, api_admin_logins="boss:1001")

    def call(sub: str, login: str) -> None:
        token = issue_token(SECRET, sub, login, installations=[7])
        auth.current_user(
            settings, HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )

    auth._NOTED_USERS.discard("2002")
    with caplog.at_level("WARNING", logger="api.auth"):
        call("1001", "boss")  # an admin: nothing to say
        call("2002", "friend")
        call("2002", "friend")
    notes = [r.getMessage() for r in caplog.records if "D137" in r.getMessage()]
    assert notes == [
        "dashboard user friend (not in API_ADMIN_LOGINS) sees every repo of their"
        " installations [7], not only the repos they can open on GitHub (D137)"
    ]


def test_local_mode_makes_every_caller_the_owner() -> None:
    # CodeLens Desktop (D147): no sign-in, one owner; only its own dashboard
    # holds the secret, so a valid token is the owner's, whatever it says.
    from fastapi.security import HTTPAuthorizationCredentials

    from api.auth import current_user

    settings = Settings(_env_file=None, api_jwt_secret=SECRET, local_mode=True)
    token = issue_token(SECRET, "local-owner", "local")
    user = current_user(settings, HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    assert user.is_admin
    assert user.demo is False


def test_local_mode_still_needs_a_valid_token() -> None:
    with client(local_mode=True) as c:
        missing = c.get("/api/v1/repos")
        forged = c.get("/api/v1/repos", headers=bearer(issue_token(OTHER_SECRET, "1", "x")))
    assert missing.status_code == 401
    assert forged.status_code == 401


def test_demo_and_local_mode_cannot_both_be_on() -> None:
    with pytest.raises(ValueError, match="can't both be on"):
        Settings(_env_file=None, demo_mode=True, local_mode=True)
