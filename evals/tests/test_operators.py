"""Each operator on the toy repo: the exact edit, the lines a reviewer should
flag, and the oracle context. Python and TypeScript side by side."""

import pytest

from evals.edits import apply_edits
from evals.graph import CodeGraph
from evals.operators import (
    Mutation,
    Operator,
    drop_await,
    drop_param,
    exception_type,
    extract_return,
    raise_to_none,
    remove_guard,
    rename_local,
    reorder_params,
    sql_format,
)
from evals.tests.toy import get_target


def run(graph: CodeGraph, operator: Operator, name: str) -> tuple[Mutation, list[str], range]:
    """The mutation, the new file's lines, and the 1-based expected line range."""
    target = get_target(graph, name)
    mutation = operator(target)
    assert mutation is not None, f"{operator.__name__} found nothing in {name}"
    new, (first, last) = apply_edits(target.content, mutation.edits)
    return mutation, new.decode().split("\n"), range(first, last + 1)


def oracle(mutation: Mutation) -> list[tuple[str, str]]:
    return [(ref.qualified_name, role) for ref, role in mutation.oracle]


def flagged(lines: list[str], span: range) -> list[str]:
    return [lines[i - 1] for i in span]


# ---------------------------------------------------------------- seeded


@pytest.mark.parametrize(
    ("name", "line", "callers"),
    [
        ("pkg.store.find", "    return None", ["pkg.api.first_name", "pkg.api.show"]),
        ("src/store:find", "  return undefined;", ["src/api:firstName", "src/api:show"]),
    ],
)
def test_raise_to_none(toy_graph: CodeGraph, name: str, line: str, callers: list[str]) -> None:
    mutation, lines, span = run(toy_graph, raise_to_none, name)

    assert flagged(lines, span) == [line]
    assert oracle(mutation) == [(c, "caller") for c in callers]
    assert mutation.expected is not None and mutation.expected[0] == "bug"


def test_raise_to_none_needs_a_caller_that_uses_the_result(toy_graph: CodeGraph) -> None:
    # describe() calls check(...) as a statement: returning None would go unnoticed.
    assert raise_to_none(get_target(toy_graph, "pkg.store.check")) is None


@pytest.mark.parametrize(
    ("name", "signature", "body", "caller"),
    [
        (
            "pkg.store.find",
            "def find(items, key):",
            [
                '    """Return the item with this key."""',
                "    strict = True",
                "    for item in items:",
            ],
            "pkg.api.show",
        ),
        (
            "src/store:find",
            "export function find(items: Item[], key: string): Item {",
            ["  const strict = true;", "  for (const item of items) {"],
            "src/api:show",
        ),
    ],
)
def test_drop_param(
    toy_graph: CodeGraph, name: str, signature: str, body: list[str], caller: str
) -> None:
    mutation, lines, span = run(toy_graph, drop_param, name)

    assert flagged(lines, span) == [signature]
    # The body still works: the default became a local, after any docstring.
    assert lines[span.stop - 1 : span.stop - 1 + len(body)] == body
    # Only show() passes `strict` (positionally); first_name() relies on the default.
    assert oracle(mutation) == [(caller, "caller")]


def test_drop_param_on_its_own_line_expects_the_new_local(toy_graph: CodeGraph) -> None:
    mutation, lines, span = run(toy_graph, drop_param, "pkg.store.render")

    # `    escape=True,` went as a whole line, so the signature adds no line to
    # the diff; the expectation runs from the gap to the added local.
    assert lines[span.start - 4 : span.start - 1] == [
        "def render(",
        "    template,",
        "    context,",
    ]
    assert flagged(lines, span) == ["):", '    """Fill the template."""', "    escape = True"]
    assert oracle(mutation) == [("pkg.api.page", "caller")]  # passes escape= by keyword


@pytest.mark.parametrize(
    ("name", "signature", "callers"),
    [
        ("pkg.store.label", "def label(name, prefix):", ["pkg.api.describe", "pkg.api.show"]),
        (
            "src/store:label",
            "export function label(name: string, prefix: string): string {",
            ["src/api:show"],
        ),
    ],
)
def test_reorder_params(
    toy_graph: CodeGraph, name: str, signature: str, callers: list[str]
) -> None:
    mutation, lines, span = run(toy_graph, reorder_params, name)

    assert flagged(lines, span) == [signature]
    assert oracle(mutation) == [(c, "caller") for c in callers]


@pytest.mark.parametrize(
    ("name", "line", "callee"),
    [
        ("pkg.store.load", "    row = fetch(db, key)", "pkg.store.fetch"),
        ("src/store:loadRow", "  const row = fetchRow(db, key);", "src/store:fetchRow"),
    ],
)
def test_drop_await(toy_graph: CodeGraph, name: str, line: str, callee: str) -> None:
    mutation, lines, span = run(toy_graph, drop_await, name)

    assert flagged(lines, span) == [line]
    assert oracle(mutation) == [(callee, "callee")]


def test_drop_await_skips_awaits_on_code_outside_the_repo(toy_graph: CodeGraph) -> None:
    # fetch() awaits db.get(): nothing in the graph says that is async.
    assert drop_await(get_target(toy_graph, "pkg.store.fetch")) is None


@pytest.mark.parametrize(
    ("name", "around", "callee"),
    [
        (
            "pkg.api.first_name",
            ["    item = find(items, key)", "    return item.name"],
            "pkg.store.find",
        ),
        (
            "src/api:firstName",
            ["  const item = find(items, key);", "  return item.name;"],
            "src/store:find",
        ),
    ],
)
def test_remove_guard(toy_graph: CodeGraph, name: str, around: list[str], callee: str) -> None:
    mutation, lines, span = run(toy_graph, remove_guard, name)

    # Whole lines went: the expected range is the two lines either side of the gap.
    assert flagged(lines, span) == around
    # The value came from find(), which can return nothing.
    assert oracle(mutation) == [(callee, "callee")]


def test_sql_format(toy_graph: CodeGraph) -> None:
    mutation, lines, span = run(toy_graph, sql_format, "pkg.store.save")

    assert flagged(lines, span) == [
        """    db.execute(f"insert into t (name, value) values ('{name}', '{value}')")"""
    ]
    assert oracle(mutation) == []  # db.execute is outside the repo
    assert sql_format(get_target(toy_graph, "src/store:find")) is None  # Python only


@pytest.mark.parametrize(
    ("name", "line", "caller"),
    [
        ("pkg.store.find", "    raise RuntimeError(key)", "pkg.api.show"),
        ("src/store:find", "  throw new TypeError(key);", "src/api:show"),
    ],
)
def test_exception_type(toy_graph: CodeGraph, name: str, line: str, caller: str) -> None:
    mutation, lines, span = run(toy_graph, exception_type, name)

    assert flagged(lines, span) == [line]
    # Only show() wraps the call in a handler for NotFound.
    assert oracle(mutation) == [(caller, "caller")]


def test_exception_type_needs_a_caller_that_catches_it(toy_graph: CodeGraph) -> None:
    assert exception_type(get_target(toy_graph, "pkg.store.check")) is None


# ---------------------------------------------------------------- clean


def test_rename_local_renames_every_use_and_only_variables(toy_graph: CodeGraph) -> None:
    mutation, lines, _ = run(toy_graph, rename_local, "pkg.api.describe")

    assert mutation.expected is None and mutation.oracle == ()
    assert lines[18:22] == [
        "def describe(items, key):",
        "    current = first_name(items, key)",
        "    check(len(key))",
        "    return label(current, key)",
    ]


def test_rename_local_uses_camel_case_in_typescript(toy_graph: CodeGraph) -> None:
    _, lines, _ = run(toy_graph, rename_local, "src/api:firstName")

    assert lines[12:16] == [
        "export function firstName(items: Item[], key: string): string {",
        "  const itemValue = find(items, key);",
        '  if (!itemValue) return "";',
        "  return itemValue.name;",
    ]


@pytest.mark.parametrize(
    ("name", "first", "lines_after"),
    [
        (
            "pkg.store.label",
            13,
            ["def label(prefix, name):", '    result = prefix + ":" + name', "    return result"],
        ),
        (
            "src/store:label",
            10,
            [
                "export function label(prefix: string, name: string): string {",
                "  const result = `${prefix}:${name}`;",
                "  return result;",
            ],
        ),
    ],
)
def test_extract_return(
    toy_graph: CodeGraph, name: str, first: int, lines_after: list[str]
) -> None:
    mutation, lines, _ = run(toy_graph, extract_return, name)

    assert mutation.expected is None
    assert lines[first - 1 : first + 2] == lines_after


def test_rename_local_does_not_double_a_trailing_underscore(toy_graph: CodeGraph) -> None:
    _, lines, _ = run(toy_graph, rename_local, "pkg.api.help_text")

    assert '    help_value = item.help or "_"' in lines
    assert "    return help_value.strip()" in lines
