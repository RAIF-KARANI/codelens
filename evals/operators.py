"""Mutation operators (planning.md §10.1) and behaviour-preserving edits.

Each operator looks at one function and returns at most one Mutation: the
first place where its bug can be planted *and* where catching it needs context
(a caller that uses the result, a callee that is async, ...). The nodes that
supply that context become the case's oracle. None means "not applicable here".
"""

import itertools
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from keyword import iskeyword

from tree_sitter import Node

from core.enums import NodeKind, Severity
from core.parser.types import NodeRef
from core.review.schemas import Category
from evals.edits import Edit, indent_at, line_end, line_start, on_own_lines
from evals.graph import Target
from evals.syntax import (
    CALLS,
    RAISES,
    CallSite,
    FunctionSyntax,
    Param,
    call_name,
    call_sites,
    caught_around,
    handler_classes,
    inside,
    last_name,
    parameters,
    raised_class,
    text,
)

Role = str  # "caller" | "callee"


@dataclass(frozen=True, slots=True)
class Mutation:
    edits: tuple[Edit, ...]
    description: str
    expected: tuple[Category, Severity] | None = None  # None: a clean, behaviour-preserving edit
    oracle: tuple[tuple[NodeRef, Role], ...] = ()
    subject: tuple[Node, ...] = ()  # what was mutated (a parameter, a raised class...)
    # Counterfactuals only: edits per file for a "prep" commit the case is based on.
    prep: tuple[tuple[str, tuple[Edit, ...]], ...] = ()


Operator = Callable[[Target], Mutation | None]

_HANDLERS = frozenset({"except_clause", "catch_clause", "finally_clause"})
_YIELDS = frozenset({"yield", "yield_expression"})


# ---------------------------------------------------------------- seeded bugs


def raise_to_none(t: Target) -> Mutation | None:
    """`raise X(...)` -> `return None` where callers use the return value."""
    if _is_special(t.node.name) or any(n.type in _YIELDS for n in t.fn.own()):
        return None
    users = _callers_where(t, lambda _fn, site: site.result_used)
    if not users:
        return None
    for node in t.fn.own():
        if node.type not in RAISES or inside(node, _HANDLERS, t.fn.node):
            continue
        cls = raised_class(node)
        if cls is None or last_name(cls) == "NotImplementedError":  # abstract, not an error path
            continue
        if t.fn.python:
            new = "return None"
        else:
            new = "return undefined;" if text(node).endswith(";") else "return undefined"
        return Mutation(
            (Edit(node.start_byte, node.end_byte, new.encode()),),
            f"`{_first_line(node)}` became `{new}`; callers use the result",
            (Category.BUG, Severity.CRITICAL),
            _roles(users, "caller"),
        )
    return None


def drop_param(t: Target) -> Mutation | None:
    """Remove a parameter that callers pass; the body keeps working with the
    old default as a local, so only the callers show the break."""
    params = parameters(t.fn)
    anchor = _first_statement(t.fn)
    if not params or anchor is None:
        return None
    for param in params:
        name, default = param.name, param.default
        if name is None or default is None or b"\n" in (default.text or b""):
            continue
        users = _callers_where(t, lambda _fn, site, p=param: _passes(site, p))
        if not users:
            continue
        content = t.content
        annotation = _annotation(param, t.fn.python)
        if t.fn.python:
            line = f"{name}{annotation} = {text(default)}\n"
        else:
            line = f"const {name}{annotation} = {text(default)};\n"
        at = line_start(content, anchor.start_byte)
        insert = indent_at(content, anchor.start_byte) + line.encode()
        removal = _remove_item(param.node, content)
        # A parameter on its own line goes as a whole line, so the signature
        # adds nothing to the diff; the new local is then the added line a
        # reviewer can comment on, so it carries the expectation too.
        own_line = removal.start == line_start(content, removal.start)
        return Mutation(
            (removal, Edit(at, at, insert, primary=own_line)),
            f"Dropped parameter `{name}` (its default became a local); callers still pass it",
            (Category.API_CONTRACT, Severity.CRITICAL),
            _roles(users, "caller"),
            subject=(param.node,),
        )
    return None


def reorder_params(t: Target) -> Mutation | None:
    """Swap two adjacent parameters of the same shape; positional callers now
    pass each value to the other."""
    params = parameters(t.fn)
    positional = [p for p in params or [] if p.index is not None and p.name]
    for a, b in itertools.pairwise(positional):
        if (a.default is None) != (b.default is None):
            continue  # swapping would put a required parameter after a defaulted one
        if _text(a.annotation) != _text(b.annotation):
            continue  # a type checker would catch it; we want the silent kind
        users = _callers_where(t, lambda _fn, site, b=b: site.positional > (b.index or 0))
        if users:
            return Mutation(
                (
                    Edit(a.node.start_byte, a.node.end_byte, b.node.text or b""),
                    Edit(b.node.start_byte, b.node.end_byte, a.node.text or b""),
                ),
                f"Swapped parameters `{a.name}` and `{b.name}`; callers pass both by position",
                (Category.API_CONTRACT, Severity.WARNING),
                _roles(users, "caller"),
                subject=(a.node, b.node),
            )
    return None


def drop_await(t: Target) -> Mutation | None:
    """Remove `await` before a call to an async function in the repo."""
    if not t.fn.is_async:
        return None
    async_callees: dict[str, NodeRef] = {}
    for ref in t.graph.callees(t.node.ref):
        callee = t.graph.function(ref)
        if callee is not None and callee.is_async:
            async_callees.setdefault(callee.name, ref)
    for node in t.fn.own():
        if node.type not in ("await", "await_expression") or not node.named_children:
            continue
        call = node.named_children[0]
        ref = async_callees.get(call_name(call) or "") if call.type in CALLS else None
        if ref is not None:
            return Mutation(
                (Edit(node.start_byte, call.start_byte, b""),),
                f"Removed `await` before `{_first_line(call)}`, an async function",
                (Category.CONCURRENCY, Severity.CRITICAL),
                ((ref, "callee"),),
            )
    return None


def remove_guard(t: Target) -> Mutation | None:
    """Delete an `if x is None: return` style guard before `x` is dereferenced.
    The oracle is where `x` comes from: the callers (a parameter) or the callee
    that produced it."""
    content = t.content
    own = list(t.fn.own())
    for node in own:
        if node.type != "if_statement" or not on_own_lines(content, node.start_byte, node.end_byte):
            continue
        name = _null_checked(node, t.fn.python)
        if name is None or not _dereferenced_after(own, name, node.end_byte, t.fn.python):
            continue
        oracle = _origin(t, own, name, node)
        if oracle:
            start = line_start(content, node.start_byte)
            end = line_end(content, node.end_byte)
            return Mutation(
                (Edit(start, end, b""),),
                f"Deleted the guard `{_first_line(node)}` before `{name}` is used",
                (Category.BUG, Severity.WARNING),
                oracle,
            )
    return None


_SQL_METHOD = re.compile(r"execute|query|where", re.IGNORECASE)


def sql_format(t: Target) -> Mutation | None:
    """Python: `execute("... = ?", [v])` -> `execute(f"... = '{v}'")`, plain or
    f-string queries alike."""
    if not t.fn.python:
        return None
    for node in t.fn.own():
        args = node.child_by_field_name("arguments") if node.type == "call" else None
        items = [
            a
            for a in (args.named_children if args else [])
            if a.type not in ("comment", "keyword_argument")  # order_by=... is fine
        ]
        if len(items) != 2 or items[1].type not in ("list", "tuple"):
            continue
        query, params = items
        values = [v for v in params.named_children if v.type != "comment"]
        parts = _query_parts(query)
        if (
            not parts
            or not values
            or any(v.type not in ("identifier", "attribute") for v in values)
        ):
            continue
        # A full statement, or a WHERE fragment for a method that takes one.
        is_sql = re.search(rb"(?i)\b(select|insert|update|delete)\b", query.text or b"")
        if not is_sql and not _SQL_METHOD.search(call_name(node) or ""):
            continue
        placeholders = sum((c.text or b"").count(b"?") for p in parts for c in _contents(p))
        if placeholders != len(values):
            continue
        new = _interpolate(query, parts, [v.text or b"" for v in values])
        ref = _callee_named(t, call_name(node))
        return Mutation(
            (Edit(query.start_byte, params.end_byte, new),),
            "Replaced a parameterised query with string interpolation",
            (Category.SECURITY, Severity.CRITICAL),
            ((ref, "callee"),) if ref else (),
        )
    return None


_PY_SWAPS = ("RuntimeError", "ValueError", "TypeError")
_JS_SWAPS = ("TypeError", "RangeError", "SyntaxError")


def exception_type(t: Target) -> Mutation | None:
    """Raise a different exception class than the one callers catch."""
    for node in t.fn.own():
        if node.type not in RAISES or inside(node, _HANDLERS, t.fn.node):
            continue
        cls = raised_class(node)
        name = last_name(cls) if cls is not None else None
        if cls is None or not name:
            continue
        handlers, caught = [], set()
        for ref in t.graph.callers(t.node.ref):
            caller = t.graph.function(ref)
            for site in call_sites(caller, t.node.name) if caller else []:
                names = caught_around(caller, site.node)
                if name in names:
                    handlers.append(ref)
                    caught |= names
                    break
        # A broad handler (or `instanceof Error`) would still catch the new class.
        if not handlers or caught & {"Exception", "BaseException", "Error"}:
            continue
        swaps = _PY_SWAPS if t.fn.python else _JS_SWAPS
        new = next((s for s in swaps if s != name and s not in caught), None)
        if new is not None:
            return Mutation(
                (Edit(cls.start_byte, cls.end_byte, new.encode()),),
                f"Raises `{new}` instead of `{name}`, which callers catch",
                (Category.ERROR_HANDLING, Severity.WARNING),
                _roles(handlers, "caller"),
                subject=(cls,),
            )
    return None


# ---------------------------------------------------------------- counterfactuals
#
# The same diff as a seeded operator, on a base where a "prep" commit already
# made every caller compatible (planning Q10). A reviewer who reads only the
# diff sees exactly the seeded bug; only the context, indexed at the prep
# commit, shows that it is safe. Limited to private functions whose every call
# in the repo is a resolved call site: the prep provably reaches all of them,
# and no outside user can depend on the old signature.


def drop_param_cf(t: Target) -> Mutation | None:
    """drop_param's diff, after a prep commit stopped every call passing it."""
    seeded = drop_param(t)
    sites = _closed_private_sites(t) if seeded else None
    if seeded is None or sites is None:
        return None
    param = _param(t, seeded.subject[0])
    prep: dict[str, list[Edit]] = defaultdict(list)
    for path, _fn, site in sites:
        ok, arg = _argument(site, param)
        if not ok:
            return None
        if arg is not None:
            prep[path].append(_remove_item(arg, t.graph.content(path)))
    return _counterfactual(
        seeded, prep, f"Dropped parameter `{param.name}`; no call passes it any more"
    )


def reorder_params_cf(t: Target) -> Mutation | None:
    """reorder_params' diff, after a prep commit swapped the two arguments at
    every positional call."""
    seeded = reorder_params(t)
    sites = _closed_private_sites(t) if seeded else None
    if seeded is None or sites is None:
        return None
    a, b = (_param(t, node) for node in seeded.subject)
    if a.index is None or b.index is None:
        return None
    prep: dict[str, list[Edit]] = defaultdict(list)
    for path, _fn, site in sites:
        positional = _positional(site)
        if len(positional) > b.index:
            x, y = positional[a.index], positional[b.index]
            prep[path] += [
                Edit(x.start_byte, x.end_byte, y.text or b""),
                Edit(y.start_byte, y.end_byte, x.text or b""),
            ]
    return _counterfactual(
        seeded, prep, f"Swapped parameters `{a.name}` and `{b.name}`; every call already swapped"
    )


def exception_type_cf(t: Target) -> Mutation | None:
    """Python: exception_type's diff, after a prep commit made every handler
    around a call also catch the new class."""
    seeded = exception_type(t) if t.fn.python else None
    sites = _closed_private_sites(t) if seeded else None
    if seeded is None or sites is None:
        return None
    name = last_name(seeded.subject[0]) or ""
    new = seeded.edits[0].text.decode()
    prep: dict[str, dict[tuple[int, int], Edit]] = defaultdict(dict)
    for path, fn, site in sites:
        handlers = handler_classes(fn, site.node, name)
        if not handlers:
            return None  # this call lets the exception escape: its own callers may catch it
        for node in handlers:
            if node.parent is not None and node.parent.type == "tuple":
                edit = Edit(node.end_byte, node.end_byte, f", {new}".encode())
            else:
                edit = Edit(node.start_byte, node.end_byte, f"({text(node)}, {new})".encode())
            prep[path][(edit.start, edit.end)] = edit  # one try can guard several calls
    flat = {path: list(edits.values()) for path, edits in prep.items()}
    return _counterfactual(seeded, flat, f"Raises `{new}` instead of `{name}`; callers catch both")


# ---------------------------------------------------------------- clean edits

_RENAMES = {
    "result": "outcome",
    "value": "current",
    "data": "payload",
    "res": "response",
    "ret": "returned",
    "tmp": "scratch",
    "i": "index",
    "x": "item",
    "s": "text",
    "n": "count",
    "k": "key",
    "v": "val",
}


def rename_local(t: Target) -> Mutation | None:
    """Rename a local variable everywhere it is used."""
    fn, python = t.fn, t.fn.python
    own = list(fn.own())
    if python and any(n.type in ("global_statement", "nonlocal_statement") for n in own):
        return None
    params = {text(n) for n in _subtree(fn.parameters) if n.type == "identifier"}
    taken = fn.identifiers()
    everywhere = [n for n in _subtree(fn.node) if n.type == "identifier" and not _is_label(n)]
    for name in dict.fromkeys(_assigned_names(own, python)):
        if name in params:
            continue
        sites = [n for n in own if n.type == "identifier" and text(n) == name and not _is_label(n)]
        # Used in a nested scope (a closure) or an import too: leave it alone.
        if len(sites) != sum(text(n) == name for n in everywhere):
            continue
        if any(_in_import(n) for n in sites):
            continue
        # Each language's own convention, so the rename isn't a style finding.
        suffixes = ("_value", "_item") if python else ("Value", "Item")
        # `help_` (dodging a builtin) -> `help_value`, not `help__value`.
        stem = name.rstrip("_") or name
        options = (_RENAMES.get(name), *(stem + suffix for suffix in suffixes))
        new = next((c for c in options if c and c not in taken and not iskeyword(c)), None)
        if new is not None:
            return Mutation(
                tuple(Edit(n.start_byte, n.end_byte, new.encode()) for n in sites),
                f"Renamed local `{name}` to `{new}`",
            )
    return None


_TRIVIAL = frozenset(
    {"identifier", "none", "true", "false", "integer", "float", "string", "number", "null"}
)
_UNDEFINED = "undefined"


def extract_return(t: Target) -> Mutation | None:
    """`return expr` -> `result = expr; return result`."""
    taken = t.fn.identifiers()
    var = next((n for n in ("result", "outcome", "retval") if n not in taken), None)
    content = t.content
    for node in t.fn.own() if var else []:
        values = [c for c in node.named_children if c.type != "comment"]
        if node.type != "return_statement" or len(values) != 1:
            continue
        value = values[0]
        if value.type in _TRIVIAL or text(value) == _UNDEFINED:
            continue
        if node.start_point[0] != node.end_point[0]:
            continue
        if not on_own_lines(content, node.start_byte, node.end_byte):
            continue
        if any(n.type in _YIELDS for n in _subtree(value)):
            continue
        indent = indent_at(content, node.start_byte).decode()
        if t.fn.python:
            new = f"{var} = {text(value)}\n{indent}return {var}"
        else:
            semi = ";" if text(node).endswith(";") else ""
            new = f"const {var} = {text(value)}{semi}\n{indent}return {var}{semi}"
        return Mutation(
            (Edit(node.start_byte, node.end_byte, new.encode()),),
            f"Moved the returned expression into a local `{var}`",
        )
    return None


SEEDED: dict[str, Operator] = {
    "raise_to_none": raise_to_none,
    "drop_param": drop_param,
    "reorder_params": reorder_params,
    "drop_await": drop_await,
    "remove_guard": remove_guard,
    "sql_format": sql_format,
    "exception_type": exception_type,
}
COUNTERFACTUAL: dict[str, Operator] = {
    "drop_param_cf": drop_param_cf,
    "reorder_params_cf": reorder_params_cf,
    "exception_type_cf": exception_type_cf,
}
CLEAN: dict[str, Operator] = {
    "rename_local": rename_local,
    "extract_return": extract_return,
}


# ---------------------------------------------------------------- counterfactual helpers


def _counterfactual(
    seeded: Mutation, prep: dict[str, list[Edit]], description: str
) -> Mutation | None:
    if not any(prep.values()):
        return None  # nothing to prepare: the base would be the seeded bug's base
    return Mutation(
        seeded.edits,
        description,
        expected=None,
        oracle=seeded.oracle,  # the callers whose (prepared) code shows it is safe
        subject=seeded.subject,
        prep=tuple(sorted((path, tuple(edits)) for path, edits in prep.items() if edits)),
    )


def _closed_private_sites(t: Target) -> list[tuple[str, FunctionSyntax, CallSite]] | None:
    """Every call site of a private function, or None if the function is
    public, a call can't be accounted for, or a call unpacks *args."""
    if not _private(t):
        return None
    sites = []
    for ref in t.graph.callers(t.node.ref):
        fn = t.graph.function(ref)
        for site in call_sites(fn, t.node.name) if fn else []:
            if site.unpacks:
                return None
            sites.append((t.graph.nodes[ref][0].path, fn, site))
    # Every `name(` in the repo, tests and module-level code included, must be
    # one of those sites; anything else (an unresolved obj.name() call, a test)
    # could still use the old signature.
    pattern = re.compile(rf"(?<![\w$]){re.escape(t.node.name)}\s*\(")
    mentions = sum(len(pattern.findall(text)) for text in t.graph.texts.values())
    body = t.fn.body
    header = t.content[t.fn.node.start_byte : body.start_byte if body else t.fn.node.end_byte]
    mentions -= len(pattern.findall(header.decode("utf-8", "replace")))  # the definition
    return sites if sites and mentions == len(sites) else None


def _private(t: Target) -> bool:
    name = t.node.name
    if "<locals>" in t.node.qualified_name:
        return True
    if t.fn.python:
        return name.startswith("_") and not name.startswith("__")
    code = t.node.code.lstrip()
    if t.node.kind is NodeKind.FUNCTION:
        return not code.startswith("export")
    return code.startswith(("private ", "#")) or name.startswith(("_", "#"))


def _param(t: Target, node: Node) -> Param:
    return next(p for p in parameters(t.fn) or [] if p.node == node)


def _positional(site: CallSite) -> list[Node]:
    args = site.node.child_by_field_name("arguments")
    return [
        a
        for a in (args.named_children if args else [])
        if a.type not in ("comment", "keyword_argument")
    ]


def _argument(site: CallSite, param: Param) -> tuple[bool, Node | None]:
    """The argument a call passes for `param`: (True, node), (True, None) when
    it doesn't pass it, or (False, None) when removing it would shift later
    positional arguments onto the wrong parameters."""
    args = site.node.child_by_field_name("arguments")
    for arg in args.named_children if args else []:
        if arg.type == "keyword_argument" and _field(arg, "name") == param.name:
            return True, arg
    positional = _positional(site)
    if param.index is None or len(positional) <= param.index:
        return True, None
    if len(positional) != param.index + 1:
        return False, None
    return True, positional[param.index]


def _field(node: Node, name: str) -> str | None:
    child = node.child_by_field_name(name)
    return text(child) if child is not None else None


# ---------------------------------------------------------------- helpers


def _callers_where(t: Target, check: Callable[[FunctionSyntax, CallSite], bool]) -> list[NodeRef]:
    found = []
    for ref in t.graph.callers(t.node.ref):
        caller = t.graph.function(ref)
        if caller is not None and any(check(caller, s) for s in call_sites(caller, t.node.name)):
            found.append(ref)
    return found


def _roles(refs: list[NodeRef], role: Role) -> tuple[tuple[NodeRef, Role], ...]:
    return tuple((ref, role) for ref in refs)


def _callee_named(t: Target, name: str | None) -> NodeRef | None:
    for ref in t.graph.callees(t.node.ref):
        if t.graph.nodes[ref][1].name == name:
            return ref
    return None


def _is_special(name: str) -> bool:
    """Constructors and dunders: their return value is not what callers use."""
    return name == "constructor" or (name.startswith("__") and name.endswith("__"))


def _first_line(node: Node) -> str:
    return text(node).split("\n", 1)[0].strip()[:80]


def _text(node: Node | None) -> str | None:
    return text(node) if node is not None else None


def _subtree(node: Node | None) -> list[Node]:
    found, stack = [], [node] if node is not None else []
    while stack:
        current = stack.pop()
        found.append(current)
        stack.extend(current.children)
    return found


def _passes(site: CallSite, param: Param) -> bool:
    if site.unpacks:
        return False  # can't tell what *args carries
    by_position = param.index is not None and site.positional > param.index
    return by_position or param.name in site.keywords


def _annotation(param: Param, python: bool) -> str:
    if param.annotation is None:
        return ""
    # Python's `type` node is the bare type; TS's type_annotation includes ": ".
    return f": {text(param.annotation)}" if python else text(param.annotation)


def _first_statement(fn: FunctionSyntax) -> Node | None:
    """Where a new first line of the body goes: after a docstring, and only
    when the body starts on its own line."""
    body = fn.body
    if body is None or (not fn.python and body.type != "statement_block"):
        return None
    statements = [c for c in body.named_children if c.type != "comment"]
    if fn.python and statements and _is_docstring(statements[0]):
        statements = statements[1:]
    if not statements:
        return None
    header_row = (fn.parameters or fn.node).end_point[0] if fn.python else body.start_point[0]
    return statements[0] if statements[0].start_point[0] > header_row else None


def _is_docstring(node: Node) -> bool:
    return node.type == "expression_statement" and [c.type for c in node.named_children] == [
        "string"
    ]


def _remove_item(node: Node, content: bytes) -> Edit:
    """Remove a parameter or argument and the comma that separates it from its neighbour:
    its whole line when it has one (`a,\\n    b=1,\\n):` -> `a,\\n):`)."""
    siblings = node.parent.children if node.parent else []
    i = next(i for i, s in enumerate(siblings) if s == node)
    before = siblings[i - 1] if i > 0 else None
    after = siblings[i + 1] if i + 1 < len(siblings) else None
    end = after.end_byte if after is not None and after.type == "," else node.end_byte
    if on_own_lines(content, node.start_byte, end):
        return Edit(line_start(content, node.start_byte), line_end(content, end), b"")
    if before is not None and before.type == ",":
        return Edit(before.start_byte, node.end_byte, b"")
    if after is not None and after.type == ",":
        following = siblings[i + 2]
        end = following.start_byte if following.type != ")" else after.end_byte
        return Edit(node.start_byte, end, b"")
    return Edit(node.start_byte, node.end_byte, b"")


_PY_NONE_CHECK = re.compile(r"([A-Za-z_]\w*) is None|not ([A-Za-z_]\w*)")
_JS_NULL_CHECK = re.compile(
    r"\(\s*(?:([A-Za-z_$][\w$]*)\s*===?\s*(?:null|undefined)|!\s*([A-Za-z_$][\w$]*))\s*\)"
)
_EXITS = frozenset({"return_statement", "raise_statement", "throw_statement", "continue_statement"})


def _null_checked(node: Node, python: bool) -> str | None:
    """`x` for `if x is None: return` / `if (!x) return;` with no else branch."""
    if any(c.type in ("elif_clause", "else_clause") for c in node.children):
        return None
    if node.child_by_field_name("alternative") is not None:
        return None
    condition = node.child_by_field_name("condition")
    consequence = node.child_by_field_name("consequence")
    if condition is None or consequence is None:
        return None
    pattern = _PY_NONE_CHECK if python else _JS_NULL_CHECK
    match = pattern.fullmatch(text(condition))
    if match is None:
        return None
    if consequence.type in ("block", "statement_block"):
        statements = [c for c in consequence.named_children if c.type != "comment"]
    else:
        statements = [consequence]
    if len(statements) != 1 or statements[0].type not in _EXITS:
        return None
    return match.group(1) or match.group(2)


def _dereferenced_after(own: list[Node], name: str, after: int, python: bool) -> bool:
    fields = (
        {"attribute": "object", "subscript": "value", "call": "function"}
        if python
        else {
            "member_expression": "object",
            "subscript_expression": "object",
            "call_expression": "function",
        }
    )
    for node in own:
        field = fields.get(node.type)
        if field is None or node.start_byte < after:
            continue
        target = node.child_by_field_name(field)
        if target is not None and target.type == "identifier" and text(target) == name:
            return True
    return False


def _origin(t: Target, own: list[Node], name: str, guard: Node) -> tuple[tuple[NodeRef, Role], ...]:
    """Where the guarded value comes from: the last assignment before the
    guard (the in-repo function whose result it holds), else the callers if it
    is a parameter. Empty if neither is in the graph."""
    assigned: Node | None = None
    for node in own:
        if node.start_byte >= guard.start_byte:
            break
        if node.type == "assignment":
            target, value = node.child_by_field_name("left"), node.child_by_field_name("right")
        elif node.type == "variable_declarator":
            target, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        else:
            continue
        if target is not None and value is not None and text(target) == name:
            assigned = value
    if assigned is not None:
        if assigned.type in ("await", "await_expression") and assigned.named_children:
            assigned = assigned.named_children[0]
        ref = _callee_named(t, call_name(assigned)) if assigned.type in CALLS else None
        return ((ref, "callee"),) if ref is not None else ()
    if any(p.name == name for p in parameters(t.fn) or []):
        return _roles(t.graph.callers(t.node.ref), "caller")
    return ()


def _query_parts(node: Node) -> list[Node]:
    """The literal parts of a query string that can take interpolation: plain
    or f-strings (r allowed, b not). A plain part holding `?` must have no
    braces, since adding `f` would turn them into interpolations. Empty if the
    query doesn't qualify."""
    if node.type == "string":
        parts = [node]
    elif node.type == "concatenated_string":
        parts = [c for c in node.named_children if c.type == "string"]
    else:
        return []
    for part in parts:
        prefix = _string_prefix(part).lower()
        if not set(prefix) <= {"f", "r"}:
            return []
        literal = b"".join(c.text or b"" for c in _contents(part))
        if "f" not in prefix and b"?" in literal and (b"{" in literal or b"}" in literal):
            return []
    return parts


def _string_prefix(part: Node) -> str:
    start = next((c for c in part.children if c.type == "string_start"), None)
    return text(start).rstrip("'\"") if start is not None else "b"  # unknown: reject


def _contents(part: Node) -> list[Node]:
    """The literal text of a string, outside any {interpolation}."""
    return [c for c in part.children if c.type == "string_content"]


def _interpolate(query: Node, parts: list[Node], values: list[bytes]) -> bytes:
    """Rebuild the query text, turning each `?` into a quoted `{value}` and
    each plain part that gained a value into an f-string."""
    source = query.text or b""
    edits: list[tuple[int, int, bytes]] = []  # (start, end, text) relative to the query
    remaining = iter(values)
    for part in parts:
        quote = b'"' if _string_prefix(part) + "'" in text(part)[:4] else b"'"
        changed = False
        for content in _contents(part):
            literal = content.text or b""
            if b"?" not in literal:
                continue
            pieces = literal.split(b"?")
            literal = pieces[0]
            for piece in pieces[1:]:
                literal += quote + b"{" + next(remaining) + b"}" + quote + piece
            edits.append((content.start_byte, content.end_byte, literal))
            changed = True
        if changed and "f" not in _string_prefix(part).lower():
            edits.append((part.start_byte, part.start_byte, b"f"))
    out, pos = bytearray(), 0
    for start, end, new in sorted(edits):
        out += source[pos : start - query.start_byte] + new
        pos = end - query.start_byte
    return bytes(out + source[pos:])


def _assigned_names(own: list[Node], python: bool) -> list[str]:
    names = []
    for node in own:
        if python and node.type == "assignment":
            target = node.child_by_field_name("left")
        elif not python and node.type == "variable_declarator":
            target = node.child_by_field_name("name")
        else:
            continue
        if target is not None and target.type == "identifier":
            names.append(text(target))
    return names


def _is_label(node: Node) -> bool:
    """An identifier that names something other than the variable: the `x`
    in `f(x=1)` or `obj.x`."""
    parent = node.parent
    if parent is None:
        return False
    if parent.type == "keyword_argument":
        return parent.child_by_field_name("name") == node
    if parent.type == "attribute":
        return parent.child_by_field_name("attribute") == node
    return False


def _in_import(node: Node) -> bool:
    current = node.parent
    while current is not None:
        if current.type in ("import_statement", "import_from_statement", "dotted_name"):
            return True
        current = current.parent
    return False
