"""Chunk text: what gets embedded for each code node (planning.md §5.6).

A chunk is a node of the code graph, not a fixed-size window, so a vector hit
maps 1:1 onto a graph node. Each chunk starts with a short context header (the
"where" and "what"), followed by:

- function / method: the full source
- class / module:    a *skeleton*: the source with each direct child replaced
                     by its one-line signature. Method bodies are embedded once,
                     in their own chunks, not again inside the class.

Text over the token budget is cut at a line boundary and marked as truncated.
"""

from dataclasses import dataclass

from core.enums import NodeKind
from core.text.tokens import chars_for_tokens, estimate_tokens

# Bump when the chunk format changes. It's part of the text, so changing it
# changes every chunk's hash, and the embedding job re-embeds everything.
CHUNK_FORMAT = "v1"

TRUNCATION_MARKER = "# ... truncated"


@dataclass(frozen=True, slots=True)
class NodeInfo:
    """The parts of a code node a chunk is built from (no DB/ORM types)."""

    kind: NodeKind
    name: str
    qualified_name: str
    signature: str | None
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class ChunkSource:
    node: NodeInfo
    path: str
    code: str
    parent: NodeInfo | None  # the containing class/function; None for top-level nodes
    children: tuple[NodeInfo, ...]  # direct children (class methods, module definitions)


@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    truncated: bool
    tokens: int  # estimated


def build_chunk(source: ChunkSource, *, max_tokens: int) -> Chunk:
    header = _header(source)
    node = source.node
    if node.kind in (NodeKind.CLASS, NodeKind.MODULE):
        body = skeleton(source.code, node.start_line, source.children)
    else:
        body = source.code

    budget = chars_for_tokens(max_tokens) - len(header) - len(TRUNCATION_MARKER) - 2
    truncated = len(body) > budget
    if truncated:
        body = _cut_at_line(body, max(budget, 0)) + "\n" + TRUNCATION_MARKER
    text = f"{header}\n{body}"
    return Chunk(text, truncated, estimate_tokens(text))


def _header(source: ChunkSource) -> str:
    node = source.node
    lines = [
        f"# {CHUNK_FORMAT} path: {source.path}",
        f"# symbol: {node.qualified_name} ({node.kind.value})",
    ]
    parent = source.parent
    if parent is not None and parent.kind is not NodeKind.MODULE and parent.signature:
        # A method's meaning depends on its class: "save" alone says little,
        # "save" in "class Order(Entity)" says much more.
        lines.append(f"# in {parent.kind.value}: {parent.signature}")
    return "\n".join(lines)


def skeleton(code: str, start_line: int, children: tuple[NodeInfo, ...]) -> str:
    """Replace each direct child's line range with its signature + " ...",
    keeping the child's indentation. Everything else (imports, constants,
    class attributes, `if __name__ == "__main__":` blocks) stays verbatim."""
    lines = code.split("\n")
    # Work bottom-up so earlier line numbers stay valid while replacing.
    for child in sorted(children, key=lambda c: c.start_line, reverse=True):
        first = child.start_line - start_line
        last = child.end_line - start_line
        if not (0 <= first <= last < len(lines)):
            continue  # defensive: a child outside the parent's range
        indent = lines[first][: len(lines[first]) - len(lines[first].lstrip())]
        summary = child.signature or child.name
        lines[first : last + 1] = [f"{indent}{summary} ..."]
    return "\n".join(lines)


def _cut_at_line(text: str, max_chars: int) -> str:
    """The longest prefix of whole lines within max_chars (at least one line,
    hard-cut if even the first line is too long)."""
    if len(text) <= max_chars:
        return text
    cut = text.rfind("\n", 0, max_chars)
    return text[:cut] if cut > 0 else text[:max_chars]
