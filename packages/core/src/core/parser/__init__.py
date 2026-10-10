"""Source code -> code graph (nodes + edges), with no database involved.

from core.parser import parse_repository
result = parse_repository(Path("path/to/repo"))
"""

from core.parser.repository import ParseStats, parse_repository, summarize
from core.parser.types import (
    NodeRef,
    ParsedEdge,
    ParsedFile,
    ParsedNode,
    ParseResult,
    SkippedFile,
)

__all__ = [
    "NodeRef",
    "ParseResult",
    "ParseStats",
    "ParsedEdge",
    "ParsedFile",
    "ParsedNode",
    "SkippedFile",
    "parse_repository",
    "summarize",
]
