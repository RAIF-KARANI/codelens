"""The OSS repos cases are built from (evals/sources.yaml), and their checkouts.

A checkout is a shallow fetch of exactly the pinned commit, with line endings
left alone (core.autocrlf=false): patches are byte-exact against the commit.
"""

import subprocess
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from core.enums import Language

DEFAULT_SOURCES = Path("evals/sources.yaml")
DEFAULT_CHECKOUTS = Path("data/evals/sources")  # data/ is gitignored


class Source(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    url: str
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: str
    language: Language
    include: list[str] = Field(min_length=1, description="path prefixes mutations may touch")


def load_sources(path: Path = DEFAULT_SOURCES) -> list[Source]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    sources = [Source.model_validate(s) for s in raw["sources"]]
    names = [s.name for s in sources]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate source names in {path}")
    return sources


def checkout(source: Source, root: Path = DEFAULT_CHECKOUTS) -> Path:
    """Make root/<name> a clean checkout of source.sha (fetching only if needed)."""
    dest = root / source.name
    if not (dest / ".git").is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        _git(dest, "init", "--quiet")
        _git(dest, "config", "core.autocrlf", "false")
    if _head(dest) != source.sha:
        _git(dest, "fetch", "--quiet", "--depth", "1", source.url, source.sha)
        _git(
            dest, "-c", "advice.detachedHead=false", "checkout", "--quiet", "--force", "FETCH_HEAD"
        )
    # Undo anything a previous run left behind: the working tree is what gets indexed.
    _git(dest, "reset", "--quiet", "--hard")
    _git(dest, "clean", "-ffdxq")
    if _head(dest) != source.sha:
        raise RuntimeError(f"{source.name}: checkout is not at {source.sha}")
    return dest


def _head(repo: Path) -> str | None:
    done = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() or None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
