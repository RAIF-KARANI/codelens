"""JavaScript / TypeScript extractor (one implementation, three grammars).

What becomes a node: the module; `function` declarations; classes (declarations
and `const X = class {}`); class methods and arrow-function class fields;
`const f = () => {}` / `const f = function () {}`; anonymous default exports
(named "default"). Not nodes: object-literal methods, callbacks, TS interfaces,
type aliases, enums and overload signatures. Their calls belong to the
enclosing node.
"""

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
from core.parser.types import Binding, Import, ReExport

_FUNCTION_DECLARATIONS = {"function_declaration", "generator_function_declaration"}
_CLASS_DECLARATIONS = {"class_declaration", "abstract_class_declaration"}
# Expression forms that define a function/class when bound to a name.
_FUNCTION_VALUES = {"arrow_function", "function_expression", "generator_function"}
_CLASS_VALUES = {"class"}
_FIELD_DEFINITIONS = {"field_definition", "public_field_definition"}  # JS / TS


def extract_javascript(source: bytes, *, module_name: str, grammar: str) -> Extraction:
    return _JsExtractor(source, module_name, grammar).run()


class _JsExtractor:
    def __init__(self, source: bytes, module_name: str, grammar: str) -> None:
        self._b = FileBuilder(source, module_name, module_member_sep=":")
        self._module_name = module_name
        self._grammar = grammar
        self._imports: list[Import] = []
        self._bindings: list[Binding] = []
        self._reexports: list[ReExport] = []

    def run(self) -> Extraction:
        # Keep `tree` referenced for the whole walk; see the note in python.py.
        tree = get_parser(self._grammar).parse(self._b.source)
        root = tree.root_node
        module = self._b.add_module(name=self._module_name.rsplit("/", 1)[-1], docstring=None)
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
            reexports=tuple(self._reexports),
            syntax_errors=syntax_errors,
        )

    def _visit(self, node: Node, scope: Scope) -> list[tuple[Node, Scope]]:
        kind = node.type
        if kind in _FUNCTION_DECLARATIONS or kind in _CLASS_DECLARATIONS:
            name_node = node.child_by_field_name("name")
            name = self._b.text(name_node) if name_node else "default"
            node_kind = NodeKind.CLASS if kind in _CLASS_DECLARATIONS else NodeKind.FUNCTION
            inner = self._define(node, name, node_kind, self._declaration_range(node), scope)
            return [(c, inner) for c in node.named_children]

        # Only methods in a class body; `{ m() {} }` object methods are not nodes.
        if kind == "method_definition" and node.parent and node.parent.type == "class_body":
            name = self._b.text(node.child_by_field_name("name"))
            inner = self._define(node, name, NodeKind.METHOD, node, scope)
            return [(c, inner) for c in node.named_children]

        if kind in _FIELD_DEFINITIONS:
            value = node.child_by_field_name("value")
            name_node = node.child_by_field_name("property") or node.child_by_field_name("name")
            if value is not None and name_node is not None and value.type in _FUNCTION_VALUES:
                # `handle = (e) => {...}` in a class body behaves like a method.
                name = self._b.text(name_node)
                inner = self._define(value, name, NodeKind.METHOD, node, scope)
                return [(c, inner) for c in value.named_children]

        if kind == "variable_declarator":
            return self._visit_declarator(node, scope)

        if kind == "export_statement":
            return self._visit_export(node, scope)

        if kind == "import_statement":
            self._record_import(node)
            return []
        if kind == "call_expression":
            self._record_call(node, scope)
        elif kind == "new_expression":
            constructor = node.child_by_field_name("constructor")
            target = self._dotted(constructor) if constructor else None
            if target:
                self._b.add_reference(EdgeKind.CALLS, scope, target, node)
        return [(c, scope) for c in node.named_children]

    # --------------------------------------------------------------- definitions

    def _visit_declarator(self, node: Node, scope: Scope) -> list[tuple[Node, Scope]]:
        name_node = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if value is None or name_node is None:
            return [(c, scope) for c in node.named_children]
        if self._is_require(value):
            self._record_require(value, name_node)
            return []
        if name_node.type == "identifier" and (
            value.type in _FUNCTION_VALUES or value.type in _CLASS_VALUES
        ):
            node_kind = NodeKind.CLASS if value.type in _CLASS_VALUES else NodeKind.FUNCTION
            name = self._b.text(name_node)
            inner = self._define(value, name, node_kind, self._declarator_range(node), scope)
            return [(c, inner) for c in value.named_children]
        return [(c, scope) for c in node.named_children]

    def _define(
        self, node: Node, name: str, kind: NodeKind, range_node: Node, scope: Scope
    ) -> Scope:
        if kind is NodeKind.FUNCTION and scope.kind is NodeKind.CLASS:
            kind = NodeKind.METHOD
        body = node.child_by_field_name("body")
        header_end = body.start_byte if body else node.end_byte
        signature = normalize_signature(self._b.text_between(range_node.start_byte, header_end))
        inner = self._b.add_definition(
            kind=kind,
            name=name,
            parent=scope,
            range_node=range_node,
            signature=signature,
            docstring=self._jsdoc(range_node),
        )
        if kind is NodeKind.CLASS:
            self._record_bases(node, inner)
        return inner

    def _declaration_range(self, node: Node) -> Node:
        # Include a wrapping `export` / `export default` in the node's code.
        parent = node.parent
        return parent if parent is not None and parent.type == "export_statement" else node

    def _declarator_range(self, declarator: Node) -> Node:
        """`const f = () => {}` spans the whole declaration (and `export`),
        unless it declares several variables at once."""
        declaration = declarator.parent
        if declaration is None:
            return declarator
        declarators = [c for c in declaration.named_children if c.type == "variable_declarator"]
        if len(declarators) != 1:
            return declarator
        return self._declaration_range(declaration)

    def _jsdoc(self, range_node: Node) -> str | None:
        """A `/** ... */` comment immediately above the declaration."""
        previous = range_node.prev_named_sibling
        if previous is None or previous.type != "comment":
            return None
        if previous.end_point.row < range_node.start_point.row - 1:
            return None  # separated by a blank line: not attached
        text = self._b.text(previous)
        if not text.startswith("/**"):
            return None
        lines = text.removeprefix("/**").removesuffix("*/").splitlines()
        return clean_doc("\n".join(line.strip().removeprefix("*").strip() for line in lines))

    def _record_bases(self, class_node: Node, scope: Scope) -> None:
        heritage = next((c for c in class_node.children if c.type == "class_heritage"), None)
        if heritage is None:
            return
        extends = next((c for c in heritage.children if c.type == "extends_clause"), None)
        if extends is not None:  # TypeScript: extends_clause(value: ...)
            values = extends.children_by_field_name("value")
        else:  # JavaScript: `extends <expression>` directly under class_heritage
            values = heritage.named_children[:1]
        for value in values:
            target = self._dotted(value)
            if target:
                self._b.add_reference(EdgeKind.INHERITS, scope, target, value)

    # --------------------------------------------------------------- calls

    def _record_call(self, node: Node, scope: Scope) -> None:
        function = node.child_by_field_name("function")
        if function is None or function.type == "import":  # dynamic import()
            return
        if self._is_require(node):
            self._record_require(node, None)
            return
        target = self._dotted(function)
        if target:
            self._b.add_reference(EdgeKind.CALLS, scope, target, node)

    def _dotted(self, node: Node) -> str | None:
        if node.type in ("identifier", "this", "super", "type_identifier"):
            return self._b.text(node)
        if node.type == "member_expression":
            obj = node.child_by_field_name("object")
            prop = node.child_by_field_name("property")
            base = self._dotted(obj) if obj else None
            return f"{base}.{self._b.text(prop)}" if base and prop else None
        return None

    # --------------------------------------------------------------- imports / exports

    def _record_import(self, node: Node) -> None:
        line = node.start_point.row + 1
        source = node.child_by_field_name("source")
        if source is None:
            return
        spec = self._string_value(source)
        clause = next((c for c in node.children if c.type == "import_clause"), None)
        if clause is None:  # side-effect import: `import "./polyfills"`
            self._imports.append(Import(spec, None, line))
            return
        for part in clause.named_children:
            if part.type == "identifier":  # default import
                self._add_import(spec, "default", self._b.text(part), line)
            elif part.type == "namespace_import":  # * as ns
                local = next(c for c in part.named_children if c.type == "identifier")
                self._add_import(spec, None, self._b.text(local), line)
            elif part.type == "named_imports":
                for spec_node in part.named_children:
                    if spec_node.type != "import_specifier":
                        continue
                    name = self._b.text(spec_node.child_by_field_name("name"))
                    alias = spec_node.child_by_field_name("alias")
                    local = self._b.text(alias) if alias else name
                    self._add_import(spec, name, local, line)

    def _add_import(self, spec: str, name: str | None, local: str, line: int) -> None:
        self._imports.append(Import(spec, name, line))
        self._bindings.append(Binding(local, spec, name))

    def _is_require(self, node: Node) -> bool:
        if node.type != "call_expression":
            return False
        function = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        return (
            function is not None
            and function.type == "identifier"
            and self._b.text(function) == "require"
            and args is not None
            and bool(args.named_children)
            and args.named_children[0].type == "string"
        )

    def _record_require(self, call: Node, target: Node | None) -> None:
        """`const x = require("m")` and `const { a, b: c } = require("m")`."""
        args = call.child_by_field_name("arguments")
        spec = self._string_value(args.named_children[0])
        line = call.start_point.row + 1
        if target is None:
            self._imports.append(Import(spec, None, line))
        elif target.type == "identifier":
            self._add_import(spec, None, self._b.text(target), line)
        elif target.type == "object_pattern":
            for prop in target.named_children:
                if prop.type == "shorthand_property_identifier_pattern":
                    name = self._b.text(prop)
                    self._add_import(spec, name, name, line)
                elif prop.type == "pair_pattern":
                    key = prop.child_by_field_name("key")
                    value = prop.child_by_field_name("value")
                    if key is not None and value is not None and value.type == "identifier":
                        self._add_import(spec, self._b.text(key), self._b.text(value), line)
        else:
            self._imports.append(Import(spec, None, line))

    def _visit_export(self, node: Node, scope: Scope) -> list[tuple[Node, Scope]]:
        line = node.start_point.row + 1
        source = node.child_by_field_name("source")
        spec = self._string_value(source) if source is not None else None
        is_default = any(c.type == "default" for c in node.children)
        declaration = node.child_by_field_name("declaration")
        value = node.child_by_field_name("value")

        clause = next((c for c in node.children if c.type == "export_clause"), None)
        namespace = next((c for c in node.children if c.type == "namespace_export"), None)
        if clause is not None:  # export { a, b as c } [from "./m"]
            for spec_node in clause.named_children:
                if spec_node.type != "export_specifier":
                    continue
                name = self._b.text(spec_node.child_by_field_name("name"))
                alias = spec_node.child_by_field_name("alias")
                exported = self._b.text(alias) if alias else name
                self._reexports.append(ReExport(exported, spec, name))
                if spec is not None:
                    self._imports.append(Import(spec, name, line))
        elif spec is not None:  # export * from "./m"  /  export * as ns from "./m"
            if namespace is not None:
                local = next(c for c in namespace.named_children if c.type == "identifier")
                self._reexports.append(ReExport(self._b.text(local), spec, None))
            else:
                self._reexports.append(ReExport("*", spec, None))
            self._imports.append(Import(spec, None, line))

        if is_default:
            if declaration is not None:  # export default class Svc / function foo
                name_node = declaration.child_by_field_name("name")
                if name_node is not None:
                    self._reexports.append(ReExport("default", None, self._b.text(name_node)))
            elif value is not None and value.type == "identifier":  # export default foo
                self._reexports.append(ReExport("default", None, self._b.text(value)))
            elif value is not None and (
                value.type in _FUNCTION_VALUES or value.type in _CLASS_VALUES
            ):
                # Anonymous `export default function () {}` becomes a node named "default".
                node_kind = NodeKind.CLASS if value.type in _CLASS_VALUES else NodeKind.FUNCTION
                name_node = value.child_by_field_name("name")
                name = self._b.text(name_node) if name_node else "default"
                if name != "default":
                    self._reexports.append(ReExport("default", None, name))
                inner = self._define(value, name, node_kind, node, scope)
                return [(c, inner) for c in value.named_children]
        return [(c, scope) for c in node.named_children]

    def _string_value(self, node: Node) -> str:
        fragment = next((c for c in node.named_children if c.type == "string_fragment"), None)
        return self._b.text(fragment) if fragment is not None else self._b.text(node).strip("'\"`")
