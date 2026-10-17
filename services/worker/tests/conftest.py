"""The worker against a fake GitHub: its REST API is an httpx.MockTransport,
and "github.com" is a directory of bare repos on disk (remotes/<owner>/<name>.git)."""

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
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

FIXTURE = (
    Path(__file__).parents[3] / "packages" / "core" / "tests" / "fixtures" / "repos" / "py_shop"
)
INSTALLATION = 555


def run_git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        text=True,
    )  # fmt: skip
    return out.stdout.strip()


@dataclass
class Remote:
    """A working copy whose pushes land in the "GitHub" repo octo/shop."""

    work: Path

    def git(self, *args: str) -> str:
        return run_git(self.work, *args)

    def commit(self, path: str, text: str, message: str) -> str:
        (self.work / path).write_text(text, newline="\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def push(self, refspec: str) -> None:
        self.git("push", "-q", "--force", "origin", refspec)


@pytest.fixture
def remote(tmp_path: Path) -> Remote:
    work = Path(shutil.copytree(FIXTURE, tmp_path / "work"))
    run_git(work, "init", "-q", "-b", "main")
    run_git(work, "config", "core.autocrlf", "false")
    run_git(work, "add", "-A")
    run_git(work, "commit", "-q", "-m", "base")
    bare = tmp_path / "remotes" / "octo" / "shop.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    run_git(work, "remote", "add", "origin", str(bare))
    return Remote(work)


@dataclass
class FakeGitHub:
    minted: list[str] = field(default_factory=list)  # installation tokens handed out
    reviews: list[dict[str, Any]] = field(default_factory=list)  # posted PR reviews
    refuse_inline: int = 0  # answer 422 to this many reviews that have comments
    down: bool = False  # answer 502 to every review posted
    # What GitHub calls each repo id now; a rename changes it (D135).
    names: dict[int, str] = field(default_factory=lambda: {42: "octo/shop"})

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/app/installations/{INSTALLATION}/access_tokens":
            self.minted.append(f"ghs_{len(self.minted)}")
            expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
            return httpx.Response(201, json={"token": self.minted[-1], "expires_at": expires})
        assert request.headers["Authorization"] == f"Bearer {self.minted[-1]}"
        if path.startswith("/repositories/") and int(path.split("/")[2]) in self.names:
            github_id = int(path.split("/")[2])
            repo = {"id": github_id, "full_name": self.names[github_id], "default_branch": "main"}
            return httpx.Response(200, json=repo)
        if path.startswith("/repos/") and "/pulls/" in path and path.endswith("/reviews"):
            if self.down:
                return httpx.Response(502, json={"message": "Bad Gateway"})
            review = json.loads(request.content)
            if self.refuse_inline and review.get("comments"):
                self.refuse_inline -= 1
                return httpx.Response(422, json={"message": "Line could not be resolved"})
            parts = path.split("/")  # /repos/{owner}/{name}/pulls/{n}/reviews
            self.reviews.append(
                {"repo": f"{parts[2]}/{parts[3]}", "number": int(parts[5]), **review}
            )
            return httpx.Response(200, json={"id": 9000 + len(self.reviews)})
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def github(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch, worker_on_fakes: Any
) -> FakeGitHub:
    """The worker's tasks pointed at fake GitHub (and, via worker_on_fakes, at
    the test database and fake models)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "app.pem"
    pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    fake = FakeGitHub()
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
        lambda s: GitHubClient(s.github_api_url, transport=httpx.MockTransport(fake.handle)),
    )
    monkeypatch.setattr(worker.tasks, "_TOKEN_CACHE", {})
    return fake


@pytest.fixture
def attached_repo(sql: Callable[..., list[Any]]) -> int:
    """octo/shop as a GitHub repo of an installation (not indexed yet)."""
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
