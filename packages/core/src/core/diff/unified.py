"""Parse unified diffs (git's format, and plain `diff -u`) into per-file hunks.

    diff --git a/old.py b/new.py        <- file header (git)
    similarity index 90%                <- extended headers: rename / mode / binary
    rename from old.py
    rename to new.py
    --- a/old.py                        <- old path (/dev/null for an added file)
    +++ b/new.py                        <- new path (/dev/null for a deleted file)
    @@ -12,7 +12,8 @@ def checkout():    <- hunk: old start,count  new start,count
     context line
    -removed line
    +added line
    \\ No newline at end of file

Line numbers are 1-based. "New side" means the head version of the file.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
_OCTAL = re.compile(rb"\\([0-7]{3})")


class FileStatus(StrEnum):
    ADDED = "added"
    DELETED = "deleted"
    MODIFIED = "modified"
    RENAMED = "renamed"  # may also have content changes


@dataclass(frozen=True, slots=True)
class DiffLine:
    kind: Literal["context", "added", "removed"]
    text: str
    old_line: int | None  # None for added lines
    new_line: int | None  # None for removed lines


@dataclass(frozen=True, slots=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str  # text after the second @@ (often the enclosing function)
    lines: tuple[DiffLine, ...]


@dataclass(frozen=True, slots=True)
class FileDiff:
    old_path: str | None  # None when the file was added
    new_path: str | None  # None when the file was deleted
    status: FileStatus
    hunks: tuple[Hunk, ...]
    binary: bool = False
    similarity: int | None = None  # rename similarity %, when git reports it

    @property
    def path(self) -> str:
        """Head path, or the old path for a deleted file."""
        return self.new_path or self.old_path or ""

    def added_lines(self, *, ignore_blank: bool = False) -> frozenset[int]:
        """New-side line numbers that were added (or replaced).

        ignore_blank: skip whitespace-only lines, which change no behaviour
        (an extra blank line between methods shouldn't mark the class as changed).
        """
        return frozenset(
            line.new_line
            for hunk in self.hunks
            for line in hunk.lines
            if line.kind == "added"
            and line.new_line is not None
            and not (ignore_blank and not line.text.strip())
        )

    def removed_lines(self, *, ignore_blank: bool = False) -> frozenset[int]:
        """Old-side line numbers that were removed (or replaced)."""
        return frozenset(
            line.old_line
            for hunk in self.hunks
            for line in hunk.lines
            if line.kind == "removed"
            and line.old_line is not None
            and not (ignore_blank and not line.text.strip())
        )

    def deletion_borders(self) -> frozenset[int]:
        """New-side lines of the context lines just before and just after each
        pure deletion (a run of removed lines with no added line in it).

        A pure deletion has no new line of its own, so a comment about it has
        to sit next to it; GitHub accepts comments on any line of a hunk."""
        borders: set[int] = set()
        for hunk in self.hunks:
            lines = hunk.lines
            i = 0
            while i < len(lines):
                if lines[i].kind == "context":
                    i += 1
                    continue
                end = i  # [i, end) is a run of changed lines
                while end < len(lines) and lines[end].kind != "context":
                    end += 1
                if all(line.kind == "removed" for line in lines[i:end]):
                    for j in (i - 1, end):
                        if 0 <= j < len(lines) and lines[j].new_line is not None:
                            borders.add(lines[j].new_line)
                i = end
        return frozenset(borders)


def parse_unified_diff(text: str) -> list[FileDiff]:
    """Parse a (possibly multi-file) unified diff. Unknown lines are ignored,
    so a commit message or `git format-patch` preamble before the diff is fine."""
    lines = text.replace("\r\n", "\n").split("\n")
    files: list[FileDiff] = []
    builder: _FileBuilder | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            if builder:
                files.append(builder.build())
            builder = _FileBuilder()
            builder.header_paths(line[len("diff --git ") :])
        elif builder is None and line.startswith("--- "):
            builder = _FileBuilder()  # plain `diff -u` output: no git header
            continue  # reprocess this line inside the builder
        elif builder is None:
            pass  # preamble
        elif (
            line.startswith("--- ")
            and builder.hunks
            and i + 1 < len(lines)
            and lines[i + 1].startswith("+++ ")
        ):
            # Plain multi-file `diff -u`: no "diff --git" separator, so a
            # ---/+++ pair after hunks starts the next file.
            files.append(builder.build())
            builder = _FileBuilder()
            continue
        elif line.startswith("--- ") and not builder.hunks:
            builder.old_path = _diff_path(line[4:], "a/")
        elif line.startswith("+++ ") and not builder.hunks:
            builder.new_path = _diff_path(line[4:], "b/")
        elif match := _HUNK.match(line):
            hunk, i = _parse_hunk(match, lines, i + 1)
            builder.hunks.append(hunk)
            continue
        else:
            builder.extended_header(line)
        i += 1
    if builder:
        files.append(builder.build())
    return files


class _FileBuilder:
    def __init__(self) -> None:
        self.old_path: str | None = None  # "/dev/null" is kept until build()
        self.new_path: str | None = None
        self.header_old: str | None = None
        self.header_new: str | None = None
        self.added = False
        self.deleted = False
        self.renamed = False
        self.binary = False
        self.similarity: int | None = None
        self.hunks: list[Hunk] = []

    def header_paths(self, rest: str) -> None:
        """Fallback paths from `diff --git a/X b/Y`, used when the diff has no
        ---/+++ lines (pure renames, mode changes, binary files)."""
        if rest.startswith('"'):
            old, _, new = rest.partition('" ')
            self.header_old = _strip_prefix(_unquote(old + '"'), "a/")
            self.header_new = _strip_prefix(_unquote(new), "b/")
        elif " b/" in rest:
            old, new = rest.rsplit(" b/", 1)
            self.header_old = _strip_prefix(old, "a/")
            self.header_new = new

    def extended_header(self, line: str) -> None:
        if line.startswith("rename from "):
            self.renamed, self.old_path = True, _unquote(line[len("rename from ") :])
        elif line.startswith("rename to "):
            self.renamed, self.new_path = True, _unquote(line[len("rename to ") :])
        elif line.startswith("new file mode"):
            self.added = True
        elif line.startswith("deleted file mode"):
            self.deleted = True
        elif line.startswith("similarity index "):
            self.similarity = int(line[len("similarity index ") :].rstrip("%") or 0)
        elif line.startswith("Binary files ") or line == "GIT binary patch":
            self.binary = True

    def build(self) -> FileDiff:
        old = self.old_path if self.old_path is not None else self.header_old
        new = self.new_path if self.new_path is not None else self.header_new
        added = self.added or old == "/dev/null"
        deleted = self.deleted or new == "/dev/null"
        old = None if added else old
        new = None if deleted else new
        if added:
            status = FileStatus.ADDED
        elif deleted:
            status = FileStatus.DELETED
        elif self.renamed and old != new:
            status = FileStatus.RENAMED
        else:
            status = FileStatus.MODIFIED
        return FileDiff(old, new, status, tuple(self.hunks), self.binary, self.similarity)


def _parse_hunk(match: re.Match[str], lines: list[str], i: int) -> tuple[Hunk, int]:
    old_start, new_start = int(match[1]), int(match[3])
    # An omitted count means 1: "@@ -3 +3 @@".
    old_count = int(match[2]) if match[2] is not None else 1
    new_count = int(match[4]) if match[4] is not None else 1
    old_no, new_no = old_start, new_start
    old_left, new_left = old_count, new_count
    parsed: list[DiffLine] = []

    while (old_left > 0 or new_left > 0) and i < len(lines):
        line = lines[i]
        marker, body = (line[:1], line[1:]) if line else (" ", "")  # editors strip "' '" lines
        if marker == "\\":  # "\ No newline at end of file": not a line of the file
            i += 1
            continue
        if marker == "+":
            parsed.append(DiffLine("added", body, None, new_no))
            new_no, new_left = new_no + 1, new_left - 1
        elif marker == "-":
            parsed.append(DiffLine("removed", body, old_no, None))
            old_no, old_left = old_no + 1, old_left - 1
        elif marker == " ":
            parsed.append(DiffLine("context", body, old_no, new_no))
            old_no, new_no = old_no + 1, new_no + 1
            old_left, new_left = old_left - 1, new_left - 1
        else:
            break  # malformed: stop this hunk rather than eat the next file
        i += 1
    while i < len(lines) and lines[i].startswith("\\"):
        i += 1  # trailing "\ No newline at end of file"
    hunk = Hunk(old_start, old_count, new_start, new_count, match[5], tuple(parsed))
    return hunk, i


def _diff_path(raw: str, prefix: str) -> str:
    """Path from a ---/+++ line. Plain `diff -u` may append a tab and timestamp."""
    path = _unquote(raw.split("\t", 1)[0].rstrip())
    return path if path == "/dev/null" else _strip_prefix(path, prefix)


def _strip_prefix(path: str, prefix: str) -> str:
    return path[len(prefix) :] if path.startswith(prefix) else path


def _unquote(path: str) -> str:
    """Undo git's C-style quoting: "a/caf\\303\\251.py" -> a/café.py.

    git quotes paths containing special or non-ASCII characters, writing
    non-ASCII as octal escapes of the UTF-8 bytes.
    """
    if not (len(path) >= 2 and path.startswith('"') and path.endswith('"')):
        return path
    raw = path[1:-1].encode("utf-8")
    raw = _OCTAL.sub(lambda m: bytes([int(m[1], 8)]), raw)
    for escaped, plain in ((b'\\"', b'"'), (b"\\t", b"\t"), (b"\\n", b"\n")):
        raw = raw.replace(escaped, plain)
    return raw.replace(b"\\\\", b"\\").decode("utf-8", errors="replace")
