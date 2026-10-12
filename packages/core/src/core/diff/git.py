"""Read revisions of a local git repository (no working-tree side effects).

Every call passes arguments as a list (never through a shell), and refuses
revision names that start with "-", which git would read as an option
(e.g. a branch named "--output=/etc/passwd").
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(Exception):
    pass


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            input=stdin,
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


def commit_subjects(repo: Path, base_sha: str, head_sha: str) -> list[str]:
    """Subjects of the commits in base..head, oldest first: the CLI's stand-in
    for a PR description."""
    out = _git(repo, "log", "--reverse", "--format=%s", f"{_rev(base_sha)}..{_rev(head_sha)}")
    return [line for line in out.decode("utf-8", errors="replace").splitlines() if line]


def show(repo: Path, rev: str, path: str) -> bytes:
    """A file's bytes at a revision."""
    return _git(repo, "show", f"{_rev(rev)}:{path}")


def list_files(repo: Path, rev: str) -> list[str]:
    """All tracked file paths at a revision."""
    out = _git(repo, "-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", _rev(rev))
    return out.decode("utf-8", errors="replace").splitlines()


@dataclass(frozen=True, slots=True)
class Blob:
    path: str
    oid: str  # the blob's object id
    size: int


def list_blobs(repo: Path, rev: str) -> list[Blob]:
    """Regular files at a revision, with their sizes, without reading them.

    Symlinks (mode 120000) and submodules (type commit) are left out: a
    symlink's "content" is its target path, not code.
    """
    # -z: NUL-separated records, so no path quoting or escaping to undo.
    out = _git(repo, "ls-tree", "-r", "-l", "-z", _rev(rev))
    blobs = []
    for record in out.split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        mode, kind, oid, size = meta.split()
        if kind == b"blob" and mode != b"120000":
            blobs.append(Blob(path.decode("utf-8", errors="replace"), oid.decode(), int(size)))
    return blobs


def read_blobs(repo: Path, oids: list[str]) -> dict[str, bytes]:
    """Many blobs' contents through one `git cat-file --batch` process, instead
    of one `git show` per file (a process spawn each: seconds on a big repo)."""
    if not oids:
        return {}
    out = _git(repo, "cat-file", "--batch", stdin=("\n".join(oids) + "\n").encode())
    # Each answer: "<oid> blob <size>\n" + exactly <size> bytes + "\n".
    contents: dict[str, bytes] = {}
    pos = 0
    for oid in oids:
        header_end = out.index(b"\n", pos)
        header = out[pos:header_end].split()
        if len(header) != 3 or header[1] != b"blob":  # e.g. "<oid> missing"
            raise GitError(f"git cat-file: {b' '.join(header).decode()}")
        size = header[2]
        start = header_end + 1
        contents[oid] = out[start : start + int(size)]
        pos = start + int(size) + 1
    return contents
