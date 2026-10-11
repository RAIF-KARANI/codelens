"""Commits made from patches with git plumbing: no checkout, no working tree.

A throwaway index file holds the tree being built: `read-tree <parent>`, then
`git apply --cached` for each patch, then `write-tree` and `commit-tree`. With
a fixed author and clock, the same patches always make the same commit.
"""

import os
import subprocess
import tempfile
from pathlib import Path

_IDENTITY = {
    "GIT_AUTHOR_NAME": "codelens-eval",
    "GIT_AUTHOR_EMAIL": "eval@codelens.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "codelens-eval",
    "GIT_COMMITTER_EMAIL": "eval@codelens.invalid",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}


def commit_patch(repo: Path, parent: str, patch: str, message: str) -> str:
    """The sha of a new commit: `parent` plus `patch`."""
    with tempfile.TemporaryDirectory() as scratch:
        env = _env(scratch)
        _git(repo, env, "read-tree", parent)
        _git(repo, env, "apply", "--cached", "-", stdin=patch)
        tree = _git(repo, env, "write-tree")
        return _git(repo, env, "commit-tree", tree, "-p", parent, "-m", message)


def applies(repo: Path, parent: str, *patches: str) -> bool:
    """Do the patches apply cleanly, each on top of the ones before it?"""
    with tempfile.TemporaryDirectory() as scratch:
        env = _env(scratch)
        try:
            _git(repo, env, "read-tree", parent)
            for patch in patches:
                _git(repo, env, "apply", "--cached", "-", stdin=patch)
        except subprocess.CalledProcessError:
            return False
        return True


def _env(scratch: str) -> dict[str, str]:
    return {**os.environ, **_IDENTITY, "GIT_INDEX_FILE": str(Path(scratch) / "index")}


def _git(repo: Path, env: dict[str, str], *args: str, stdin: str | None = None) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        input=stdin.encode("utf-8") if stdin is not None else None,
        capture_output=True,
        check=True,
    )
    return done.stdout.decode().strip()
