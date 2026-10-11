import subprocess
from pathlib import Path

import pytest

from evals.edits import Edit, apply_edits, unified_patch

CODE = b"a = 1\nb = 2\nc = 3\n"


def test_replacement_reports_the_new_lines() -> None:
    new, lines = apply_edits(CODE, (Edit(6, 11, b"b = 20\nb2 = 0"),))

    assert new == b"a = 1\nb = 20\nb2 = 0\nc = 3\n"
    assert lines == (2, 3)


def test_deleting_whole_lines_reports_both_sides_of_the_gap() -> None:
    new, lines = apply_edits(CODE, (Edit(6, 12, b""),))

    assert new == b"a = 1\nc = 3\n"
    assert lines == (1, 2)


def test_deleting_inside_a_line_reports_that_line() -> None:
    new, lines = apply_edits(CODE, (Edit(6, 8, b""),))  # "b " -> "= 2"

    assert new == b"a = 1\n= 2\nc = 3\n"
    assert lines == (2, 2)


def test_secondary_edits_move_lines_but_are_not_reported() -> None:
    edits = (Edit(0, 0, b"import x\n", primary=False), Edit(12, 17, b"c = 30"))

    new, lines = apply_edits(CODE, edits)

    assert new == b"import x\na = 1\nb = 2\nc = 30\n"
    assert lines == (4, 4)


def test_overlapping_edits_are_rejected() -> None:
    with pytest.raises(ValueError, match="overlapping"):
        apply_edits(CODE, (Edit(0, 5, b"x"), Edit(4, 8, b"y")))


def test_patch_applies_with_git_even_without_a_final_newline(tmp_path: Path) -> None:
    old = b"x = 1\n\x0cy = 2"  # a form feed (not a line break for diff) and no final newline
    new, _ = apply_edits(old, (Edit(11, 12, b"3"),))

    patch = unified_patch("m.py", old, new)

    # One line changed (the form feed stayed part of it), on both sides marked
    # as lacking a final newline.
    assert patch.endswith(
        "-\x0cy = 2\n\\ No newline at end of file\n+\x0cy = 3\n\\ No newline at end of file\n"
    )
    (tmp_path / "m.py").write_bytes(old)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    # As in evals.sources.checkout: a global autocrlf=true (Windows) would write CRLF.
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=tmp_path, check=True)
    subprocess.run(["git", "apply", "-"], cwd=tmp_path, input=patch.encode(), check=True)
    assert (tmp_path / "m.py").read_bytes() == new
