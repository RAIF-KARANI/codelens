"""Shared machinery for the language extractors: scopes, qualified names,
ordinals, and collecting nodes and references."""

import hashlib
import inspect
import re
import textwrap
from dataclasses import dataclass

from tree_sitter import Node

from core.enums import EdgeKind, NodeKind
from core.parser.types import NodeRef, ParsedNode, Reference

_WHITESPACE = re.compile(r"\s+")
_SPACE_AFTER_OPEN = re.compile(r"([(\[]) ")
_SPACE_BEFORE_CLOSE = re.compile(r" ([)\]])")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_signature(text: str) -> str:
    """Collapse a multi-line signature onto one line: `def f(\\n a,\\n b)` -> `def f(a, b)`."""
    text = _WHITESPACE.sub(" ", text).strip()
    text = _SPACE_AFTER_OPEN.sub(r"\1", text)
    return _SPACE_BEFORE_CLOSE.sub(r"\1", text)


def clean_doc(text: str) -> str | None:
    """Dedent and trim; None when empty."""
    return inspect.cleandoc(text) or None


@dataclass(frozen=True, slots=True)
class Scope:
    """Where we are while walking a tree: the enclosing node, plus the nearest
    enclosing class (what `self`/`this` refer to)."""

    ref: NodeRef
    kind: NodeKind
    class_ref: NodeRef | None


def count_syntax_errors(root: Node) -> int:
    """ERROR and MISSING nodes in the tree. Walks everything (MISSING tokens can
    be anonymous), so only done when tree-sitter flags the tree as broken."""
    if not root.has_error:
        return 0
    errors = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            errors += 1
        stack.extend(node.children)
    return errors


def first_line(node: Node) -> int:
    return node.start_point.row + 1


def last_line(node: Node) -> int:
    # A node ending at column 0 actually ended on the previous line's newline.
    row, column = node.end_point.row, node.end_point.column
    if column == 0 and row > node.start_point.row:
        row -= 1
    return row + 1


class FileBuilder:
    def __init__(self, source: bytes, module_name: str, module_member_sep: str) -> None:
        self.source = source
        self.module_name = module_name
        # Python joins every level with "."; JS/TS joins module->member with ":".
        self._module_member_sep = module_member_sep
        self._nodes: list[ParsedNode] = []
        self._references: list[Reference] = []
        self._seen_references: set[tuple[NodeRef, EdgeKind, str]] = set()
        self._ordinals: dict[str, int] = {}

    @property
    def nodes(self) -> tuple[ParsedNode, ...]:
        return tuple(self._nodes)

    @property
    def references(self) -> tuple[Reference, ...]:
        return tuple(self._references)

    def text(self, node: Node) -> str:
        return self.text_between(node.start_byte, node.end_byte)

    def text_between(self, start_byte: int, end_byte: int) -> str:
        # Slice bytes, not str: tree-sitter offsets are byte offsets, and they
        # diverge from character offsets as soon as the file has non-ASCII text.
        raw = self.source[start_byte:end_byte].decode("utf-8", errors="replace")
        return raw.replace("\r\n", "\n")  # CRLF vs LF must not change content hashes

    def block_text(self, node: Node) -> str:
        """A definition's code, dedented to column 0.

        The node starts at `def`/`class`, so a plain slice would leave the first
        line at column 0 but every other line at its original indentation (a
        method body 8 spaces in). When the node starts its line, include the
        indentation before it and dedent the whole block, so nested code reads
        exactly like top-level code.
        """
        line_start = self.source.rfind(b"\n", 0, node.start_byte) + 1
        prefix = self.source[line_start : node.start_byte]
        if prefix.strip():  # something else precedes it on the line: leave as is
            return self.text(node)
        return textwrap.dedent(self.text_between(line_start, node.end_byte))

    def add_module(self, *, name: str, docstring: str | None) -> Scope:
        code = self.text_between(0, len(self.source))
        line_count = max(1, len(code.splitlines()))
        return self._add(
            NodeKind.MODULE, name, self.module_name, None, None, docstring, 1, line_count, code
        )

    def add_definition(
        self,
        *,
        kind: NodeKind,
        name: str,
        parent: Scope,
        range_node: Node,
        signature: str | None,
        docstring: str | None,
    ) -> Scope:
        qualified_name = self._child_qualified_name(parent, name)
        scope = self._add(
            kind,
            name,
            qualified_name,
            parent,
            signature,
            docstring,
            first_line(range_node),
            last_line(range_node),
            self.block_text(range_node),
        )
        return scope

    def add_reference(self, kind: EdgeKind, scope: Scope, target: str, node: Node) -> None:
        # One edge per (source, kind, target): calling helper() five times in a
        # function is still a single relationship in the graph.
        key = (scope.ref, kind, target)
        if key in self._seen_references:
            return
        self._seen_references.add(key)
        self._references.append(Reference(kind, scope.ref, target, first_line(node)))

    def _child_qualified_name(self, parent: Scope, name: str) -> str:
        if parent.kind is NodeKind.MODULE:
            return f"{parent.ref.qualified_name}{self._module_member_sep}{name}"
        if parent.kind is NodeKind.CLASS:
            return f"{parent.ref.qualified_name}.{name}"
        # Same convention as Python's own __qualname__ for nested functions.
        return f"{parent.ref.qualified_name}.<locals>.{name}"

    def _add(
        self,
        kind: NodeKind,
        name: str,
        qualified_name: str,
        parent: Scope | None,
        signature: str | None,
        docstring: str | None,
        start_line: int,
        end_line: int,
        code: str,
    ) -> Scope:
        ordinal = self._ordinals.get(qualified_name, 0)
        self._ordinals[qualified_name] = ordinal + 1
        node = ParsedNode(
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            ordinal=ordinal,
            parent=parent.ref if parent else None,
            signature=signature,
            docstring=docstring,
            start_line=start_line,
            end_line=end_line,
            code=code,
            content_hash=sha256_hex(code.encode("utf-8")),
        )
        self._nodes.append(node)
        class_ref = node.ref if kind is NodeKind.CLASS else (parent.class_ref if parent else None)
        return Scope(node.ref, kind, class_ref)
