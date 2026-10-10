"""Human-readable summary of an index run (what the CLI prints)."""

from collections.abc import Mapping

from core.indexing.indexer import IndexReport


def format_report(report: IndexReport) -> str:
    p, w = report.parse, report.writes
    skipped = sum(p.skipped.values())
    lines = [
        f"Indexed {report.repo_name} (repo {report.repo_id}, run {report.index_run_id})"
        + (f" @ {report.commit_sha[:12]}" if report.commit_sha else ""),
        "",
        f"  files    {p.files} parsed ({_fmt(p.languages)})"
        + (f", {skipped} skipped ({_fmt(p.skipped)})" if skipped else ""),
        f"           {report.files_new} new, {report.files_changed} changed, "
        f"{report.files_unchanged} unchanged, {report.files_deleted} deleted",
        f"  nodes    {sum(p.nodes_by_kind.values())} ({_fmt(p.nodes_by_kind)})",
        f"           {w.nodes_inserted} inserted, {w.nodes_updated} updated, "
        f"{w.nodes_moved} moved, {w.nodes_kept} kept, "
        f"{w.nodes_deleted + report.nodes_deleted_with_files} deleted",
        f"  edges    {sum(p.edges_by_kind.values())} ({_fmt(p.edges_by_kind)})",
        f"           {w.edges_written} written, {report.edges_relinked} re-linked",
        f"  calls    {p.calls_resolved}/{p.calls} resolved: {p.call_resolution:.1%} of all, "
        f"{p.internal_call_resolution:.1%} excluding {p.calls_external} external",
        f"  in DB    {report.db_totals.get('nodes', 0)} nodes, "
        f"{report.db_totals.get('edges', 0)} edges "
        f"({report.db_totals.get('edges_resolved', 0)} resolved)",
        f"  time     parse {report.parse_seconds:.2f}s, write {report.write_seconds:.2f}s",
    ]
    if not report.wrote_graph:
        lines.append("\n  No changes: nothing written to files / nodes / edges.")
    return "\n".join(lines)


def _fmt(counter: Mapping[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))
