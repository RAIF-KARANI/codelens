"""What a language extractor returns for one file (before repo-wide resolution)."""

from dataclasses import dataclass

from core.parser.types import Binding, Import, ParsedNode, ReExport, Reference


@dataclass(frozen=True, slots=True)
class Extraction:
    nodes: tuple[ParsedNode, ...]
    references: tuple[Reference, ...]
    imports: tuple[Import, ...]
    bindings: tuple[Binding, ...]
    reexports: tuple[ReExport, ...]
    syntax_errors: int
