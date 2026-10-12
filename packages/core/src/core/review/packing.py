"""S7 pack: changed files -> review chunks that fit one LLM call each.

A chunk holds some changed files' diffs, the code of their changed nodes
("C" blocks) and retrieved context ("N" blocks), each within its own token
budget (planning.md §6.2). A block that doesn't fit whole is reduced to its
signature line, so the model still knows the function exists.

Everything shown to the model carries new-file line numbers in a left
column: models are bad at counting lines from @@ headers, and a finding is
only usable if it points at exact lines.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from core.diff import FileDiff, Hunk
from core.enums import NodeKind
from core.review.changes import ChangeSet, FileChange
from core.review.config import PackingConfig
from core.review.retrieval import Candidate, merge_candidates
from core.review.seeds import ChangeKind, Seed
from core.text.tokens import chars_for_tokens, estimate_tokens


@dataclass(frozen=True, slots=True)
class Block:
    label: str  # "C1" (changed code) or "N12" (context node 12)
    attrs: dict[str, str]  # rendered as XML-ish attributes in the prompt
    text: str
    tokens: int
    truncated: bool  # True = signature only


@dataclass(frozen=True, slots=True)
class TraceItem:
    candidate: Candidate
    included: bool
    truncated: bool
    tokens: int
    reason: str | None = None  # why it wasn't included


@dataclass(frozen=True, slots=True)
class ReviewChunk:
    index: int
    files: tuple[FileChange, ...]
    diff_text: str
    changed: tuple[Block, ...]
    context: tuple[Block, ...]
    trace: tuple[TraceItem, ...]

    @property
    def paths(self) -> list[str]:
        return [f.diff.path for f in self.files]

    @property
    def labels(self) -> set[str]:
        return {b.label for b in self.changed} | {b.label for b in self.context}

    def file(self, path: str) -> FileChange | None:
        return next((f for f in self.files if f.diff.path == path), None)


def numbered(code: str, start_line: int) -> str:
    """Code with its file line numbers in a left column."""
    return "\n".join(f"{start_line + i:>5}  {line}" for i, line in enumerate(code.split("\n")))


def render_diff(diff: FileDiff, style: str = "unified") -> str:
    """The diff with each line's *new-file* line number on the left.

    unified: removed and added lines interleaved; removed lines have no number.
    split (Q12): each hunk twice, before (unnumbered, removed lines marked "-")
    and after (numbered, added lines marked "+"), so a line that moved or was
    rewritten isn't read as two lines that both exist."""
    out = [f"--- {diff.old_path or '/dev/null'}", f"+++ {diff.new_path or '/dev/null'}"]
    marker = {"added": "+", "removed": "-", "context": " "}
    for hunk in diff.hunks:
        out.append(
            f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@ "
            f"{hunk.section}".rstrip()
        )
        if style == "split":
            out += _split_hunk(hunk, marker)
            continue
        for line in hunk.lines:
            number = f"{line.new_line:>5}" if line.new_line is not None else "     "
            out.append(f"{number} {marker[line.kind]} {line.text}")
    return "\n".join(out)


def _split_hunk(hunk: Hunk, marker: dict[str, str]) -> list[str]:
    out = []
    if any(line.kind == "removed" for line in hunk.lines):
        out.append("before (for reference only; do not cite these lines):")
        out += [
            f"      {marker[line.kind]} {line.text}" for line in hunk.lines if line.kind != "added"
        ]
        out.append("after:")
    out += [
        f"{line.new_line:>5} {marker[line.kind]} {line.text}"
        for line in hunk.lines
        if line.kind != "removed" and line.new_line is not None
    ]
    return out


def _cut(text: str, tokens: int, note: str) -> str:
    limit = chars_for_tokens(tokens) - len(note) - 1
    cut = text.rfind("\n", 0, max(limit, 0))
    return (text[:cut] if cut > 0 else text[: max(limit, 0)]) + "\n" + note


def build_chunks(
    changes: ChangeSet,
    seeds: Sequence[Seed],
    candidates_per_seed: dict[int, list[Candidate]],
    config: PackingConfig,
) -> tuple[list[ReviewChunk], list[str]]:
    """Returns (chunks, paths left out because of max_chunks)."""
    # Only parsed source files with seeds are reviewed. Others (docs, configs,
    # vendored or minified files) have no nodes to anchor findings to.
    # Source files first, then tests, so related source files share chunks.
    reviewable = sorted(
        (f for f in changes.files if f.head is not None and f.seeds),
        key=lambda f: (f.head.is_test if f.head else False, f.diff.path),
    )

    groups: list[list[tuple[FileChange, str]]] = []
    current: list[tuple[FileChange, str]] = []
    used = 0
    for change in reviewable:
        text = render_diff(change.diff, config.diff_style)
        tokens = estimate_tokens(text)
        if tokens > config.diff_tokens:  # one huge file: keep its first hunks
            text = _cut(text, config.diff_tokens, "... (diff truncated)")
            tokens = config.diff_tokens
        if current and used + tokens > config.diff_tokens:
            groups.append(current)
            current, used = [], 0
        current.append((change, text))
        used += tokens
    if current:
        groups.append(current)

    # Too many chunks: keep the groups with the most changed lines.
    left_out: list[str] = []
    if len(groups) > config.max_chunks:

        def weight(change: FileChange) -> float:
            return config.test_file_weight if change.head and change.head.is_test else 1.0

        sizes = [sum(weight(f) * s.change_size for f, _ in g for s in f.seeds) for g in groups]
        ranked = sorted(range(len(groups)), key=lambda i: -sizes[i])
        keep = set(ranked[: config.max_chunks])
        left_out = [f.diff.path for i, g in enumerate(groups) if i not in keep for f, _ in g]
        groups = [g for i, g in enumerate(groups) if i in keep]  # path order preserved

    seed_index = {id(s): i for i, s in enumerate(seeds)}
    chunks: list[ReviewChunk] = []
    for index, group in enumerate(groups):
        files = tuple(f for f, _ in group)
        chunk_seeds = [s for f in files for s in f.seeds if id(s) in seed_index]
        changed = _changed_blocks(files, config)
        candidates = merge_candidates(
            [candidates_per_seed.get(seed_index[id(s)], []) for s in chunk_seeds]
        )
        context, trace = _context_blocks(candidates, files, config)
        chunks.append(
            ReviewChunk(
                index=index,
                files=files,
                diff_text="\n\n".join(text for _, text in group),
                changed=tuple(changed),
                context=tuple(context),
                trace=tuple(trace),
            )
        )
    return chunks, left_out


def fit_to_window(
    chunk: ReviewChunk, measure: Callable[[ReviewChunk], int], max_tokens: int
) -> ReviewChunk | None:
    """Shrink a chunk until its rendered prompt fits the model's window.

    Ollama silently cuts a prompt that exceeds num_ctx, which could remove the
    instructions themselves, so we never send one. Context blocks go first
    (they're best-first, so the weakest from the end), then changed blocks;
    the diff is never cut here. Returns None if even the bare diff is too big.
    """
    while measure(chunk) > max_tokens:
        if chunk.context:
            dropped = chunk.context[-1].label
            trace = tuple(
                replace(t, included=False, truncated=False, tokens=0, reason="context_window")
                if t.included and t.candidate.label == dropped
                else t
                for t in chunk.trace
            )
            chunk = replace(chunk, context=chunk.context[:-1], trace=trace)
        elif chunk.changed:
            chunk = replace(chunk, changed=chunk.changed[:-1])
        else:
            return None
    return chunk


def _changed_blocks(files: Sequence[FileChange], config: PackingConfig) -> list[Block]:
    # Module seeds are skipped: the diff already shows module-level changes,
    # and a module's code is the whole file.
    seeds = [s for f in files for s in f.seeds if s.node.kind is not NodeKind.MODULE]
    seeds.sort(key=lambda s: -s.change_size)
    blocks: list[Block] = []
    budget = config.changed_node_tokens
    for i, seed in enumerate(seeds, start=1):
        node = seed.node
        attrs = {
            "name": node.qualified_name,
            "kind": node.kind.value,
            "path": seed.path,
            "lines": f"{node.start_line}-{node.end_line}",
            "change": seed.change.value,
        }
        if seed.change is ChangeKind.DELETED:
            attrs["note"] = "deleted by this change; shown as it was before"
        if seed.signature_changed:
            attrs["signature_changed"] = "yes"
        block = _fit(f"C{i}", attrs, node.code, node.start_line, node.signature, budget, config)
        if block is not None:
            blocks.append(block)
            budget -= block.tokens
    return blocks


def _context_blocks(
    candidates: Sequence[Candidate], files: Sequence[FileChange], config: PackingConfig
) -> tuple[list[Block], list[TraceItem]]:
    changed_paths = {f.diff.path for f in files} | {
        f.diff.old_path for f in files if f.diff.old_path
    }
    blocks: list[Block] = []
    trace: list[TraceItem] = []
    budget = config.context_tokens
    for c in candidates:
        # A stored class/module from a file this change edits contains the
        # *old* version of the changed code: misleading, so leave it out.
        if c.path in changed_paths and c.kind in (NodeKind.CLASS, NodeKind.MODULE):
            trace.append(TraceItem(c, False, False, 0, "contains_changed_code"))
            continue
        attrs = {
            "name": c.qualified_name,
            "kind": c.kind.value,
            "path": c.path,
            "lines": f"{c.start_line}-{c.end_line}",
            "similarity": f"{c.similarity:.2f}",
        }
        if c.relation:
            attrs["relation"] = c.relation
        block = _fit(c.label, attrs, c.code, c.start_line, c.signature, budget, config)
        if block is None:
            trace.append(TraceItem(c, False, False, 0, "over_budget"))
            continue
        blocks.append(block)
        budget -= block.tokens
        trace.append(TraceItem(c, True, block.truncated, block.tokens))
    return blocks, trace


def _fit(
    label: str,
    attrs: dict[str, str],
    code: str,
    start_line: int,
    signature: str | None,
    budget: int,
    config: PackingConfig,
) -> Block | None:
    """The whole block if it fits, else just its signature, else nothing."""
    full = numbered(code, start_line)
    tokens = estimate_tokens(full)
    if tokens <= budget:
        return Block(label, attrs, full, tokens, truncated=False)
    if config.degrade_to_signature and signature:
        short = numbered(signature + "  # ... body omitted", start_line)
        short_tokens = estimate_tokens(short)
        if short_tokens <= budget:
            return Block(label, attrs, short, short_tokens, truncated=True)
    return None
