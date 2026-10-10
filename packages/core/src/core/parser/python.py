"""Python extractor: one tree-sitter-python tree -> nodes, references, imports.

What becomes a node: the module, every `class`, every `def` (methods when
directly inside a class body, otherwise functions, nested ones qualified as
`outer.<locals>.inner`). Lambdas are not nodes; their calls belong to the
enclosing definition.
"""

import re

from tree_sitter import Node

from core.enums import EdgeKind, NodeKind
from core.parser.builder import (
    FileBuilder,
    Scope,
    clean_doc,
    count_syntax_errors,
    normalize_signature,
)
from core.parser.extraction import Extraction
from core.parser.languages import get_parser
from core.parser.types import Binding, Import

_STRING_PREFIX = re.compile(r"^[rRbBuUfF]*")


def extract_python(source: bytes, *, module_name: str, is_package: bool) -> Extraction:
    return _PythonExtractor(source, module_name, is_package).run()


def string_literal_value(text: str) -> str | None:
    """The contents of a Python string literal's source text, e.g. '\"\"\"Doc.\"\"\"' -> 'Doc.'."""
    body = _STRING_PREFIX.sub("", text, count=1)
    for quote in ('"""', "'''", '"', "'"):
        if body.startswith(quote) and body.endswith(quote) and len(body) >= 2 * len(quote):
            return clean_doc(body[len(quote) : -len(quote)])
    return None


class _PythonExtractor:
    def __init__(self, source: bytes, module_name: str, is_package: bool) -> None:
        self._b = FileBuilder(source, module_name, module_member_sep=".")
        self._module_name = module_name
        self._is_package = is_package
        self._imports: list[Import] = []
        self._bindings: list[Binding] = []

    def run(self) -> Extraction:
        # Keep `tree` referenced for the whole walk: Nodes point into memory the
        # Tree owns, so they must not outlive it.
        tree = get_parser("python").parse(self._b.source)
        root = tree.root_node
        module = self._b.add_module(
            name=self._module_name.rsplit(".", 1)[-1], docstring=self._docstring(root)
        )
        # Iterative depth-first walk. Recursion would hit Python's limit on
        # deeply nested code (e.g. long chains of `a + b + c + ...`).
        # Only *named* children: everything we extract is a named node, and
        # skipping punctuation tokens roughly halves the walk.
        stack: list[tuple[Node, Scope]] = [(c, module) for c in reversed(root.named_children)]
        while stack:
            node, scope = stack.pop()
            stack.extend(reversed(self._visit(node, scope)))
        syntax_errors = count_syntax_errors(root)
        del tree
        return Extraction(
            nodes=self._b.nodes,
            references=self._b.references,
            imports=tuple(self._imports),
            bindings=tuple(self._bindings),
            reexports=(),
            syntax_errors=syntax_errors,
        )

    def _visit(self, node: Node, scope: Scope) -> list[tuple[Node, Scope]]:
        """Handle one node; return the children to walk next, with their scope."""
        kind = node.type
        if kind == "decorated_definition":
            definition = node.child_by_field_name("definition")
            if definition is None:
                return [(c, scope) for c in node.named_children]
            inner = self._define(definition, range_node=node, scope=scope)
            # Decorators run at definition time; we attribute their calls
            # (e.g. @app.route("/x")) to the decorated node, which is what a
            # reviewer cares about.
            decorators = [(c, inner) for c in node.named_children if c != definition]
            return decorators + [(c, inner) for c in definition.named_children]

        if kind in ("function_definition", "class_definition"):
            inner = self._define(node, range_node=node, scope=scope)
            return [(c, inner) for c in node.named_children]

        if kind == "import_statement":
            self._record_import(node)
            return []
        if kind == "import_from_statement":
            self._record_import_from(node)
            return []
        if kind == "call":
            self._record_call(node, scope)
        return [(c, scope) for c in node.named_children]

    # --------------------------------------------------------------- definitions

    def _define(self, node: Node, *, range_node: Node, scope: Scope) -> Scope:
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        name = self._b.text(name_node) if name_node else "<anonymous>"
        if node.type == "class_definition":
            kind = NodeKind.CLASS
        elif scope.kind is NodeKind.CLASS:
            kind = NodeKind.METHOD
        else:
            kind = NodeKind.FUNCTION

        # Signature = the header up to the body: `async def f(a: int) -> str`.
        header_end = body.start_byte if body else node.end_byte
        header = self._b.text_between(node.start_byte, header_end)
        signature = normalize_signature(header).removesuffix(":").rstrip()

        inner = self._b.add_definition(
            kind=kind,
            name=name,
            parent=scope,
            range_node=range_node,
            signature=signature,
            docstring=self._docstring(body),
        )
        if kind is NodeKind.CLASS:
            self._record_bases(node, inner)
        return inner

    def _docstring(self, block: Node | None) -> str | None:
        """A docstring is a plain string literal as the first statement."""
        if block is None or not block.named_children:
            return None
        first = block.named_children[0]
        if first.type != "expression_statement" or not first.named_children:
            return None
        literal = first.named_children[0]
        return string_literal_value(self._b.text(literal)) if literal.type == "string" else None

    def _record_bases(self, class_node: Node, scope: Scope) -> None:
        superclasses = class_node.child_by_field_name("superclasses")
        if superclasses is None:
            return
        for arg in superclasses.named_children:
            if arg.type == "keyword_argument":  # metaclass=..., total=False
                continue
            if arg.type == "subscript":  # Generic[T] -> Generic
                arg = arg.child_by_field_name("value") or arg
            target = self._dotted(arg)
            if target:
                self._b.add_reference(EdgeKind.INHERITS, scope, target, arg)

    # --------------------------------------------------------------- calls

    def _record_call(self, node: Node, scope: Scope) -> None:
        function = node.child_by_field_name("function")
        target = self._dotted(function) if function else None
        # Non-name callees (`handlers[k]()`, `make()()`) can't be resolved
        # statically; the inner `make()` call is still recorded on its own.
        # The bare `super()` inside `super().save()` adds nothing: the outer
        # call is recorded as "super().save".
        if target and target != "super":
            self._b.add_reference(EdgeKind.CALLS, scope, target, node)

    def _dotted(self, node: Node) -> str | None:
        """`a.b.c` for name/attribute chains; `super().m` for super calls; else None."""
        if node.type == "identifier":
            return self._b.text(node)
        if node.type == "attribute":
            obj = node.child_by_field_name("object")
            attr = node.child_by_field_name("attribute")
            base = self._dotted(obj) if obj else None
            return f"{base}.{self._b.text(attr)}" if base and attr else None
        if node.type == "call":
            function = node.child_by_field_name("function")
            if function is not None and self._b.text(function) == "super":
                return "super()"
        return None

    # --------------------------------------------------------------- imports

    def _record_import(self, node: Node) -> None:
        line = node.start_point.row + 1
        for child in node.children_by_field_name("name"):
            if child.type == "aliased_import":
                module = self._b.text(child.child_by_field_name("name"))
                alias = self._b.text(child.child_by_field_name("alias"))
                self._imports.append(Import(module, None, line))
                self._bindings.append(Binding(alias, module, None))
            else:
                # `import a.b.c` imports a.b.c but only binds the name `a`.
                module = self._b.text(child)
                root = module.split(".", 1)[0]
                self._imports.append(Import(module, None, line))
                self._bindings.append(Binding(root, root, None))

    def _record_import_from(self, node: Node) -> None:
        line = node.start_point.row + 1
        module_node = node.child_by_field_name("module_name")
        module = self._absolute_module(module_node) if module_node else ""
        if any(c.type == "wildcard_import" for c in node.children):
            self._imports.append(Import(module, "*", line))
            self._bindings.append(Binding("*", module, None))
            return
        for child in node.children_by_field_name("name"):
            if child.type == "aliased_import":
                name = self._b.text(child.child_by_field_name("name"))
                local = self._b.text(child.child_by_field_name("alias"))
            else:
                name = local = self._b.text(child)
            self._imports.append(Import(module, name, line))
            self._bindings.append(Binding(local, module, name))

    def _absolute_module(self, node: Node) -> str:
        """Resolve `from ..pkg import x` against this module's package."""
        if node.type != "relative_import":
            return self._b.text(node)
        prefix = next((c for c in node.children if c.type == "import_prefix"), None)
        dots = self._b.text(prefix).count(".") if prefix else 0
        named = next((c for c in node.children if c.type == "dotted_name"), None)

        # A package's own __init__ is its package; a plain module's package is its parent.
        package = self._module_name.split(".")
        if not self._is_package:
            package = package[:-1]
        levels_up = dots - 1
        base = package[: len(package) - levels_up] if levels_up <= len(package) else []
        if named is not None:
            base = [*base, self._b.text(named)]
        return ".".join(base)
