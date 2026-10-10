"""Repo-wide resolution: turn references and imports into graph edges.

Static resolution in dynamic languages can't be complete, so the aim is to be
right in the common cases and honest about the rest. A name like `helper` in
`pkg.mod.f` is looked up in this order:

  1. definitions nested in the enclosing function(s)   pkg.mod.f.<locals>.helper
  2. module-level definitions                          pkg.mod.helper
  3. import bindings                                   from pkg.utils import helper
  4. Python wildcard imports                           from pkg.utils import *
  5. builtins / JS globals (`len`, `console`): no edge at all (pure noise)
  6. otherwise unresolved, keeping the text as target_name

`self.x` / `this.x` look up `x` on the enclosing class, then its base classes
(depth-first, left to right: an approximation of Python's MRO). `super()...`
does the same but skips the class itself. Not handled: types of arbitrary
variables (`obj.save()`), parameters or locals shadowing global names,
`getattr`, decorators that swap functions, TS path aliases.
"""

import builtins
import posixpath
from collections.abc import Sequence
from dataclasses import dataclass

from core.enums import EdgeKind, Language, NodeKind
from core.parser.languages import JS_TS_EXTENSIONS
from core.parser.types import Binding, NodeRef, ParsedEdge, ParsedFile, ParsedNode, Reference

PYTHON_BUILTINS = frozenset(dir(builtins))
JS_GLOBALS = frozenset(
    {
        "console", "Math", "JSON", "Object", "Array", "String", "Number", "Boolean",
        "Symbol", "BigInt", "Promise", "Date", "RegExp", "Map", "Set", "WeakMap",
        "WeakSet", "Reflect", "Proxy", "Intl", "Error", "TypeError", "RangeError",
        "SyntaxError", "ReferenceError", "parseInt", "parseFloat", "isNaN", "isFinite",
        "setTimeout", "clearTimeout", "setInterval", "clearInterval", "queueMicrotask",
        "structuredClone", "encodeURIComponent", "decodeURIComponent", "encodeURI",
        "decodeURI", "fetch", "URL", "URLSearchParams", "globalThis", "window",
        "document", "navigator", "localStorage", "sessionStorage", "alert",
        "process", "Buffer", "require", "module", "exports", "__dirname", "__filename",
    }
)  # fmt: skip

_MAX_EXPORT_DEPTH = 8  # re-export / __init__ chains; guards against cycles


@dataclass(frozen=True, slots=True)
class _Target:
    qualified_name: str
    ref: NodeRef | None = None
    is_module: bool = False
    external: bool = False


class _Builtin:
    """Marker: the name is a builtin, so no edge is produced."""


_BUILTIN = _Builtin()


def resolve_edges(files: Sequence[ParsedFile]) -> list[ParsedEdge]:
    return _Resolver(files).resolve()


class _Resolver:
    def __init__(self, files: Sequence[ParsedFile]) -> None:
        self._files = files
        self._nodes: dict[NodeRef, ParsedNode] = {}
        # First definition wins for duplicate names (e.g. property getter vs
        # setter); files arrive sorted by path, so this is deterministic.
        self._by_name: dict[str, NodeRef] = {}
        self._module_files: dict[str, ParsedFile] = {}
        self._path_files: dict[str, ParsedFile] = {}
        self._python_prefixes: set[str] = set()  # every module name + its parent packages
        self._bindings: dict[str, dict[str, Binding]] = {}
        self._star_imports: dict[str, list[str]] = {}
        self._bases: dict[str, list[_Target]] = {}

        for file in files:
            self._module_files[file.module_name] = file
            self._path_files[file.path] = file
            for node in file.nodes:
                self._nodes[node.ref] = node
                self._by_name.setdefault(node.qualified_name, node.ref)
            if file.language is Language.PYTHON:
                parts = file.module_name.split(".")
                self._python_prefixes.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
            table: dict[str, Binding] = {}
            for binding in file.bindings:
                if binding.local_name == "*":
                    self._star_imports.setdefault(file.path, []).append(binding.module)
                else:
                    table[binding.local_name] = binding  # a later import shadows an earlier one
            self._bindings[file.path] = table

    def resolve(self) -> list[ParsedEdge]:
        # Base classes first: method lookup through inheritance needs them.
        for file in self._files:
            for ref in file.references:
                if ref.kind is EdgeKind.INHERITS:
                    target = self._resolve_reference(file, ref)
                    if isinstance(target, _Target):
                        self._bases.setdefault(ref.source.qualified_name, []).append(target)

        edges: list[ParsedEdge] = []
        for file in self._files:
            for node in file.nodes:
                if node.parent is not None:
                    edges.append(
                        ParsedEdge(EdgeKind.CONTAINS, node.parent, node.qualified_name, node.ref)
                    )
            module_ref = file.module_node.ref
            for imp in file.imports:
                target = self._resolve_import(file, imp.module, imp.name)
                edges.append(self._edge(EdgeKind.IMPORTS, module_ref, target))
            for ref in file.references:
                resolved = self._resolve_reference(file, ref)
                if isinstance(resolved, _Target):
                    edges.append(self._edge(ref.kind, ref.source, resolved))
        return _dedupe(edges)

    # --------------------------------------------------------------- references

    def _resolve_reference(self, file: ParsedFile, ref: Reference) -> _Target | _Builtin:
        source = self._nodes[ref.source]
        head, *rest = ref.target.split(".")
        is_python = file.language is Language.PYTHON

        if (is_python and head in ("self", "cls")) or (not is_python and head == "this"):
            return self._on_class(self._enclosing_class(source), rest, ref.target, skip_self=False)
        if (is_python and head == "super()") or (not is_python and head == "super"):
            cls = self._enclosing_class(source)
            if not rest:  # JS `super(...)` calls the base constructor
                bases = self._bases.get(cls.qualified_name, []) if cls else []
                return bases[0] if bases else _Target(ref.target)
            return self._on_class(cls, rest, ref.target, skip_self=True)

        base = self._lookup_name(file, source, head)
        if isinstance(base, _Builtin):
            return base
        if base is None:
            return _Target(ref.target)  # an unknown name: variable, parameter, dynamic
        return self._descend(file, base, rest)

    def _lookup_name(
        self, file: ParsedFile, source: ParsedNode, name: str
    ) -> _Target | _Builtin | None:
        node: ParsedNode | None = source
        while node is not None:  # 1. nested definitions, innermost function first
            if node.kind in (NodeKind.FUNCTION, NodeKind.METHOD):
                found = self._target(f"{node.qualified_name}.<locals>.{name}")
                if found.ref:
                    return found
            node = self._nodes[node.parent] if node.parent else None

        found = self._target(self._member_name(file, file.module_name, name))  # 2.
        if found.ref:
            return found

        binding = self._bindings[file.path].get(name)  # 3.
        if binding is not None:
            return self._binding_target(file, binding, depth=0)

        for module in self._star_imports.get(file.path, []):  # 4.
            found = self._target(f"{module}.{name}")
            if found.ref:
                return found

        builtin_names = PYTHON_BUILTINS if file.language is Language.PYTHON else JS_GLOBALS
        return _BUILTIN if name in builtin_names else None  # 5. / 6.

    def _descend(self, file: ParsedFile, base: _Target, rest: list[str]) -> _Target:
        """Follow `.a.b` from a resolved head: module members, class members, ..."""
        current = base
        for name in rest:
            current = self._member_of(file, current, name)
        return current

    def _member_of(self, file: ParsedFile, target: _Target, name: str) -> _Target:
        is_js = file.language is not Language.PYTHON
        if target.external:
            sep = ":" if is_js and target.is_module else "."
            return _Target(f"{target.qualified_name}{sep}{name}", external=True)
        if target.is_module:
            exported = self._export(file.language, target.qualified_name, name, depth=0)
            if exported is not None:
                return exported
            return _Target(self._member_name(file, target.qualified_name, name))
        found = self._target(f"{target.qualified_name}.{name}")
        if found.ref is None and target.ref and self._nodes[target.ref].kind is NodeKind.CLASS:
            inherited = self._find_member(target.qualified_name, name, skip_self=True)
            if inherited is not None:
                return inherited
        return found

    def _on_class(
        self, cls: ParsedNode | None, rest: list[str], raw: str, *, skip_self: bool
    ) -> _Target:
        if cls is None or not rest:
            return _Target(raw)
        member = self._find_member(cls.qualified_name, rest[0], skip_self=skip_self)
        if member is None:
            # Unresolved: name it after the class we'd expect it on (or, for
            # super(), the first base, which may be external like TestCase).
            bases = self._bases.get(cls.qualified_name, [])
            if skip_self and bases:
                return _Target(
                    ".".join([bases[0].qualified_name, *rest]), external=bases[0].external
                )
            return _Target(".".join([cls.qualified_name, *rest]))
        current = member
        for name in rest[1:]:  # self.method.attr: rarely a node, but keep the name
            current = self._target(f"{current.qualified_name}.{name}")
        return current

    def _find_member(
        self, class_name: str, member: str, *, skip_self: bool, seen: frozenset[str] = frozenset()
    ) -> _Target | None:
        if class_name in seen:  # inheritance cycles exist in broken code
            return None
        if not skip_self:
            found = self._target(f"{class_name}.{member}")
            if found.ref:
                return found
        for base in self._bases.get(class_name, []):
            if base.ref is not None:
                found_in_base = self._find_member(
                    base.qualified_name, member, skip_self=False, seen=seen | {class_name}
                )
                if found_in_base is not None:
                    return found_in_base
        return None

    def _enclosing_class(self, node: ParsedNode) -> ParsedNode | None:
        current: ParsedNode | None = node
        while current is not None:
            if current.kind is NodeKind.CLASS:
                return current
            current = self._nodes[current.parent] if current.parent else None
        return None

    # --------------------------------------------------------------- imports

    def _binding_target(self, file: ParsedFile, binding: Binding, *, depth: int) -> _Target:
        if file.language is Language.PYTHON:
            if binding.name is None:
                return self._python_module(binding.module)
            exported = self._export(Language.PYTHON, binding.module, binding.name, depth=depth)
            if exported is not None:
                return exported
            return _Target(
                _join(binding.module, binding.name),
                external=not self._python_in_repo(binding.module),
            )

        module, found, external = self._resolve_specifier(file.path, binding.module)
        if external or not found:
            # A default import of a package (`import React from "react"`) is
            # named after the package, so `React.useState` reads `react:useState`.
            whole_module = binding.name in (None, "default")
            name = module if whole_module else f"{module}:{binding.name}"
            return _Target(name, is_module=whole_module, external=external)
        if binding.name is None:
            return self._target(module)
        exported = self._export(file.language, module, binding.name, depth=depth)
        return exported if exported is not None else _Target(f"{module}:{binding.name}")

    def _resolve_import(self, file: ParsedFile, module: str, name: str | None) -> _Target:
        """Target of an `imports` edge: the imported symbol if it is a node,
        otherwise the module (e.g. `from config import SETTINGS`, a variable)."""
        if file.language is Language.PYTHON:
            module_target = self._python_module(module)
            if name in (None, "*"):
                return module_target
            exported = self._export(Language.PYTHON, module, name, depth=0)
            if exported is not None and exported.ref is not None:
                return exported
            if module_target.ref is not None:
                return module_target
            return _Target(_join(module, name), external=module_target.external)

        resolved_module, found, external = self._resolve_specifier(file.path, module)
        if external or not found:
            target_name = resolved_module if name is None else f"{resolved_module}:{name}"
            return _Target(target_name, external=external)
        if name is not None:
            exported = self._export(file.language, resolved_module, name, depth=0)
            if exported is not None and exported.ref is not None:
                return exported
        return self._target(resolved_module)

    def _export(self, language: Language, module: str, name: str, *, depth: int) -> _Target | None:
        """What `name` means when imported from `module`, following Python
        `__init__` re-imports and JS/TS re-exports (barrel files)."""
        if depth > _MAX_EXPORT_DEPTH:
            return None
        sep = "." if language is Language.PYTHON else ":"
        direct = self._target(f"{module}{sep}{name}")
        if direct.ref is not None:
            return direct
        file = self._module_files.get(module)
        if file is None:
            return None

        if language is Language.PYTHON:
            # `from pkg import helper` where pkg/__init__.py did `from .utils import helper`.
            binding = self._bindings[file.path].get(name)
            if binding is not None:
                return self._binding_target(file, binding, depth=depth + 1)
            return None

        for rx in file.reexports:
            if rx.exported_name != name:
                continue
            if rx.module is None:  # export { foo as bar } / export default foo
                return self._export(language, module, rx.name or name, depth=depth + 1)
            target_module, found, external = self._resolve_specifier(file.path, rx.module)
            if external or not found:
                return _Target(
                    f"{target_module}:{rx.name}" if rx.name else target_module, external=external
                )
            if rx.name is None:  # export * as ns from "./m"
                return self._target(target_module)
            return self._export(language, target_module, rx.name, depth=depth + 1)
        for rx in file.reexports:  # export * from "./m"
            if rx.exported_name == "*" and rx.module is not None:
                target_module, found, _ = self._resolve_specifier(file.path, rx.module)
                if found:
                    exported = self._export(language, target_module, name, depth=depth + 1)
                    if exported is not None and exported.ref is not None:
                        return exported
        return None

    def _resolve_specifier(self, from_path: str, spec: str) -> tuple[str, bool, bool]:
        """JS/TS module specifier -> (module name, found in repo, external).

        Relative specifiers are tried with each extension and as a directory
        index, like Node/TS resolution. Bare specifiers ("react") are external.
        """
        if not spec.startswith("."):
            return spec, False, True
        base = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec))
        candidates = [base]
        if base.endswith((".js", ".jsx", ".mjs", ".cjs")):
            # ESM TypeScript writes "./x.js" for a file that is really x.ts.
            stem = base.rsplit(".", 1)[0]
            candidates += [f"{stem}.ts", f"{stem}.tsx"]
        candidates += [f"{base}{ext}" for ext in JS_TS_EXTENSIONS]
        candidates += [f"{base}/index{ext}" for ext in JS_TS_EXTENSIONS]
        for candidate in candidates:
            file = self._path_files.get(candidate)
            if file is not None:
                return file.module_name, True, False
        return base, False, False  # a relative path to something we didn't parse

    def _python_module(self, module: str) -> _Target:
        found = self._target(module)
        if found.ref is not None:
            return found
        return _Target(module, is_module=True, external=not self._python_in_repo(module))

    def _python_in_repo(self, module: str) -> bool:
        return module == "" or module in self._python_prefixes

    # --------------------------------------------------------------- helpers

    def _target(self, qualified_name: str) -> _Target:
        ref = self._by_name.get(qualified_name)
        if ref is None:
            return _Target(qualified_name)
        return _Target(qualified_name, ref, is_module=self._nodes[ref].kind is NodeKind.MODULE)

    @staticmethod
    def _member_name(file: ParsedFile, module: str, name: str) -> str:
        sep = "." if file.language is Language.PYTHON else ":"
        return f"{module}{sep}{name}"

    @staticmethod
    def _edge(kind: EdgeKind, source: NodeRef, target: _Target) -> ParsedEdge:
        return ParsedEdge(kind, source, target.qualified_name, target.ref, target.external)


def _join(module: str, name: str) -> str:
    return f"{module}.{name}" if module else name


def _dedupe(edges: list[ParsedEdge]) -> list[ParsedEdge]:
    # The resolved target is part of the key: a class *contains* both its
    # @property getter and setter, which share a qualified name and differ only
    # by ordinal. Keying on target_name alone silently dropped the setter.
    seen: set[tuple[NodeRef, EdgeKind, str, NodeRef | None]] = set()
    unique: list[ParsedEdge] = []
    for edge in edges:
        key = (edge.source, edge.kind, edge.target_name, edge.target)
        if key not in seen:
            seen.add(key)
            unique.append(edge)
    return unique
