"""Indexing a GitHub repo: installation token -> repo info -> bare mirror ->
index the default branch from git objects (fake GitHub: tests/conftest.py)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import worker.tasks
from worker.tasks import index_repo

pytestmark = pytest.mark.integration

Sql = Callable[..., list[Any]]


def test_a_github_repo_is_mirrored_and_indexed(
    remote: Any, github: Any, attached_repo: int, sql: Sql, tmp_path: Path
) -> None:
    first = index_repo.apply(args=(attached_repo,)).get()

    mirror = tmp_path / "data" / "repos" / f"{attached_repo}.git"
    assert (mirror / "HEAD").exists()
    assert not (mirror / "shop").exists()  # bare: nothing checked out
    assert first["commit_sha"] == remote.git("rev-parse", "main")
    assert first["wrote_graph"] is True
    [repo] = sql("SELECT default_branch, status FROM repos WHERE id = :id", id=attached_repo)
    assert (repo.default_branch, repo.status) == ("main", "ready")
    files = {r.path for r in sql("SELECT path FROM files WHERE repo_id = :id", id=attached_repo)}
    assert "shop/pricing.py" in files

    # A push to main: the next run fetches it and writes only what changed.
    head = remote.commit(
        "shop/notes.py", '"""Notes."""\n\n\ndef note():\n    return 1\n', "Add notes"
    )
    remote.push("main")

    second = index_repo.apply(args=(attached_repo,)).get()

    assert second["commit_sha"] == head
    [run] = sql("SELECT stats FROM index_runs WHERE id = :id", id=second["index_run_id"])
    assert run.stats["files"]["new"] == 1
    assert run.stats["files"]["changed"] == 0
    assert github.minted == ["ghs_0"]  # one token, reused from the cache by the second run


def test_a_missing_app_configuration_fails_clearly(
    remote: Any, github: Any, attached_repo: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = worker.tasks.get_settings().model_copy(update={"github_app_id": None})
    monkeypatch.setattr(worker.tasks, "get_settings", lambda: configured)

    result = index_repo.apply(args=(attached_repo,))

    assert "GITHUB_APP_ID is not set" in str(result.result)
    assert github.minted == []
