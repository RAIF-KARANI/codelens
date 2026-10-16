"""What a branch changed: per-file diffs, parsed base/head versions, seeds.

Everything is read from git objects (no checkout, no working tree, no DB):
the base side is the merge-base, like a GitHub PR, and both sides of each
changed file are parsed in memory. That in-memory head parse is the "overlay"
(planning.md §6.6): the stored graph stays at the indexed revision while a
review sees the PR's actual code.
"""

from dataclasses import dataclass
from pathlib import Path

from core.diff import FileDiff, FileStatus, git, parse_unified_diff
from core.parser.builder import TooComplex
from core.parser.modules import python_package_dirs
from core.parser.repository import parse_source
from core.parser.types import ParsedFile, SkippedFile
from core.parser.walker import source_from_content
from core.review.seeds import Seed, find_seeds

# Files parsed per review. Each changed file is read and parsed twice (base and
# head), and nothing else bounds a PR's size, so the rest stay in the diff
# unparsed: a review reads its first chunks anyway (max_chunks, D131).
MAX_REVIEW_FILES = 300


@dataclass(frozen=True, slots=True)
class FileChange:
    diff: FileDiff
    head: ParsedFile | None  # None if deleted, or not parseable source
    base: ParsedFile | None  # None if added, or not parseable source
    seeds: tuple[Seed, ...]
    skipped: str | None = None  # why it wasn't parsed ("not_source", "minified", ...)


@dataclass(frozen=True, slots=True)
class ChangeSet:
    base_sha: str  # the merge-base
    head_sha: str
    files: tuple[FileChange, ...]
    diff: str = ""  # the raw unified diff, as git produced it (stored with the review)

    @property
    def seeds(self) -> list[Seed]:
        return [seed for change in self.files for seed in change.seeds]


def collect_changes(repo: Path, base: str, head: str) -> ChangeSet:
    head_sha = git.resolve(repo, head)
    base_sha = git.merge_base(repo, base, head_sha)
    raw = git.diff(repo, base_sha, head_sha)
    diffs = parse_unified_diff(raw)

    # Python module names depend on where __init__.py files are, at each side.
    head_packages = python_package_dirs(git.list_files(repo, head_sha))
    base_packages = python_package_dirs(git.list_files(repo, base_sha))

    files: list[FileChange] = []
    for index, diff in enumerate(diffs):
        if index >= MAX_REVIEW_FILES:
            files.append(FileChange(diff, None, None, (), "too_many_files"))
            continue
        head_file, base_file, skipped = None, None, None
        if diff.new_path and diff.status is not FileStatus.DELETED and not diff.binary:
            head_file, skipped = _parse_at(repo, head_sha, diff.new_path, head_packages)
        if diff.old_path and diff.status is not FileStatus.ADDED and not diff.binary:
            base_file, base_skipped = _parse_at(repo, base_sha, diff.old_path, base_packages)
            skipped = skipped or base_skipped
        if diff.binary:
            skipped = "binary"
        seeds = tuple(find_seeds(diff, head_file, base_file))
        files.append(FileChange(diff, head_file, base_file, seeds, skipped))
    return ChangeSet(base_sha, head_sha, tuple(files), raw)


def _parse_at(
    repo: Path, rev: str, path: str, package_dirs: frozenset[str]
) -> tuple[ParsedFile | None, str | None]:
    content = git.show(repo, rev, path)
    source = source_from_content(path, content)
    if source is None:
        return None, "not_source"
    if isinstance(source, SkippedFile):
        return None, source.reason
    try:
        return parse_source(source, package_dirs), None
    except TooComplex:
        # Deeply nested or huge definitions: skipped, as minified files are (D131).
        return None, "too_complex"
