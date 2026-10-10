"""Seed mapping: changed lines -> the innermost code nodes they touch."""

import difflib
from textwrap import dedent

from core.diff import FileDiff, parse_unified_diff
from core.parser.modules import python_package_dirs
from core.parser.repository import parse_source
from core.parser.types import ParsedFile
from core.parser.walker import SourceFile, source_from_content
from core.review import ChangeKind, Seed, find_seeds


def parse(path: str, code: str) -> ParsedFile:
    source = source_from_content(path, dedent(code).lstrip("\n").encode())
    assert isinstance(source, SourceFile)
    return parse_source(source, python_package_dirs([path]))


def make_diff(old_path: str | None, new_path: str | None, old: str, new: str) -> FileDiff:
    lines = difflib.unified_diff(
        dedent(old).lstrip("\n").splitlines(keepends=True) if old_path else [],
        dedent(new).lstrip("\n").splitlines(keepends=True) if new_path else [],
        fromfile=f"a/{old_path}" if old_path else "/dev/null",
        tofile=f"b/{new_path}" if new_path else "/dev/null",
    )
    [diff] = parse_unified_diff("".join(lines))
    return diff


def seeds_for(old: str, new: str, *, old_path: str = "m.py", new_path: str = "m.py") -> list[Seed]:
    diff = make_diff(old_path, new_path, old, new)
    head = parse(new_path, new) if new_path else None
    base = parse(old_path, old) if old_path else None
    return find_seeds(diff, head, base)


def summary(seeds: list[Seed]) -> list[tuple[str, str]]:
    return [(s.change.value, str(s.node.ref)) for s in seeds]


BASE = """
import os


class Order:
    currency = "EUR"

    def total(self, items):
        return sum(items)

    def empty(self):
        return False


def helper(x):
    return x * 2
"""


def test_a_body_edit_seeds_only_the_innermost_method() -> None:
    head = BASE.replace("return sum(items)", "return sum(i for i in items)")

    seeds = seeds_for(BASE, head)

    assert summary(seeds) == [("modified", "m.Order.total")]
    [seed] = seeds
    assert seed.added_lines == {8}
    assert seed.removed_count == 1
    assert not seed.signature_changed


def test_a_signature_edit_is_flagged() -> None:
    head = BASE.replace("def helper(x):", "def helper(x, factor=2):")

    [seed] = seeds_for(BASE, head)

    assert seed.node.qualified_name == "m.helper"
    assert seed.signature_changed
    assert (seed.base.signature, seed.head.signature) == (
        "def helper(x)",
        "def helper(x, factor=2)",
    )


def test_class_body_and_module_level_edits_seed_the_class_and_module() -> None:
    head = BASE.replace('currency = "EUR"', 'currency = "USD"').replace(
        "import os", "import os, sys"
    )

    assert summary(seeds_for(BASE, head)) == [("modified", "m"), ("modified", "m.Order")]


def test_added_and_deleted_functions() -> None:
    head = BASE.replace(
        "def helper(x):\n    return x * 2\n", "def twice(x):\n    return x * 2\n"
    ).replace("    def empty(self):\n        return False\n", "")

    assert summary(seeds_for(BASE, head)) == [
        ("modified", "m"),  # lost a top-level function (helper)
        ("modified", "m.Order"),  # lost a method (empty)
        ("deleted", "m.Order.empty"),
        ("added", "m.twice"),  # same start line as helper; "added" sorts first
        ("deleted", "m.helper"),  # callers of deleted nodes are prime suspects
    ]


def test_pure_deletion_inside_a_function_seeds_that_function() -> None:
    base = "def f():\n    a = 1\n    b = 2\n    return a\n"
    head = "def f():\n    a = 1\n    return a\n"

    [seed] = seeds_for(base, head)

    assert (seed.change, seed.node.qualified_name) == (ChangeKind.MODIFIED, "m.f")
    assert seed.added_lines == frozenset() and seed.removed_count == 1


def test_blank_line_changes_create_no_seeds() -> None:
    head = BASE.replace("    def empty(self):", "\n    def empty(self):")

    assert seeds_for(BASE, head) == []


def test_new_file_seeds_every_definition_and_the_module() -> None:
    new = (
        "import json\n\n"
        "def load(p):\n    return json.load(open(p))\n\n"
        "class Cache:\n    def get(self):\n        pass\n"
    )

    seeds = seeds_for("", new, old_path=None, new_path="c.py")  # type: ignore[arg-type]

    assert summary(seeds) == [
        ("added", "c"),
        ("added", "c.load"),
        ("added", "c.Cache"),
        ("added", "c.Cache.get"),
    ]


def test_deleted_file_seeds_every_definition() -> None:
    seeds = seeds_for(BASE, "", old_path="m.py", new_path=None)  # type: ignore[arg-type]

    assert summary(seeds) == [
        ("deleted", "m.Order"),
        ("deleted", "m.Order.total"),
        ("deleted", "m.Order.empty"),
        ("deleted", "m.helper"),
    ]
    assert all(s.head is None and s.base is not None for s in seeds)


def test_renamed_file_with_an_edit_is_a_modification_not_add_plus_delete() -> None:
    head = BASE.replace("return x * 2", "return x + x")

    seeds = seeds_for(BASE, head, old_path="shop/old_name.py", new_path="shop/new_name.py")

    assert summary(seeds) == [("modified", "shop.new_name.helper")]
    assert seeds[0].base is not None and seeds[0].base.qualified_name == "shop.old_name.helper"


def test_decorator_edit_seeds_the_decorated_function() -> None:
    base = "@cache\ndef f():\n    return 1\n"
    head = "@cache(maxsize=10)\ndef f():\n    return 1\n"

    assert summary(seeds_for(base, head)) == [("modified", "m.f")]


def test_js_method_edit() -> None:
    base = "export class Cart {\n  total() {\n    return 1;\n  }\n}\n"
    head = "export class Cart {\n  total() {\n    return 2;\n  }\n}\n"

    seeds = seeds_for(base, head, old_path="src/cart.ts", new_path="src/cart.ts")

    assert summary(seeds) == [("modified", "src/cart:Cart.total")]
