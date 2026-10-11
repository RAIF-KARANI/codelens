"""Build the seeded-bug and clean datasets from the pinned sources.

    python -m evals.generate_seeded [--per-operator 12] [--clean-per-source 6]

For each source: check out the pinned commit, parse it with core.parser, run
every operator over every non-test function, then pick cases round-robin
across sources so no repo dominates an operator. Every case is checked: the
mutated file still parses, and the patch applies with `git apply --check`.

Deterministic: the same sources.yaml and code give byte-identical datasets.
The previous evals/datasets/{seeded,clean} are replaced.
"""

import argparse
import ast
import hashlib
import random
import shutil
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from core.diff import parse_unified_diff
from core.enums import Language
from core.parser.repository import parse_source
from core.parser.types import ParsedFile
from core.parser.walker import SourceFile, source_from_content
from evals.cases import KINDS, Case, ExpectedFinding, Kind, OracleNode, write_case
from evals.edits import apply_edits, unified_patch
from evals.gitops import applies
from evals.graph import CodeGraph, Target
from evals.operators import CLEAN, COUNTERFACTUAL, SEEDED, Mutation, Operator
from evals.sources import DEFAULT_CHECKOUTS, DEFAULT_SOURCES, Source, checkout, load_sources

DEFAULT_OUT = Path("evals/datasets")


@dataclass(frozen=True, slots=True)
class Candidate:
    source: Source
    checkout: Path
    target: Target
    operator: str
    kind: Kind
    mutation: Mutation


Built = tuple[Case, str]  # the case (split not yet assigned) and its patch
Pools = dict[str, dict[str, list[Candidate]]]  # source -> operator -> shuffled candidates


def find_candidates(
    source: Source,
    root: Path,
    graph: CodeGraph,
    operators: dict[str, Operator],
    kind: Kind,
) -> dict[str, list[Candidate]]:
    """Every site each operator applies to, shuffled with a seed derived from
    the source and operator: stable across runs, but not biased to file order."""
    targets = graph.targets(source.include)
    pools: dict[str, list[Candidate]] = {}
    for name, operator in operators.items():
        found = []
        for target in targets:
            mutation = operator(target)
            if mutation is not None:
                found.append(Candidate(source, root, target, name, kind, mutation))
        random.Random(f"{source.name}:{source.sha}:{name}").shuffle(found)
        pools[name] = found
    return pools


def build_case(candidate: Candidate) -> Built | None:
    """The case and patch, or None if the mutation breaks parsing or the patch
    doesn't apply (both would make a case that tests the wrong thing).

    A counterfactual also gets a prep commit (its callers made compatible):
    patch.diff is then the mutation on top of the prepared files."""
    target, mutation = candidate.target, candidate.mutation
    graph = target.graph
    path = target.file.path
    original = target.content
    prep_edits = dict(mutation.prep)
    prepared = {p: apply_edits(graph.content(p), edits)[0] for p, edits in prep_edits.items()}
    base = prepared.get(path, original)
    # Prep and mutation edits are both in the original file's coordinates and
    # never overlap (callers vs the function itself), so they apply together.
    new, (first, last) = apply_edits(original, prep_edits.get(path, ()) + mutation.edits)
    changes = [(p, graph.content(p), after) for p, after in prepared.items() if p != path]
    for changed, before, after in [*changes, (path, original, new)]:
        if not _still_parses(graph, graph.file(changed), before, after):
            return None
    prep = "".join(unified_patch(p, graph.content(p), prepared[p]) for p in sorted(prepared))
    patch = unified_patch(path, base, new)
    patches = [prep, patch] if prep else [patch]
    if not patch or not applies(candidate.checkout, candidate.source.sha, *patches):
        return None
    # The reviewer can only comment on added lines (validation rule
    # not_on_changed_lines). If the patch adds any, the expectation must touch
    # one, or a correct finding could never be posted. A pure deletion
    # (remove_guard) adds none: that limit is the pipeline's, and is measured.
    added = parse_unified_diff(patch)[0].added_lines()
    if mutation.expected and added and not added & set(range(first, last + 1)):
        return None

    ref = target.node.ref
    digest = hashlib.sha256(f"{path}:{ref}".encode()).hexdigest()[:8]
    expected = []
    if mutation.expected is not None:
        category, severity = mutation.expected
        expected.append(
            ExpectedFinding(
                file=path, line_start=first, line_end=last, category=category, severity=severity
            )
        )
    case = Case(
        id=f"{candidate.source.name}.{candidate.operator}.{digest}",
        kind=candidate.kind,
        split="test",  # assigned once all cases are known
        source=candidate.source.name,
        sha=candidate.source.sha,
        language=target.file.language,
        operator=candidate.operator,
        file=path,
        function=str(ref),
        description=mutation.description,
        expected=expected,
        oracle=[
            OracleNode(qualified_name=r.qualified_name, ordinal=r.ordinal, role=role)
            for r, role in mutation.oracle
        ],
        prep=prep or None,
    )
    return case, patch


def select_per_operator(
    pools: Pools,
    operators: Iterable[str],
    per_operator: int,
    build: Callable[[Candidate], Built | None],
) -> list[Built]:
    """Up to `per_operator` cases per operator, taking from the sources in turn."""
    chosen: list[Built] = []
    for operator in operators:
        queues = [iter(pools[name][operator]) for name in sorted(pools)]
        chosen += _round_robin(queues, per_operator, build)
    return chosen


def select_clean(
    pools: Pools, per_source: int, build: Callable[[Candidate], Built | None]
) -> list[Built]:
    """Up to `per_source` clean cases per source, alternating the edit kinds."""
    chosen: list[Built] = []
    for name in sorted(pools):
        queues = [iter(pools[name][operator]) for operator in CLEAN]
        chosen += _round_robin(queues, per_source, build)
    return chosen


def _round_robin(
    queues: list[Iterator[Candidate]], quota: int, build: Callable[[Candidate], Built | None]
) -> list[Built]:
    picked: list[Built] = []
    while queues and len(picked) < quota:
        for queue in list(queues):
            candidate = next(queue, None)
            if candidate is None:
                queues.remove(queue)
                continue
            built = build(candidate)
            if built is not None:
                picked.append(built)
                if len(picked) == quota:
                    break
    return picked


def assign_splits(cases: list[Built], dev_share: float) -> list[Built]:
    """Stratified dev/test split: within each (kind, operator), the cases with
    the lowest id hashes go to dev. Prompts and weights are tuned on dev only
    (planning §10.4); reported numbers come from test."""
    groups: dict[tuple[str, str], list[Built]] = defaultdict(list)
    for case, patch in cases:
        groups[(case.kind, case.operator)].append((case, patch))
    result = []
    for group in groups.values():
        group.sort(key=lambda built: hashlib.sha256(built[0].id.encode()).hexdigest())
        n_dev = round(len(group) * dev_share)
        for i, (case, patch) in enumerate(group):
            result.append(
                (case.model_copy(update={"split": "dev" if i < n_dev else "test"}), patch)
            )
    return sorted(result, key=lambda built: built[0].id)


def write_dataset(out: Path, cases: list[Built]) -> None:
    for kind in KINDS:
        shutil.rmtree(out / kind, ignore_errors=True)
    for case, patch in cases:
        write_case(out, case, patch)
    (out / "summary.md").write_text(summarize(cases), encoding="utf-8", newline="\n")


def summarize(cases: list[Built]) -> str:
    counts = Counter((c.kind, c.operator, c.source) for c, _ in cases)
    splits = Counter((c.kind, c.split) for c, _ in cases)
    sources = sorted({c.source for c, _ in cases})
    kinds = {
        **dict.fromkeys(SEEDED, "seeded"),
        **dict.fromkeys(COUNTERFACTUAL, "counterfactual"),
        **dict.fromkeys(CLEAN, "clean"),
    }
    lines = [
        "# Dataset summary",
        "",
        "Generated by `python -m evals.generate_seeded`; do not edit by hand.",
        "",
        "| kind | operator | " + " | ".join(sources) + " | total |",
        "|---|---|" + "---|" * len(sources) + "---|",
    ]
    for operator, kind in kinds.items():
        row = [counts[(kind, operator, s)] for s in sources]
        lines.append(f"| {kind} | {operator} | " + " | ".join(map(str, row)) + f" | {sum(row)} |")
    for kind in KINDS:
        lines.append("")
        lines.append(f"{kind}: {splits[(kind, 'dev')]} dev / {splits[(kind, 'test')]} test")
    return "\n".join(lines) + "\n"


def _still_parses(graph: CodeGraph, file: ParsedFile, old: bytes, new: bytes) -> bool:
    source = source_from_content(file.path, new)
    if not isinstance(source, SourceFile):
        return False
    if parse_source(source, graph.package_dirs).syntax_errors > file.syntax_errors:
        return False
    if file.language is Language.PYTHON:
        # tree-sitter is lenient; the real compiler isn't (e.g. `return` with a
        # value in an async generator).
        try:
            ast.parse(old)
        except SyntaxError:
            return True  # the original doesn't compile on this Python either
        try:
            ast.parse(new)
        except SyntaxError:
            return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m evals.generate_seeded", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--checkouts", type=Path, default=DEFAULT_CHECKOUTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--per-operator", type=int, default=12, help="seeded cases per operator")
    parser.add_argument(
        "--counterfactual-per-operator", type=int, default=12, help="counterfactuals per operator"
    )
    parser.add_argument("--clean-per-source", type=int, default=6)
    parser.add_argument("--dev-share", type=float, default=0.3)
    args = parser.parse_args()

    seeded: Pools = {}
    counterfactual: Pools = {}
    clean: Pools = {}
    for source in load_sources(args.sources):
        started = time.perf_counter()
        root = checkout(source, args.checkouts)
        graph = CodeGraph.from_path(root)
        seeded[source.name] = find_candidates(source, root, graph, SEEDED, "seeded")
        counterfactual[source.name] = find_candidates(
            source, root, graph, COUNTERFACTUAL, "counterfactual"
        )
        clean[source.name] = find_candidates(source, root, graph, CLEAN, "clean")
        pools = {**seeded[source.name], **counterfactual[source.name], **clean[source.name]}
        sizes = ", ".join(f"{op} {len(c)}" for op, c in pools.items())
        print(f"{source.name} @ {source.sha[:10]} ({time.perf_counter() - started:.1f}s): {sizes}")

    cases = select_per_operator(seeded, SEEDED, args.per_operator, build_case)
    cases += select_per_operator(
        counterfactual, COUNTERFACTUAL, args.counterfactual_per_operator, build_case
    )
    cases += select_clean(clean, args.clean_per_source, build_case)
    cases = assign_splits(cases, args.dev_share)
    write_dataset(args.out, cases)
    print()
    print(summarize(cases))
    if not cases:
        sys.exit("no cases generated")


if __name__ == "__main__":
    main()
