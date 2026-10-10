from textwrap import dedent

from core.diff import FileStatus, parse_unified_diff


def parse(text: str):  # type: ignore[no-untyped-def]
    return parse_unified_diff(dedent(text).lstrip("\n"))


def test_modified_file_with_two_hunks() -> None:
    [diff] = parse(
        """
        diff --git a/app/models.py b/app/models.py
        index 1111111..2222222 100644
        --- a/app/models.py
        +++ b/app/models.py
        @@ -2,4 +2,5 @@ import os
         def a():
        -    return 1
        +    x = 1
        +    return x

         def b():
        @@ -20,3 +21,2 @@ class C:
             def m(self):
        -        pass
        -        pass
        +        return None
        """
    )

    assert (diff.old_path, diff.new_path, diff.status) == (
        "app/models.py",
        "app/models.py",
        FileStatus.MODIFIED,
    )
    assert [(h.old_start, h.old_count, h.new_start, h.new_count) for h in diff.hunks] == [
        (2, 4, 2, 5),
        (20, 3, 21, 2),
    ]
    assert diff.hunks[0].section == "import os"
    assert diff.added_lines() == {3, 4, 22}
    assert diff.removed_lines() == {3, 21, 22}
    assert diff.hunks[0].lines[0].kind == "context"
    assert (diff.hunks[0].lines[0].old_line, diff.hunks[0].lines[0].new_line) == (2, 2)


def test_added_and_deleted_files() -> None:
    added, deleted = parse(
        """
        diff --git a/new.py b/new.py
        new file mode 100644
        index 0000000..3333333
        --- /dev/null
        +++ b/new.py
        @@ -0,0 +1,2 @@
        +def f():
        +    pass
        diff --git a/old.py b/old.py
        deleted file mode 100644
        index 4444444..0000000
        --- a/old.py
        +++ /dev/null
        @@ -1,2 +0,0 @@
        -def g():
        -    pass
        """
    )

    assert (added.old_path, added.new_path, added.status) == (None, "new.py", FileStatus.ADDED)
    assert added.added_lines() == {1, 2}
    assert (deleted.old_path, deleted.new_path, deleted.status) == (
        "old.py",
        None,
        FileStatus.DELETED,
    )
    assert deleted.removed_lines() == {1, 2}
    assert deleted.path == "old.py"


def test_pure_rename_and_rename_with_edits() -> None:
    pure, edited = parse(
        """
        diff --git a/a.py b/b.py
        similarity index 100%
        rename from a.py
        rename to b.py
        diff --git a/pkg/x.py b/pkg/y.py
        similarity index 88%
        rename from pkg/x.py
        rename to pkg/y.py
        index 5555555..6666666 100644
        --- a/pkg/x.py
        +++ b/pkg/y.py
        @@ -1,2 +1,2 @@
         def h():
        -    return 1
        +    return 2
        """
    )

    assert (pure.old_path, pure.new_path, pure.status, pure.hunks) == (
        "a.py",
        "b.py",
        FileStatus.RENAMED,
        (),
    )
    assert pure.similarity == 100
    assert (edited.old_path, edited.new_path, edited.status) == (
        "pkg/x.py",
        "pkg/y.py",
        FileStatus.RENAMED,
    )
    assert edited.added_lines() == {2}


def test_binary_mode_change_and_no_newline_marker() -> None:
    binary, mode, no_newline = parse(
        """
        diff --git a/logo.png b/logo.png
        index 7777777..8888888 100644
        Binary files a/logo.png and b/logo.png differ
        diff --git a/run.sh b/run.sh
        old mode 100644
        new mode 100755
        diff --git a/c.py b/c.py
        index 9999999..aaaaaaa 100644
        --- a/c.py
        +++ b/c.py
        @@ -1 +1 @@
        -x = 1
        \\ No newline at end of file
        +x = 2
        \\ No newline at end of file
        """
    )

    assert binary.binary and binary.path == "logo.png" and binary.status is FileStatus.MODIFIED
    assert (mode.path, mode.status, mode.hunks) == ("run.sh", FileStatus.MODIFIED, ())
    # "@@ -1 +1 @@": omitted counts mean 1; the "\" lines are not file lines.
    assert [(h.old_count, h.new_count) for h in no_newline.hunks] == [(1, 1)]
    assert (no_newline.added_lines(), no_newline.removed_lines()) == ({1}, {1})


def test_quoted_unicode_paths_and_crlf() -> None:
    text = (
        'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\r\n'
        "index bbbbbbb..ccccccc 100644\r\n"
        '--- "a/caf\\303\\251.py"\r\n'
        '+++ "b/caf\\303\\251.py"\r\n'
        "@@ -1 +1,2 @@\r\n"
        " a = 1\r\n"
        "+b = 2\r\n"
    )

    [diff] = parse_unified_diff(text)

    assert diff.path == "café.py"
    assert diff.added_lines() == {2}
    assert diff.hunks[0].lines[1].text == "b = 2"  # no stray \r


def test_plain_multi_file_diff_without_git_headers() -> None:
    first, second = parse(
        """
        --- a/one.py\t2026-10-10 10:00:00
        +++ b/one.py\t2026-10-10 10:01:00
        @@ -1 +1 @@
        -a = 1
        +a = 2
        --- a/two.py
        +++ b/two.py
        @@ -1 +1 @@
        -b = 1
        +b = 2
        """
    )

    assert (first.path, second.path) == ("one.py", "two.py")  # timestamps stripped


def test_blank_lines_can_be_ignored() -> None:
    [diff] = parse(
        """
        --- a/m.py
        +++ b/m.py
        @@ -1,3 +1,5 @@
         class C:
        +
             def a(self): ...
        +    x = 1
        -
        """
    )

    assert diff.added_lines() == {2, 4}
    assert diff.added_lines(ignore_blank=True) == {4}
    assert diff.removed_lines(ignore_blank=True) == frozenset()


def test_preamble_is_ignored() -> None:
    [diff] = parse(
        """
        From 1234 Mon Sep 17 00:00:00 2001
        Subject: [PATCH] fix

        ---
         m.py | 2 +-
        diff --git a/m.py b/m.py
        --- a/m.py
        +++ b/m.py
        @@ -1 +1 @@
        -a
        +b
        """
    )

    assert diff.path == "m.py" and diff.added_lines() == {1}
