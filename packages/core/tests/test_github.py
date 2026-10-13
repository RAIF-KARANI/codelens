"""The GitHub App plumbing, without GitHub: App JWTs checked against a
throwaway RSA key, the REST client against httpx.MockTransport, and the
registration server over real local HTTP."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from core.github import GitHubClient, GitHubError, InstallationTokens, app_jwt
from core.github.auth import APP_JWT_LIFETIME, CLOCK_SKEW
from core.github.manifest import (
    DASHBOARD_CALLBACK_URL,
    EVENTS,
    PERMISSIONS,
    AppCredentials,
    build_manifest,
    save_credentials,
)
from core.github.register import Registration
from core.github.webhooks import sign, verify_signature

APP_ID = 4242
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def keypair() -> tuple[str, Any]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return pem, key.public_key()


def test_app_jwt_is_rs256_backdated_and_short_lived(keypair: tuple[str, Any]) -> None:
    pem, public = keypair
    token = app_jwt(APP_ID, pem, now=NOW)

    assert jwt.get_unverified_header(token)["alg"] == "RS256"
    claims = jwt.decode(token, public, algorithms=["RS256"], options={"verify_exp": False})
    assert claims["iss"] == str(APP_ID)
    assert claims["iat"] == int((NOW - CLOCK_SKEW).timestamp())
    assert claims["exp"] == int((NOW + APP_JWT_LIFETIME).timestamp())
    assert claims["exp"] - int(NOW.timestamp()) <= 600  # GitHub's 10-minute ceiling


def token_server(keypair: tuple[str, Any], minted: list[str]) -> httpx.MockTransport:
    """Fake GitHub: mints installation tokens valid for an hour from NOW."""
    _, public = keypair

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/app/installations/7/access_tokens"
        # Only a valid App JWT may mint tokens.
        bearer = request.headers["Authorization"].removeprefix("Bearer ")
        jwt.decode(bearer, public, algorithms=["RS256"], options={"verify_exp": False})
        minted.append(f"ghs_{len(minted)}")
        expires = (NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        return httpx.Response(201, json={"token": minted[-1], "expires_at": expires})

    return httpx.MockTransport(handle)


def test_installation_tokens_are_cached_until_near_expiry(keypair: tuple[str, Any]) -> None:
    pem, _ = keypair
    minted: list[str] = []
    clock = [NOW]

    async def go() -> list[str]:
        async with GitHubClient(transport=token_server(keypair, minted)) as gh:
            tokens = InstallationTokens(gh, APP_ID, pem, clock=lambda: clock[0])
            got = [await tokens.get(7), await tokens.get(7)]  # second: from the cache
            clock[0] = NOW + timedelta(minutes=54)  # 6 min left: still fine
            got.append(await tokens.get(7))
            clock[0] = NOW + timedelta(minutes=56)  # 4 min left: refresh
            got.append(await tokens.get(7))
            tokens.forget(7)
            got.append(await tokens.get(7))
            return got

    assert asyncio.run(go()) == ["ghs_0", "ghs_0", "ghs_0", "ghs_1", "ghs_2"]
    assert len(minted) == 3


def test_github_errors_carry_status_and_message() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(401, json={"message": "Bad credentials"})
    )

    async def go() -> None:
        async with GitHubClient(transport=transport) as gh:
            await gh.get_app("not-a-jwt")

    with pytest.raises(GitHubError, match="GET /app: 401 Bad credentials") as caught:
        asyncio.run(go())
    assert caught.value.status == 401


def test_requests_pin_the_api_version() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    async def go() -> None:
        async with GitHubClient(transport=httpx.MockTransport(handle)) as gh:
            await gh.list_installations("jwt")

    asyncio.run(go())
    [request] = seen
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert request.headers["User-Agent"] == "codelens"
    assert request.url.params["per_page"] == "100"


@pytest.mark.parametrize(
    ("secret", "body", "header", "ok"),
    [
        ("s3cret", b'{"a":1}', sign("s3cret", b'{"a":1}'), True),
        ("s3cret", b'{"a":2}', sign("s3cret", b'{"a":1}'), False),  # body tampered
        ("s3cret", b'{"a":1}', sign("other", b'{"a":1}'), False),  # wrong secret
        ("s3cret", b'{"a":1}', sign("s3cret", b'{"a":1}').removeprefix("sha256="), False),
        ("s3cret", b'{"a":1}', None, False),  # unsigned
        ("", b'{"a":1}', sign("", b'{"a":1}'), False),  # no secret configured: never valid
    ],
    ids=["valid", "tampered", "wrong-secret", "no-prefix", "missing", "empty-secret"],
)
def test_verify_signature(secret: str, body: bytes, header: str | None, ok: bool) -> None:
    assert verify_signature(secret, body, header) is ok


CONVERSION = {
    "id": APP_ID,
    "slug": "codelens-test",
    "html_url": "https://github.com/apps/codelens-test",
    "pem": "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n",
    "webhook_secret": "whsec",
    "client_id": "Iv1.abc",
    "client_secret": "cs",
}


def test_manifest_asks_for_least_privilege() -> None:
    manifest = build_manifest(
        name="CodeLens (t)", webhook_url="https://smee.io/x", redirect_url="http://127.0.0.1:1/cb"
    )
    assert manifest["default_permissions"] == PERMISSIONS
    assert PERMISSIONS == {"contents": "read", "metadata": "read", "pull_requests": "write"}
    assert manifest["default_events"] == EVENTS
    assert manifest["public"] is False
    assert manifest["hook_attributes"] == {"url": "https://smee.io/x", "active": True}
    assert manifest["callback_urls"] == [DASHBOARD_CALLBACK_URL]  # the dashboard's login


def test_credentials_are_saved_for_env(tmp_path: Path) -> None:
    creds = AppCredentials.from_conversion(CONVERSION)
    key_path, env_path = save_credentials(creds, tmp_path / "secrets")

    assert key_path.read_text() == CONVERSION["pem"]
    env = dict(line.split("=", 1) for line in env_path.read_text().splitlines() if "=" in line)
    assert env["GITHUB_APP_ID"] == str(APP_ID)
    assert env["GITHUB_WEBHOOK_SECRET"] == "whsec"
    assert env["GITHUB_APP_PRIVATE_KEY_PATH"] == key_path.as_posix()


def test_registration_flow(tmp_path: Path) -> None:
    codes: list[str] = []

    def convert(code: str) -> AppCredentials:
        codes.append(code)
        return AppCredentials.from_conversion(CONVERSION)

    registration = Registration(
        name="CodeLens (t)",
        webhook_url="https://smee.io/x",
        convert=convert,
        secrets_dir=tmp_path,
    )
    registration.start()
    try:
        page = httpx.get(f"{registration.url}/").text
        forged = httpx.get(f"{registration.url}/callback?code=evil&state=wrong")
        assert not registration.done.is_set()
        ok = httpx.get(f"{registration.url}/callback?code=abc&state={registration.state}")
    finally:
        registration.stop()

    # The page posts the manifest to GitHub with our state, and the manifest
    # sends GitHub back to our callback.
    assert f"settings/apps/new?state={registration.state}" in page
    assert registration.manifest["redirect_url"] == f"{registration.url}/callback"
    assert json.dumps(PERMISSIONS).replace('"', "&quot;") in page
    assert forged.status_code == 400
    assert ok.status_code == 200
    assert codes == ["abc"]  # the forged callback never reached GitHub
    assert registration.saved is not None
    assert (tmp_path / "github-app.pem").exists()
    assert "codelens-test/installations/new" in ok.text


def test_mirror_sync_keeps_the_token_off_argv_and_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from core.github import mirror

    work = tmp_path / "work"
    work.mkdir()
    (work / "a.py").write_text("x = 1\n")
    run = ["git", "-C", str(work), "-c", "user.name=T", "-c", "user.email=t@example.com"]
    subprocess.run([*run, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*run, "add", "-A"], check=True)
    subprocess.run([*run, "commit", "-q", "-m", "one"], check=True)

    calls: list[tuple[list[str], dict[str, str]]] = []
    real_run = subprocess.run

    def spy(args: list[str], **kwargs: Any) -> Any:
        calls.append((args, kwargs.get("env") or {}))
        return real_run(args, **kwargs)

    monkeypatch.setattr(mirror.subprocess, "run", spy)
    path = mirror.mirror_path(tmp_path / "data", 3)
    mirror.sync_mirror(path, work.as_uri(), "ghs_SECRET")
    mirror.sync_mirror(path, work.as_uri(), "ghs_SECRET")  # second run: fetch only

    assert [args[1] if args[1] != "-C" else args[3] for args, _ in calls] == [
        "init",
        "fetch",
        "fetch",
    ]
    for args, env in calls:
        assert not any("ghs_SECRET" in a for a in args)
        assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert calls[1][1]["GIT_CONFIG_VALUE_1"].startswith("Authorization: Basic ")
    assert "ghs_SECRET" not in (path / "config").read_text()
    assert (
        subprocess.run(
            ["git", "-C", str(path), "rev-parse", "refs/heads/main"], capture_output=True, text=True
        ).stdout.strip()
        == subprocess.run(
            ["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
    )
