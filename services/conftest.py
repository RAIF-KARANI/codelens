"""Fixtures shared by the API and worker tests: a small git repo indexed into
the test database, settings pointing at it, and the worker's tasks wired to
fake models (no broker, no GPU)."""

import asyncio
import re
import shutil
import subprocess
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select, text

from api.auth import issue_token
from core.config import Settings, get_settings
from core.db import create_engine
from core.db.models import Repo
from core.embedding import embed_repository
from core.indexing import index_repository
from core.providers import Message, Providers
from core.providers.fake import FakeEmbedder, FakeLLM

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "packages" / "core" / "tests" / "fixtures" / "repos" / "py_shop"
REPO_NAME = "local/shop"
BRANCH = "reject-bad-discounts"
JWT_SECRET = "test-secret-test-secret-test-secre"  # >= 32 bytes, as Settings requires


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        text=True,
    )  # fmt: skip
    return out.stdout.strip()


@dataclass(frozen=True)
class Shop:
    path: Path
    repo_id: int
    base_sha: str
    head_sha: str
    branch: str = BRANCH

    def git(self, *args: str) -> str:
        return git(self.path, *args)


@pytest.fixture
def settings(migrated_database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=migrated_database_url,
        redis_url=get_settings().redis_url,
        llm_provider="fake",
        embed_provider="fake",
        api_jwt_secret=JWT_SECRET,
        pipeline_config=ROOT / "configs" / "pipeline" / "default.yaml",
    )


@pytest.fixture
def sql(settings: Settings) -> Callable[..., list[Any]]:
    """Run one SQL statement on the test database, committed; returns its rows."""

    def run(statement: str, **params: Any) -> list[Any]:
        async def go() -> list[Any]:
            engine = create_engine(settings.database_url, pooled=False)
            try:
                async with engine.begin() as conn:
                    result = await conn.execute(text(statement), params)
                    return list(result) if result.returns_rows else []
            finally:
                await engine.dispose()

        return asyncio.run(go())

    return run


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(JWT_SECRET, '1', 'tester')}"}


@pytest.fixture
def shop(tmp_path: Path, settings: Settings) -> Shop:
    """py_shop as a git repo, indexed and embedded on main, with a branch that
    makes apply_discount return None."""
    repo = Path(shutil.copytree(FIXTURE, tmp_path / "shop"))
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")

    async def index_and_embed() -> int:
        engine = create_engine(settings.database_url, pooled=False)
        try:
            await index_repository(engine, repo, name=REPO_NAME)
            await embed_repository(
                engine, FakeEmbedder(settings.embed_dim), REPO_NAME, max_tokens=1024
            )
            async with engine.connect() as conn:
                return await conn.scalar(select(Repo.id).where(Repo.full_name == REPO_NAME))
        finally:
            await engine.dispose()

    repo_id = asyncio.run(index_and_embed())

    git(repo, "checkout", "-q", "-b", BRANCH)
    pricing = repo / "shop" / "pricing.py"
    pricing.write_text(
        pricing.read_text().replace(
            '    """Return amount after a percentage discount."""\n',
            '    """Return amount after a percentage discount."""\n'
            "    if percent > 100:\n"
            "        return None\n",
        ),
        newline="\n",
    )
    git(repo, "commit", "-q", "-am", "Reject discounts above 100%")
    git(repo, "checkout", "-q", "main")
    return Shop(repo, repo_id, git(repo, "rev-parse", "main"), git(repo, "rev-parse", BRANCH))


def scripted(messages: Sequence[Message], schema: type[BaseModel]) -> dict[str, Any]:
    """One finding on the added lines (posted), one in a file the change
    doesn't touch (dropped by validation)."""
    shown = re.findall(r'<context id="(N\d+)"', messages[-1].content)
    good = {
        "file_path": "shop/pricing.py",
        "line_start": 8,
        "line_end": 9,
        "severity": "critical",
        "category": "api-contract",
        "title": "apply_discount can now return None",
        "explanation": "with_tax multiplies the result.",
        "evidence": shown[:1],
        "confidence": 0.9,
    }
    elsewhere = {**good, "file_path": "shop/orders.py", "title": "not in the diff"}
    return {"summary": "Adds a guard that returns None.", "findings": [good, elsewhere]}


@pytest.fixture
def worker_on_fakes(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> FakeLLM:
    """Point the worker's tasks at the test database and fake models."""
    import worker.tasks

    llm = FakeLLM(scripted)

    @asynccontextmanager
    async def fake_providers(s: Settings) -> AsyncIterator[Providers]:
        yield Providers(FakeEmbedder(s.embed_dim), llm)

    monkeypatch.setattr(worker.tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(worker.tasks, "open_providers", fake_providers)
    return llm
