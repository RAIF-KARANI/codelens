"""File discovery: which files in a repo get parsed, and why the rest don't."""

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pathspec

from core.enums import Language
from core.parser.languages import detect
from core.parser.types import SkippedFile

# Never descend into these, whatever .gitignore says: vendored dependencies,
# virtualenvs, build output and caches are not "the team's code" and can be huge.
ALWAYS_SKIP_DIRS = frozenset(
    {
        ".git", ".hg", ".svn",
        "node_modules", "bower_components", "vendor", "third_party",
        ".venv", "venv", "env", "site-packages", "__pycache__",
        ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        "dist", "build", "out", ".next", ".nuxt", ".svelte-kit",
        "coverage", "htmlcov", ".turbo", ".cache",
    }
)  # fmt: skip

MAX_FILE_BYTES = 512 * 1024
# Minified/bundled files have very long lines; they are generated, not authored.
_MINIFIED_AVG_LINE_LENGTH = 300
_BINARY_SNIFF_BYTES = 8192

_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__)/"  # tests/, test/, __tests__/
    r"|(^|/)test_[^/]*\.py$|_test\.py$"  # test_x.py, x_test.py
    r"|\.(test|spec)\.[cm]?[jt]sx?$"  # x.test.ts, x.spec.js
)


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: str  # POSIX, relative to the repo root
    language: Language
    grammar: str
    content: bytes
    is_test: bool


def is_test_path(path: str) -> bool:
    return _TEST_PATH.search(path) is not None


def discover_sources(root: Path) -> tuple[list[SourceFile], list[SkippedFile]]:
    """Walk `root`, returning parseable files and the skipped ones with reasons.

    Output is sorted by path so parsing (and therefore node ordinals and edge
    order) is deterministic across machines and runs.
    """
    ignore = _load_gitignore(root)
    sources: list[SourceFile] = []
    skipped: list[SkippedFile] = []

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        # Prune in place so os.walk never descends into skipped directories.
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in ALWAYS_SKIP_DIRS
            and not d.endswith(".egg-info")
            and not ignore.match_file(_join(rel_dir, d) + "/")
        )
        for filename in sorted(filenames):
            rel_path = _join(rel_dir, filename)
            detected = detect(rel_path)
            if detected is None or ignore.match_file(rel_path):
                continue  # not source code, or ignored: not worth reporting
            try:
                reason = _skip_reason(root / rel_path, rel_path)
                content = b"" if reason else (root / rel_path).read_bytes()
            except OSError:
                # Permissions, files deleted mid-walk, Windows paths over 260
                # chars (pnpm's node_modules)... one bad file must not abort the walk.
                reason = "unreadable"
            if reason is not None:
                skipped.append(SkippedFile(rel_path, reason))
                continue
            reason = _content_skip_reason(rel_path, content)
            if reason is not None:
                skipped.append(SkippedFile(rel_path, reason))
                continue
            language, grammar = detected
            sources.append(SourceFile(rel_path, language, grammar, content, is_test_path(rel_path)))

    sources.sort(key=lambda s: s.path)
    skipped.sort(key=lambda s: s.path)
    return sources, skipped


def _join(rel_dir: str, name: str) -> str:
    return f"{rel_dir}/{name}" if rel_dir else name


def _load_gitignore(root: Path) -> pathspec.PathSpec:
    # Only the root .gitignore; nested .gitignore files are not applied.
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return pathspec.GitIgnoreSpec.from_lines([])
    lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
    return pathspec.GitIgnoreSpec.from_lines(lines)


def source_from_content(path: str, content: bytes) -> SourceFile | SkippedFile | None:
    """Apply the walker's rules to a file that didn't come from a disk walk,
    e.g. a file read with `git show <rev>:<path>` when reviewing a diff.

    Returns None when the path isn't source code at all (README.md, a.yaml).
    """
    detected = detect(path)
    if detected is None:
        return None
    directories = PurePosixPath(path).parts[:-1]
    if any(d in ALWAYS_SKIP_DIRS or d.endswith(".egg-info") for d in directories):
        return SkippedFile(path, "vendored_or_generated")
    reason = _name_skip_reason(path)
    if reason is None and len(content) > MAX_FILE_BYTES:
        reason = "too_large"
    reason = reason or _content_skip_reason(path, content)
    if reason is not None:
        return SkippedFile(path, reason)
    language, grammar = detected
    return SourceFile(path, language, grammar, content, is_test_path(path))


def _skip_reason(abs_path: Path, rel_path: str) -> str | None:
    # A symlink in an untrusted repo could point anywhere on the worker's disk
    # (e.g. ~/.ssh), so we never follow them.
    if abs_path.is_symlink():
        return "symlink"
    reason = _name_skip_reason(rel_path)
    if reason is None and abs_path.stat().st_size > MAX_FILE_BYTES:
        reason = "too_large"
    return reason


def _name_skip_reason(rel_path: str) -> str | None:
    if rel_path.endswith(".d.ts"):
        return "type_declarations"  # types only; no runtime code to review
    if ".min." in PurePosixPath(rel_path).name:
        return "minified"
    return None


def _content_skip_reason(rel_path: str, content: bytes) -> str | None:
    if b"\x00" in content[:_BINARY_SNIFF_BYTES]:
        return "binary"
    lines = content.count(b"\n") + 1
    if len(content) > 2000 and len(content) / lines > _MINIFIED_AVG_LINE_LENGTH:
        return "minified"
    return None
