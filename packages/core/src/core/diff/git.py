"""Read revisions of a local git repository (no working-tree side effects).

Every call passes arguments as a list (never through a shell), and refuses
revision names that start with "-", which git would read as an option
(e.g. a branch named "--output=/etc/passwd").
"""

import subprocess
from pathlib import Path


class GitError(Exception):
    pass


def _git(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            check=True,
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git {' '.join(args[:2])}: {message}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args[:2])} timed out") from exc
    return result.stdout


def _rev(rev: str) -> str:
    if not rev or rev.startswith("-"):
        raise GitError(f"invalid revision: {rev!r}")
    return rev


def resolve(repo: Path, rev: str) -> str:
    """Full commit SHA for a branch, tag or SHA."""
    return _git(repo, "rev-parse", "--verify", f"{_rev(rev)}^{{commit}}").decode().strip()


def merge_base(repo: Path, base: str, head: str) -> str:
    """The commit a PR's diff is computed against (what GitHub shows)."""
    return _git(repo, "merge-base", _rev(base), _rev(head)).decode().strip()


def diff(repo: Path, base_sha: str, head_sha: str) -> str:
    """Unified diff between two commits, with rename detection.

    --no-ext-diff / --no-textconv: ignore any diff drivers configured in the
    repo, which could run arbitrary commands. Paths are not quoted for
    non-ASCII (core.quotePath=false), which keeps them readable; the parser
    still handles quoted paths.
    """
    out = _git(
        repo,
        "-c",
        "core.quotePath=false",
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--find-renames",
        "--unified=3",
        _rev(base_sha),
        _rev(head_sha),
        "--",
    )
    return out.decode("utf-8", errors="replace")


def show(repo: Path, rev: str, path: str) -> bytes:
    """A file's bytes at a revision."""
    return _git(repo, "show", f"{_rev(rev)}:{path}")


def list_files(repo: Path, rev: str) -> list[str]:
    """All tracked file paths at a revision."""
    out = _git(repo, "-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", _rev(rev))
    return out.decode("utf-8", errors="replace").splitlines()
