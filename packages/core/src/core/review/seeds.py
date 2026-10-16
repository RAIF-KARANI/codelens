"""Seeds: the code nodes a change touched, where retrieval starts.

Each changed line is attributed to the *innermost* node containing it: a line
inside a method seeds the method, not its class or module. A line in a class
body outside any method seeds the class; an import seeds the module.

- Added lines are mapped on the head side.
- Removed lines are mapped on the base side. If that base node still exists
  at head, it was modified. If it was deleted, its closest surviving ancestor
  was modified (a class that lost a method, a module that lost a function),
  and the deleted node itself becomes a DELETED seed.

Head and base versions of a node are matched by their name *within the file*
(qualified name minus the module), not by full qualified name, so a file
rename with edits still yields MODIFIED seeds rather than ADDED + DELETED.
Whitespace-only lines are ignored throughout: they can't change behaviour.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from core.diff import FileDiff, FileStatus
from core.enums import NodeKind
from core.parser.types import NodeRef, ParsedFile, ParsedNode


class ChangeKind(StrEnum):
    ADDED = "added"  # exists only at head
    MODIFIED = "modified"  # exists in both, lines changed inside it
    DELETED = "deleted"  # exists only at base; its callers are prime suspects


@dataclass(frozen=True, slots=True)
class Seed:
    change: ChangeKind
    path: str  # head path (base path for DELETED)
    head: ParsedNode | None  # None for DELETED
    base: ParsedNode | None  # None for ADDED
    added_lines: frozenset[int]  # head-side lines inside this node that were added
    removed_count: int  # base-side lines removed from directly inside this node

    @property
    def node(self) -> ParsedNode:
        node = self.head or self.base
        assert node is not None
        return node

    @property
    def signature_changed(self) -> bool:
        """A changed signature means callers may now be wrong; worth extra scrutiny."""
        return (
            self.head is not None
            and self.base is not None
            and self.head.signature != self.base.signature
        )

    @property
    def change_size(self) -> int:
        return len(self.added_lines) + self.removed_count


def local_name(node: ParsedNode, module_name: str) -> tuple[str, int]:
    """A node's identity within its file: `pkg.mod.Cls.m` -> ("Cls.m", 0).

    Stripping the module (which is derived from the path) is what lets a renamed
    file's nodes be matched to their old selves.
    """
    name = node.qualified_name
    if name == module_name:
        return ("", node.ordinal)
    for sep in (".", ":"):
        if name.startswith(module_name + sep):
            return (name[len(module_name) + 1 :], node.ordinal)
    return (name, node.ordinal)


def innermost(nodes: Sequence[ParsedNode], line: int) -> ParsedNode | None:
    """The smallest node whose line range contains `line`.

    Nodes are in pre-order (parents before children), so on a tie, e.g. a
    file that is a single function whose range equals the module's, `<=`
    lets the later, deeper node win.
    """
    best: ParsedNode | None = None
    for node in nodes:
        if node.start_line <= line <= node.end_line and (
            best is None or (node.end_line - node.start_line) <= (best.end_line - best.start_line)
        ):
            best = node
    return best


def innermost_lines(nodes: Sequence[ParsedNode], lines: Iterable[int]) -> dict[int, ParsedNode]:
    """`innermost` for many lines at once, with the same tie rule.

    Calling `innermost` per changed line scans every node for every line: a
    flat file of 26k functions with all lines added took ~11 s (D131). Here
    each node visits only the wanted lines inside its range (a bisect), so
    the work is the lines times how deeply they nest."""
    wanted = sorted(set(lines))
    best: dict[int, ParsedNode] = {}
    for node in nodes:  # pre-order, so the later of equal-sized nodes wins
        size = node.end_line - node.start_line
        for line in wanted[
            bisect_left(wanted, node.start_line) : bisect_right(wanted, node.end_line)
        ]:
            current = best.get(line)
            if current is None or size <= current.end_line - current.start_line:
                best[line] = node
    return best


def find_seeds(diff: FileDiff, head: ParsedFile | None, base: ParsedFile | None) -> list[Seed]:
    head_by_name = {local_name(n, head.module_name): n for n in head.nodes} if head else {}
    base_by_name = {local_name(n, base.module_name): n for n in base.nodes} if base else {}
    base_by_ref = {n.ref: n for n in base.nodes} if base else {}

    # head node ref -> (node, added lines inside it)
    touched: dict[NodeRef, tuple[ParsedNode, set[int]]] = {}

    def touch(node: ParsedNode, line: int | None = None) -> None:
        entry = touched.setdefault(node.ref, (node, set()))
        if line is not None:
            entry[1].add(line)

    if head:
        added = list(diff.added_lines(ignore_blank=True))
        added_in = innermost_lines(head.nodes, added)
        for line in added:
            node = added_in.get(line)
            if node is not None:
                touch(node, line)

    removed_per_base: dict[NodeRef, int] = {}
    deleted: dict[NodeRef, ParsedNode] = {}
    if base:
        removed = list(diff.removed_lines(ignore_blank=True))
        removed_in = innermost_lines(base.nodes, removed)
        for line in removed:
            node = removed_in.get(line)
            if node is None:
                continue
            removed_per_base[node.ref] = removed_per_base.get(node.ref, 0) + 1
            # Walk up until we reach a node that still exists at head.
            current: ParsedNode | None = node
            while current is not None:
                survivor = head_by_name.get(local_name(current, base.module_name))
                if survivor is not None:
                    touch(survivor)
                    break
                if current.kind is not NodeKind.MODULE:
                    deleted[current.ref] = current
                current = base_by_ref.get(current.parent) if current.parent else None

    if base and diff.status is FileStatus.DELETED:
        # The whole file went away: every definition in it is deleted.
        deleted.update({n.ref: n for n in base.nodes if n.kind is not NodeKind.MODULE})

    seeds: list[Seed] = []
    for node, lines in touched.values():
        base_node = base_by_name.get(local_name(node, head.module_name)) if head else None
        seeds.append(
            Seed(
                change=ChangeKind.ADDED if base_node is None else ChangeKind.MODIFIED,
                path=diff.path,
                head=node,
                base=base_node,
                added_lines=frozenset(lines),
                removed_count=removed_per_base.get(base_node.ref, 0) if base_node else 0,
            )
        )
    for node in deleted.values():
        seeds.append(
            Seed(
                change=ChangeKind.DELETED,
                path=diff.old_path or diff.path,
                head=None,
                base=node,
                added_lines=frozenset(),
                removed_count=removed_per_base.get(node.ref, 0),
            )
        )
    seeds.sort(key=lambda s: (s.path, s.node.start_line, s.change.value, s.node.qualified_name))
    return seeds
