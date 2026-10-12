"""Indexing a GitHub repo: installation token -> repo info -> bare mirror ->
index the default branch from git objects. GitHub's API is an
httpx.MockTransport; "github.com" is a directory of bare repos on disk."""

import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import worker.tasks
from core.config import Settings
from core.github import GitHubClient
from worker.tasks import index_repo

pytestmark = pytest.mark.integration

Sql = Callable[..., list[Any]]
FIXTURE = (
    Path(__file__).parents[3] / "packages" / "core" / "tests" / "fixtures" / "repos" / "py_shop"
)
INSTALLATION = 555


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        text=True,
    )  # fmt: skip
    return out.stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """A working copy whose pushes land in remotes/octo/shop.git, the "GitHub" repo."""
    work = Path(shutil.copytree(FIXTURE, tmp_path / "work"))
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "core.autocrlf", "false")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "base")
    bare = tmp_path / "remotes" / "octo" / "shop.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    git(work, "remote", "add", "origin", str(bare))
    return work


@pytest.fixture
def github(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch, worker_on_fakes: Any
) -> list[str]:
    """The worker pointed at fake GitHub; returns the installation tokens it mints."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "app.pem"
    pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    minted: list[str] = []

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/app/installations/{INSTALLATION}/access_tokens":
            minted.append(f"ghs_{len(minted)}")
            expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
            return httpx.Response(201, json={"token": minted[-1], "expires_at": expires})
        if request.url.path == "/repos/octo/shop":
            assert request.headers["Authorization"] == f"Bearer {minted[-1]}"
            return httpx.Response(200, json={"full_name": "octo/shop", "default_branch": "main"})
        return httpx.Response(404, json={"message": "Not Found"})

    configured = settings.model_copy(
        update={
            "github_app_id": 1,
            "github_app_private_key_path": pem,
            "github_web_url": (tmp_path / "remotes").as_uri(),
            "data_dir": tmp_path / "data",
        }
    )
    monkeypatch.setattr(worker.tasks, "get_settings", lambda: configured)
    monkeypatch.setattr(
        worker.tasks,
        "github_client",
        lambda s: GitHubClient(s.github_api_url, transport=httpx.MockTransport(api)),
    )
    monkeypatch.setattr(worker.tasks, "_TOKEN_CACHE", {})
    return minted


def attached_repo(sql: Sql) -> int:
    [installation] = sql(
        "INSERT INTO installations (github_installation_id, account_id, account_login,"
        " account_type) VALUES (:id, 1, 'octo', 'User') RETURNING id",
        id=INSTALLATION,
    )
    [repo] = sql(
        "INSERT INTO repos (github_id, full_name, source, installation_id, default_branch)"
        " VALUES (42, 'octo/shop', 'github', :inst, 'unknown') RETURNING id",
        inst=installation.id,
    )
    return repo.id


def test_a_github_repo_is_mirrored_and_indexed(
    remote: Path, github: list[str], sql: Sql, tmp_path: Path
) -> None:
    repo_id = attached_repo(sql)

    first = index_repo.apply(args=(repo_id,)).get()

    mirror = tmp_path / "data" / "repos" / f"{repo_id}.git"
    assert (mirror / "HEAD").exists()
    assert not (mirror / "shop").exists()  # bare: nothing checked out
    assert first["commit_sha"] == git(remote, "rev-parse", "main")
    assert first["wrote_graph"] is True
    [repo] = sql("SELECT default_branch, status FROM repos WHERE id = :id", id=repo_id)
    assert (repo.default_branch, repo.status) == ("main", "ready")
    files = {r.path for r in sql("SELECT path FROM files WHERE repo_id = :id", id=repo_id)}
    assert "shop/pricing.py" in files

    # A push to main: the next run fetches it and writes only what changed.
    (remote / "shop" / "notes.py").write_text('"""Notes."""\n\n\ndef note():\n    return 1\n')
    git(remote, "add", "-A")
    git(remote, "commit", "-q", "-m", "Add notes")
    git(remote, "push", "-q", "origin", "main")

    second = index_repo.apply(args=(repo_id,)).get()

    assert second["commit_sha"] == git(remote, "rev-parse", "main")
    [run] = sql("SELECT stats FROM index_runs WHERE id = :id", id=second["index_run_id"])
    assert run.stats["files"]["new"] == 1
    assert run.stats["files"]["changed"] == 0
    assert github == ["ghs_0"]  # one token, reused from the cache by the second run


def test_a_missing_app_configuration_fails_clearly(
    remote: Path, github: list[str], sql: Sql, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = worker.tasks.get_settings().model_copy(update={"github_app_id": None})
    monkeypatch.setattr(worker.tasks, "get_settings", lambda: configured)

    result = index_repo.apply(args=(attached_repo(sql),))

    assert "GITHUB_APP_ID is not set" in str(result.result)
    assert github == []
