"""Parser output: plain frozen dataclasses.

Nothing here depends on SQLAlchemy or tree-sitter, so the parser can be tested in
isolation and the indexer (step 4) decides how these map onto DB rows.
"""

from dataclasses import dataclass
from pathlib import Path

from core.enums import EdgeKind, Language, NodeKind


@dataclass(frozen=True, slots=True, order=True)
class NodeRef:
    """Identifies a node within a repo.

    qualified_name alone isn't unique: Python can define the same name twice in one
    scope (a @property getter and its setter). `ordinal` counts repeats in source order.
    """

    qualified_name: str
    ordinal: int = 0

    def __str__(self) -> str:
        return self.qualified_name if self.ordinal == 0 else f"{self.qualified_name}#{self.ordinal}"


@dataclass(frozen=True, slots=True)
class ParsedNode:
    kind: NodeKind
    name: str
    qualified_name: str
    ordinal: int
    parent: NodeRef | None  # None only for the module node
    signature: str | None
    docstring: str | None
    start_line: int  # 1-based, inclusive (includes decorators / `export`)
    end_line: int
    code: str
    content_hash: str  # sha256 of `code`; lets the indexer keep unchanged nodes

    @property
    def ref(self) -> NodeRef:
        return NodeRef(self.qualified_name, self.ordinal)


@dataclass(frozen=True, slots=True)
class Reference:
    """A call or base-class mention exactly as written, e.g. "self.save" or
    "utils.helper". The resolver turns it into an edge."""

    kind: EdgeKind  # CALLS or INHERITS
    source: NodeRef
    target: str
    line: int


@dataclass(frozen=True, slots=True)
class Import:
    """Something a file imports; becomes an `imports` edge from its module node.

    `module` is an absolute dotted name for Python (relative imports are already
    resolved) and the raw specifier for JS/TS ("./utils", "react"), which needs
    the repo's file list to resolve.
    """

    module: str
    name: str | None  # None = the module itself; "*" = wildcard
    line: int


@dataclass(frozen=True, slots=True)
class Binding:
    """A local name introduced by an import, used to resolve calls.

    `import a.b.c` binds `a` (module "a"), while its Import records "a.b.c".
    That difference is why bindings and imports are separate types.
    """

    local_name: str  # "*" for Python wildcard imports
    module: str
    name: str | None  # None = binds the module itself


@dataclass(frozen=True, slots=True)
class ReExport:
    """JS/TS names a module exposes on behalf of another (barrel files) or
    under another name (`export default foo`)."""

    exported_name: str  # "*" for `export * from "./x"`
    module: str | None  # specifier; None = this module
    name: str | None  # name in the source module; None = the whole module


@dataclass(frozen=True, slots=True)
class ParsedFile:
    path: str  # POSIX, relative to the repo root
    language: Language
    module_name: str
    content_hash: str
    is_test: bool
    size_bytes: int
    nodes: tuple[ParsedNode, ...]  # nodes[0] is always the module node
    references: tuple[Reference, ...]
    imports: tuple[Import, ...]
    bindings: tuple[Binding, ...]
    reexports: tuple[ReExport, ...]
    syntax_errors: int  # ERROR/MISSING nodes; tree-sitter still parses the rest

    @property
    def module_node(self) -> ParsedNode:
        return self.nodes[0]


@dataclass(frozen=True, slots=True)
class ParsedEdge:
    kind: EdgeKind
    source: NodeRef
    target_name: str  # always set: our best qualified guess, even if unresolved
    target: NodeRef | None  # None = unresolved
    external: bool = False  # points outside the repo (stdlib, third-party)


@dataclass(frozen=True, slots=True)
class SkippedFile:
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class ParseResult:
    root: Path
    files: tuple[ParsedFile, ...]
    edges: tuple[ParsedEdge, ...]
    skipped: tuple[SkippedFile, ...]
