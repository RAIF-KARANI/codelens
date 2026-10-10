"""Debug CLI: parse a repo and print what the parser sees (writes nothing).

python -m core.parser <path>                    summary
python -m core.parser <path> --nodes            + every node
python -m core.parser <path> --edges            + every edge
python -m core.parser <path> --unresolved       + unresolved in-repo calls
python -m core.parser <path> --file src/x.py    limit listings to one file
"""

import argparse
import time
from pathlib import Path

from core.enums import EdgeKind
from core.parser import ParseResult, parse_repository, summarize


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m core.parser", description=__doc__.split("\n")[0]
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--nodes", action="store_true", help="list nodes")
    parser.add_argument("--edges", action="store_true", help="list edges")
    parser.add_argument("--unresolved", action="store_true", help="list unresolved in-repo calls")
    parser.add_argument("--file", help="only list nodes/edges from this file path")
    args = parser.parse_args()

    started = time.perf_counter()
    result = parse_repository(args.path)
    elapsed = time.perf_counter() - started

    _print_summary(result, elapsed)
    if args.nodes:
        _print_nodes(result, args.file)
    if args.edges or args.unresolved:
        _print_edges(result, args.file, unresolved_only=args.unresolved and not args.edges)


def _print_summary(result: ParseResult, elapsed: float) -> None:
    stats = summarize(result)
    print(f"Parsed {result.root} in {elapsed:.2f}s")
    print(f"  files      {stats.files}  ({_fmt(stats.languages)})")
    print(f"  skipped    {sum(stats.skipped.values())}  ({_fmt(stats.skipped) or '-'})")
    print(f"  syntax err {stats.files_with_syntax_errors} files")
    print(f"  nodes      {sum(stats.nodes_by_kind.values())}  ({_fmt(stats.nodes_by_kind)})")
    print(f"  edges      {sum(stats.edges_by_kind.values())}  ({_fmt(stats.edges_by_kind)})")
    print(
        f"  calls      {stats.calls_resolved}/{stats.calls} resolved "
        f"({stats.call_resolution:.0%} of all, {stats.internal_call_resolution:.0%} "
        f"excluding {stats.calls_external} external)"
    )


def _print_nodes(result: ParseResult, only_file: str | None) -> None:
    print("\nNODES")
    for file in result.files:
        if only_file and file.path != only_file:
            continue
        for node in file.nodes:
            indent = "  " * (node.qualified_name.count(".<locals>.") + (node.kind == "method"))
            location = f"{file.path}:{node.start_line}-{node.end_line}"
            print(f"  {indent}{node.kind.value:<8} {node.ref}  [{location}]")


def _print_edges(result: ParseResult, only_file: str | None, *, unresolved_only: bool) -> None:
    sources = {n.ref: f.path for f in result.files for n in f.nodes}
    print("\nUNRESOLVED IN-REPO CALLS" if unresolved_only else "\nEDGES")
    for edge in result.edges:
        if only_file and sources.get(edge.source) != only_file:
            continue
        if unresolved_only and (edge.kind is not EdgeKind.CALLS or edge.target or edge.external):
            continue
        status = "ext" if edge.external else ("ok " if edge.target else "?? ")
        print(f"  {status} {edge.kind.value:<8} {edge.source} -> {edge.target_name}")


def _fmt(counter: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(counter.items(), key=lambda kv: -kv[1]))


if __name__ == "__main__":
    main()
