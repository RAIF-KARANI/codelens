"""The parsed source repo, joined with the syntax tree of each function.

Everything comes from core.parser, the same parser the indexer uses, so the
oracle nodes a case names are the qualified names the indexed graph will hold.
No database is needed to generate a dataset.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from core.enums import EdgeKind, Language, NodeKind
from core.parser import ParseResult, parse_repository
from core.parser.languages import detect, get_parser
from core.parser.modules import python_package_dirs
from core.parser.types import NodeRef, ParsedFile, ParsedNode
from evals.syntax import FunctionSyntax, functions_in

_FUNCTIONS = (NodeKind.FUNCTION, NodeKind.METHOD)
_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True, slots=True)
class Target:
    """A function an operator may mutate, with the repo around it."""

    graph: "CodeGraph"
    file: ParsedFile
    node: ParsedNode
    fn: FunctionSyntax

    @property
    def content(self) -> bytes:
        return self.graph.content(self.file.path)


class CodeGraph:
    def __init__(self, result: ParseResult) -> None:
        self.result = result
        self.root = result.root
        self.nodes: dict[NodeRef, tuple[ParsedFile, ParsedNode]] = {
            n.ref: (f, n) for f in result.files for n in f.nodes
        }
        self._callers: dict[NodeRef, set[NodeRef]] = defaultdict(set)
        self._callees: dict[NodeRef, set[NodeRef]] = defaultdict(set)
        for edge in result.edges:
            if edge.kind is not EdgeKind.CALLS or edge.target is None:
                continue
            if edge.source != edge.target:  # recursion says nothing about callers
                self._callers[edge.target].add(edge.source)
                self._callees[edge.source].add(edge.target)
        self._content: dict[str, bytes] = {}
        self._functions: dict[str, dict[NodeRef, FunctionSyntax]] = {}

    @classmethod
    def from_path(cls, root: Path) -> "CodeGraph":
        return cls(parse_repository(root))

    @cached_property
    def package_dirs(self) -> frozenset[str]:
        """Needed to re-parse a mutated file under the same module name."""
        return python_package_dirs(f.path for f in self.result.files)

    def file(self, path: str) -> ParsedFile:
        return self._files_by_path[path]

    @cached_property
    def _files_by_path(self) -> dict[str, ParsedFile]:
        return {f.path: f for f in self.result.files}

    @cached_property
    def texts(self) -> dict[str, str]:
        """Every parsed file as text (tests included), for repo-wide searches."""
        return {f.path: self.content(f.path).decode("utf-8", "replace") for f in self.result.files}

    def content(self, path: str) -> bytes:
        if path not in self._content:
            self._content[path] = (self.root / path).read_bytes()
        return self._content[path]

    def function(self, ref: NodeRef) -> FunctionSyntax | None:
        file, node = self.nodes[ref]
        if node.kind not in _FUNCTIONS:
            return None
        if file.path not in self._functions:
            self._functions[file.path] = self._match_functions(file)
        return self._functions[file.path].get(ref)

    def callers(self, ref: NodeRef) -> list[NodeRef]:
        """Functions outside tests that call `ref`: tests are not the code a
        change breaks, and the reviewer weights them down anyway (D65)."""
        return sorted(
            c
            for c in self._callers[ref]
            if not self.nodes[c][0].is_test and self.function(c) is not None
        )

    def callees(self, ref: NodeRef) -> list[NodeRef]:
        return sorted(c for c in self._callees[ref] if self.function(c) is not None)

    def targets(self, include: Sequence[str]) -> list[Target]:
        """Every non-test function under the `include` path prefixes."""
        found = []
        for file in self.result.files:
            if file.is_test or not file.path.startswith(tuple(include)):
                continue
            for node in file.nodes:
                fn = self.function(node.ref) if node.kind in _FUNCTIONS else None
                if fn is not None:
                    found.append(Target(self, file, node, fn))
        return found

    def _match_functions(self, file: ParsedFile) -> dict[NodeRef, FunctionSyntax]:
        """Pair each parsed function node with its syntax-tree function: same
        name, same last line, starting within the node's range (which also
        covers decorators and `export const ... =`)."""
        content = self.content(file.path)
        detected = detect(file.path)
        if content.startswith(_BOM) or detected is None:
            return {}  # offsets would be off by the BOM; rare enough to skip
        tree = get_parser(detected[1]).parse(content)
        candidates = functions_in(tree.root_node, python=file.language is Language.PYTHON)
        matched: dict[NodeRef, FunctionSyntax] = {}
        used: set[int] = set()
        for node in file.nodes:
            if node.kind not in _FUNCTIONS:
                continue
            for i, fn in enumerate(candidates):
                if i in used or fn.name != node.name:
                    continue
                start, end = fn.node.start_point[0] + 1, fn.node.end_point[0] + 1
                if end == node.end_line and node.start_line <= start <= node.end_line:
                    matched[node.ref] = fn
                    used.add(i)
                    break
        return matched
