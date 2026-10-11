"""Tree-sitter views of functions and call sites, for the mutation operators.

core.parser gives each function's line range and the resolved call graph. The
operators also need what is *inside* a function: its parameters, raises, awaits,
and the arguments at each call site. This module reads those from the syntax tree.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from tree_sitter import Node

# Nodes that open a new scope. "A function's own code" stops at them: a raise in
# a nested helper belongs to the helper, which is a separate graph node.
SCOPES = frozenset(
    {
        "function_definition",
        "class_definition",
        "lambda",  # Python
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "generator_function",
        "arrow_function",
        "method_definition",
        "class_declaration",
        "abstract_class_declaration",
        "class",  # JS/TS
    }
)
_JS_DECLARATIONS = frozenset({"function_declaration", "generator_function_declaration"})
_JS_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "generator_function"})
_JS_FIELDS = frozenset({"field_definition", "public_field_definition"})
CALLS = frozenset({"call", "call_expression"})
RAISES = frozenset({"raise_statement", "throw_statement"})


def text(node: Node) -> str:
    return (node.text or b"").decode()


@dataclass(frozen=True, slots=True)
class FunctionSyntax:
    """One function in the syntax tree. `node` is the function itself
    (function_definition, method_definition, arrow_function, ...), not the
    `export`/`const` around it."""

    node: Node
    name: str
    python: bool

    @property
    def parameters(self) -> Node | None:
        return self.node.child_by_field_name("parameters")

    @property
    def body(self) -> Node | None:
        """A block for Python; a statement_block, or an expression for `x => x + 1`."""
        return self.node.child_by_field_name("body")

    @property
    def is_async(self) -> bool:
        return any(c.type == "async" for c in self.node.children)

    def own(self) -> Iterator[Node]:
        """Every node of the body in source order, not entering nested scopes."""
        if self.body is None:
            return
        stack = [self.body]
        while stack:
            node = stack.pop()
            yield node
            if node.type not in SCOPES:
                stack.extend(reversed(node.children))

    def identifiers(self) -> set[str]:
        """Every identifier anywhere in the function, nested scopes included:
        a new name must not collide with any of them."""
        names: set[str] = set()
        stack = [self.node]
        while stack:
            node = stack.pop()
            if node.type in ("identifier", "shorthand_property_identifier"):
                names.add(text(node))
            stack.extend(node.children)
        return names


def functions_in(root: Node, *, python: bool) -> list[FunctionSyntax]:
    """Every function with a body in a file, in source order. Mirrors what
    core.parser turns into function/method nodes."""
    found: list[FunctionSyntax] = []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.children))
        kind = node.type
        if python:
            if kind == "function_definition":
                found.append(FunctionSyntax(node, _field_text(node, "name"), True))
            continue
        if kind in _JS_DECLARATIONS or kind == "method_definition":
            found.append(FunctionSyntax(node, _field_text(node, "name"), False))
        elif kind == "variable_declarator" or kind in _JS_FIELDS:
            value = node.child_by_field_name("value")
            name = node.child_by_field_name("name") or node.child_by_field_name("property")
            if value is not None and name is not None and value.type in _JS_FUNCTION_VALUES:
                found.append(FunctionSyntax(value, text(name), False))
    return found


def _field_text(node: Node, field: str) -> str:
    child = node.child_by_field_name(field)
    return text(child) if child is not None else ""


# ---------------------------------------------------------------- parameters


@dataclass(frozen=True, slots=True)
class Param:
    node: Node
    name: str | None  # None for a destructuring pattern
    index: int | None  # positional slot, `self`/`cls` not counted; None = keyword-only
    default: Node | None
    annotation: Node | None


def parameters(fn: FunctionSyntax) -> list[Param] | None:
    """The function's parameters, or None if it takes *args/**kwargs/...rest:
    then a call's argument count says nothing about which parameter gets what."""
    params_node = fn.parameters
    if params_node is None:
        return None
    params: list[Param] = []
    index: int | None = 0
    for child in params_node.named_children:
        kind = child.type
        if kind in ("comment", "positional_separator"):
            continue
        if kind == "keyword_separator":
            index = None
            continue
        if kind in ("list_splat_pattern", "dictionary_splat_pattern"):
            return None
        if fn.python:
            name_node = child if kind == "identifier" else _python_param_name(child)
            if name_node is None:
                return None  # *args: str, tuple patterns
            name = text(name_node)
            if not params and index == 0 and name in ("self", "cls"):
                continue
        else:
            pattern = child.child_by_field_name("pattern")
            if pattern is None or pattern.type == "rest_pattern":
                return None
            if pattern.type == "this":  # TS `this: Foo` declares a type, not an argument
                continue
            name = text(pattern) if pattern.type == "identifier" else None
        params.append(
            Param(
                child,
                name,
                index,
                child.child_by_field_name("value"),
                child.child_by_field_name("type"),
            )
        )
        if index is not None:
            index += 1
    return params


def _python_param_name(node: Node) -> Node | None:
    named = node.child_by_field_name("name")
    if named is not None:
        return named
    first = node.named_children[0] if node.named_children else None
    return first if first is not None and first.type == "identifier" else None


# ---------------------------------------------------------------- call sites


@dataclass(frozen=True, slots=True)
class CallSite:
    node: Node  # call / call_expression
    positional: int  # positional arguments, not counting unpacked ones
    keywords: frozenset[str]
    unpacks: bool  # f(*a) / f(**kw) / f(...a): more may be passed than we can see
    result_used: bool  # the value is assigned, returned, dereferenced, passed on...


def call_name(call: Node) -> str | None:
    """`f(...)` -> "f"; `obj.f(...)` / `self.f(...)` / `this.f(...)` -> "f"."""
    function = call.child_by_field_name("function")
    return last_name(function) if function is not None else None


def last_name(node: Node) -> str | None:
    if node.type in ("identifier", "property_identifier"):
        return text(node)
    field = {"attribute": "attribute", "member_expression": "property"}.get(node.type)
    tail = node.child_by_field_name(field) if field else None
    return text(tail) if tail is not None else None


def call_sites(fn: FunctionSyntax, name: str) -> list[CallSite]:
    """Calls to `name` in the function's own code. The graph already says the
    caller calls the callee; matching by name finds *where*."""
    sites = []
    for node in fn.own():
        if node.type not in CALLS or call_name(node) != name:
            continue
        positional, keywords, unpacks = 0, set(), False
        args = node.child_by_field_name("arguments")
        for arg in args.named_children if args is not None else []:
            if arg.type == "comment":
                continue
            if arg.type == "keyword_argument":
                keywords.add(_field_text(arg, "name"))
            elif arg.type in ("list_splat", "dictionary_splat", "spread_element"):
                unpacks = True
            else:
                positional += 1
        parent = node.parent
        if parent is not None and parent.type in ("await", "await_expression"):
            parent = parent.parent
        used = parent is not None and parent.type != "expression_statement"
        sites.append(CallSite(node, positional, frozenset(keywords), unpacks, used))
    return sites


# ---------------------------------------------------------------- exceptions


def raised_class(statement: Node) -> Node | None:
    """The expression naming the class in `raise X(...)` / `raise mod.X` /
    `throw new X(...)`. None for a bare `raise` or `throw err`."""
    if not statement.named_children:
        return None
    expr = statement.named_children[0]
    if expr.type == "call":
        expr = expr.child_by_field_name("function") or expr
    elif expr.type == "new_expression":
        expr = expr.child_by_field_name("constructor") or expr
    if expr.type in ("identifier", "attribute", "member_expression"):
        # `raise ValueError` names a class; `raise err` / `throw err` re-raises a
        # variable. Without types, the naming convention tells them apart.
        if statement.named_children[0].type == "identifier" and not text(expr)[:1].isupper():
            return None
        return expr
    return None


def caught_around(fn: FunctionSyntax, call: Node) -> frozenset[str]:
    """Exception class names that try/except (Python) or `catch` + `instanceof`
    (JS/TS) handle around this call, within the function."""
    names: set[str] = set()
    for try_node in _enclosing_trys(fn, call):
        names |= _handled(try_node, fn.python)
    return frozenset(names)


def handler_classes(fn: FunctionSyntax, call: Node, name: str) -> list[Node]:
    """Python: the expressions naming class `name` in the `except` clauses that
    protect this call (`X` in `except X:` or `except (X, Y) as e:`)."""
    found = []
    for try_node in _enclosing_trys(fn, call):
        for clause in (c for c in try_node.children if c.type == "except_clause"):
            value = _except_value(clause)
            items = value.named_children if value is not None and value.type == "tuple" else [value]
            found += [n for n in items if n is not None and last_name(n) == name]
    return found


def _enclosing_trys(fn: FunctionSyntax, call: Node) -> Iterator[Node]:
    node = call
    while node is not None and node != fn.node:
        parent = node.parent
        # Only the try body is protected; a call inside `except` is not.
        if (
            parent is not None
            and parent.type == "try_statement"
            and parent.child_by_field_name("body") == node
        ):
            yield parent
        node = parent


def _except_value(clause: Node) -> Node | None:
    """`X` / `(X, Y)` in `except X as e:`, without the `as e`."""
    value = clause.child_by_field_name("value")
    if value is None:
        value = next((c for c in clause.named_children if c.type != "block"), None)
    if value is not None and value.type == "as_pattern" and value.named_children:
        value = value.named_children[0]
    return value


def _handled(try_node: Node, python: bool) -> set[str]:
    names: set[str] = set()
    if python:
        for clause in (c for c in try_node.children if c.type == "except_clause"):
            names |= _class_names(_except_value(clause))
        return names
    handler = try_node.child_by_field_name("handler")
    stack = [handler] if handler is not None else []
    while stack:
        node = stack.pop()
        if node.type == "binary_expression" and any(c.type == "instanceof" for c in node.children):
            right = node.child_by_field_name("right")
            name = last_name(right) if right is not None else None
            if name:
                names.add(name)
        stack.extend(node.children)
    return names


def _class_names(node: Node | None) -> set[str]:
    if node is None:
        return set()
    if node.type in ("tuple", "parenthesized_expression"):
        return set().union(*(_class_names(c) for c in node.named_children))
    name = last_name(node)
    return {name} if name else set()


def inside(node: Node, types: frozenset[str] | set[str], stop: Node) -> bool:
    """Is `node` nested in a node of one of `types`, below `stop`?"""
    current = node.parent
    while current is not None and current != stop:
        if current.type in types:
            return True
        current = current.parent
    return False
