"""Parse a whole repository: discover files, extract each one, resolve edges."""

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from core.enums import EdgeKind, Language, NodeKind
from core.parser.builder import sha256_hex
from core.parser.extraction import Extraction
from core.parser.javascript import extract_javascript
from core.parser.modules import js_module_name, python_module_name, python_package_dirs
from core.parser.python import extract_python
from core.parser.resolver import resolve_edges
from core.parser.types import ParsedFile, ParseResult, SkippedFile
from core.parser.walker import SourceFile, discover_sources

logger = logging.getLogger(__name__)

_UTF8_BOM = b"\xef\xbb\xbf"


def parse_repository(root: Path) -> ParseResult:
    root = root.resolve()
    sources, skipped = discover_sources(root)
    package_dirs = python_package_dirs(s.path for s in sources)

    files: list[ParsedFile] = []
    for source in sources:
        try:
            files.append(parse_source(source, package_dirs))
        except Exception:
            # One pathological file must not abort indexing a whole repo.
            logger.exception("failed to parse %s", source.path)
            skipped.append(SkippedFile(source.path, "parse_error"))

    edges = resolve_edges(files)
    return ParseResult(
        root, tuple(files), tuple(edges), tuple(sorted(skipped, key=lambda s: s.path))
    )


def parse_source(source: SourceFile, package_dirs: frozenset[str]) -> ParsedFile:
    # Hash the bytes as stored (the file's identity), but parse without a BOM,
    # which tree-sitter would otherwise report as a syntax error.
    content = source.content.removeprefix(_UTF8_BOM)
    extraction: Extraction
    if source.language is Language.PYTHON:
        module_name, is_package = python_module_name(source.path, package_dirs)
        extraction = extract_python(content, module_name=module_name, is_package=is_package)
    else:
        module_name = js_module_name(source.path)
        extraction = extract_javascript(content, module_name=module_name, grammar=source.grammar)

    return ParsedFile(
        path=source.path,
        language=source.language,
        module_name=module_name,
        content_hash=sha256_hex(source.content),
        is_test=source.is_test,
        size_bytes=len(source.content),
        nodes=extraction.nodes,
        references=extraction.references,
        imports=extraction.imports,
        bindings=extraction.bindings,
        reexports=extraction.reexports,
        syntax_errors=extraction.syntax_errors,
    )


@dataclass(frozen=True, slots=True)
class ParseStats:
    files: int
    skipped: Counter[str]
    nodes_by_kind: Counter[str]
    edges_by_kind: Counter[str]
    calls: int
    calls_resolved: int
    calls_external: int
    files_with_syntax_errors: int
    languages: Counter[str] = field(default_factory=Counter)

    @property
    def call_resolution(self) -> float:
        """Share of all call edges that point at a node in the repo."""
        return self.calls_resolved / self.calls if self.calls else 0.0

    @property
    def internal_call_resolution(self) -> float:
        """Same, excluding calls into stdlib/third-party code, which can never
        resolve. This is the number that reflects resolver quality."""
        internal = self.calls - self.calls_external
        return self.calls_resolved / internal if internal else 0.0


def summarize(result: ParseResult) -> ParseStats:
    calls = [e for e in result.edges if e.kind is EdgeKind.CALLS]
    return ParseStats(
        files=len(result.files),
        skipped=Counter(s.reason for s in result.skipped),
        nodes_by_kind=Counter(NodeKind(n.kind).value for f in result.files for n in f.nodes),
        edges_by_kind=Counter(EdgeKind(e.kind).value for e in result.edges),
        calls=len(calls),
        calls_resolved=sum(1 for e in calls if e.target is not None),
        calls_external=sum(1 for e in calls if e.external),
        files_with_syntax_errors=sum(1 for f in result.files if f.syntax_errors),
        languages=Counter(f.language.value for f in result.files),
    )
