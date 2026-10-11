"""Byte-range edits to a file, and the unified diff they produce.

Operators describe a mutation as edits on the original bytes (tree-sitter
offsets). Applying them also tells us which lines of the *new* file carry the
bug: that is where a reviewer should comment.
"""

import difflib
import itertools
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Edit:
    start: int  # byte offsets into the original file
    end: int
    text: bytes
    primary: bool = True  # carries the bug; secondary edits keep the code consistent


def apply_edits(content: bytes, edits: tuple[Edit, ...]) -> tuple[bytes, tuple[int, int]]:
    """The new content and the (first, last) 1-based lines of the new file
    covering the primary edits. Deleting whole lines leaves no new line of its
    own, so it reports the lines on either side of the gap."""
    ordered = sorted(edits, key=lambda e: e.start)
    for before, after in itertools.pairwise(ordered):
        if before.end > after.start:
            raise ValueError("overlapping edits")
    out = bytearray()
    spans: list[tuple[int, int]] = []
    whole_lines = True  # every primary edit removed complete lines and added nothing
    pos = 0
    for edit in ordered:
        out += content[pos : edit.start]
        begin = len(out)
        out += edit.text
        if edit.primary:
            spans.append((begin, len(out)))
            whole_lines &= not edit.text and _at_line_start(content, edit.start)
            whole_lines &= _at_line_start(content, edit.end)
        pos = edit.end
    out += content[pos:]
    new = bytes(out)
    if not spans:
        raise ValueError("no primary edit")

    first = min(begin for begin, _ in spans)
    last = max(end for _, end in spans)
    first_line = new.count(b"\n", 0, first) + 1
    if last == first:
        if whole_lines:  # the gap sits between two lines
            return new, (max(1, first_line - 1), first_line)
        return new, (first_line, first_line)  # part of a line went: that line changed
    return new, (first_line, new.count(b"\n", 0, last - 1) + 1)


def _at_line_start(content: bytes, offset: int) -> bool:
    return offset == 0 or offset == len(content) or content[offset - 1 : offset] == b"\n"


def line_start(content: bytes, offset: int) -> int:
    return content.rfind(b"\n", 0, offset) + 1


def line_end(content: bytes, offset: int) -> int:
    """Offset just past the newline ending the line that holds `offset`."""
    newline = content.find(b"\n", offset)
    return len(content) if newline == -1 else newline + 1


def indent_at(content: bytes, offset: int) -> bytes:
    """The leading whitespace of the line holding `offset`."""
    start = line_start(content, offset)
    line = content[start : line_end(content, offset)]
    return line[: len(line) - len(line.lstrip(b" \t"))]


def on_own_lines(content: bytes, start: int, end: int) -> bool:
    """Nothing but whitespace shares a line with content[start:end]."""
    before = content[line_start(content, start) : start]
    after = content[end : line_end(content, end)]
    return not before.strip() and not after.strip()


def unified_patch(path: str, old: bytes, new: bytes) -> str:
    """A `git apply`-able diff of one file. Lines split on \\n only: Python
    source may contain form feeds, which str.splitlines() would split on."""
    lines = difflib.unified_diff(_lines(old), _lines(new), f"a/{path}", f"b/{path}", n=3)
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines
    )


def _lines(content: bytes) -> list[str]:
    parts = content.decode("utf-8").split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines
