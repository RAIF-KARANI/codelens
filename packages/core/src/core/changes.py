"""Show what a branch changed, down to the functions and classes it touched.

    python -m core.changes <repo_path> --base main [--head HEAD]

Compares head against its merge-base with base (like a GitHub PR). Reads git
objects only: no checkout, no DB.
"""

import argparse
import sys
from pathlib import Path

from core.diff.git import GitError
from core.review import ChangeKind, collect_changes

_MARK = {ChangeKind.ADDED: "+", ChangeKind.MODIFIED: "~", ChangeKind.DELETED: "-"}


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m core.changes", description=__doc__.split("\n")[0]
    )
    parser.add_argument("repo", type=Path, help="path to a local git repository")
    parser.add_argument("--base", required=True, help="branch/commit the change is based on")
    parser.add_argument("--head", default="HEAD", help="branch/commit to review (default HEAD)")
    args = parser.parse_args()

    try:
        changes = collect_changes(args.repo, args.base, args.head)
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"{args.head} ({changes.head_sha[:10]}) vs merge-base {changes.base_sha[:10]}")
    print(f"{len(changes.files)} files changed, {len(changes.seeds)} seeds\n")
    for change in changes.files:
        diff = change.diff
        rename = (
            f" (from {diff.old_path})"
            if diff.old_path and diff.old_path != diff.new_path and diff.new_path
            else ""
        )
        added, removed = len(diff.added_lines()), len(diff.removed_lines())
        note = f"  [{change.skipped}]" if change.skipped else ""
        print(f"{diff.status.value:<9} {diff.path}{rename}  +{added} -{removed}{note}")
        for seed in change.seeds:
            node = seed.node
            name = node.qualified_name + (f"#{node.ordinal}" if node.ordinal else "")
            flags = "  signature changed" if seed.signature_changed else ""
            size = f"(+{len(seed.added_lines)} -{seed.removed_count})"
            print(
                f"  {_MARK[seed.change]} {node.kind.value:<8} {name}  "
                f"L{node.start_line}-{node.end_line}  {size}{flags}"
            )


if __name__ == "__main__":
    main()
