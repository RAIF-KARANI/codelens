/** Which graph nodes a finding is about: the changed definitions its lines
 * fall in, and the code it cites as evidence. The graph view highlights them
 * when opened from a finding. Pure, so it's unit-tested. */

import type { Finding, GraphNode, Seed } from "@/lib/api";

export function focusOf(
  finding: Pick<Finding, "file_path" | "line_start" | "line_end" | "evidence_node_ids">,
  seeds: Seed[],
  nodes: GraphNode[],
): string[] {
  // Seeds are located by the new file's lines, like findings.
  const changed = new Set(
    seeds
      .filter(
        (s) =>
          s.path === finding.file_path &&
          s.start_line <= finding.line_end &&
          s.end_line >= finding.line_start,
      )
      .map((s) => s.qualified_name),
  );
  const evidence = new Set(finding.evidence_node_ids);
  return nodes
    .filter(
      (n) =>
        (n.is_seed && changed.has(n.qualified_name)) ||
        (n.node_id !== null && evidence.has(n.node_id)),
    )
    .map((n) => n.id);
}
