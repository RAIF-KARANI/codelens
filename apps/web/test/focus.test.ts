import { describe, expect, it } from "vitest";

import type { GraphNode, Seed } from "@/lib/api";
import { focusOf } from "@/lib/focus";

const seed = (qualified_name: string, start_line: number, end_line: number): Seed => ({
  path: "sqlite_utils/cli.py",
  qualified_name,
  kind: "function",
  change: "modified",
  start_line,
  end_line,
  signature_changed: false,
});

const node = (id: string, extra: Partial<GraphNode>): GraphNode => ({
  id,
  node_id: null,
  label: id,
  qualified_name: id,
  kind: "function",
  path: "sqlite_utils/cli.py",
  is_seed: false,
  change: null,
  depth: 1,
  in_context: true,
  ...extra,
});

describe("focusOf", () => {
  const seeds = [seed("cli._execute_query", 2263, 2321), seed("cli.query", 1800, 1900)];
  const nodes = [
    node("n1", { node_id: 1, qualified_name: "cli._execute_query", is_seed: true, depth: 0 }),
    node("n2", { node_id: 2, qualified_name: "cli.query", is_seed: true, depth: 0 }),
    node("n3", { node_id: 3, qualified_name: "db.Database.execute" }),
    node("seed:cli.new", { qualified_name: "cli.new", is_seed: true, depth: 0 }),
  ];

  it("picks the changed code the finding sits in, and its evidence", () => {
    const finding = {
      file_path: "sqlite_utils/cli.py",
      line_start: 2264,
      line_end: 2266,
      evidence_node_ids: [3],
    };
    expect(focusOf(finding, seeds, nodes)).toEqual(["n1", "n3"]);
  });

  it("matches nothing in another file", () => {
    const finding = { file_path: "other.py", line_start: 2264, line_end: 2266, evidence_node_ids: [] };
    expect(focusOf(finding, seeds, nodes)).toEqual([]);
  });

  it("finds a new function that isn't in the index yet", () => {
    const withNew = [...seeds, seed("cli.new", 10, 20)];
    const finding = { file_path: "sqlite_utils/cli.py", line_start: 12, line_end: 12, evidence_node_ids: [] };
    expect(focusOf(finding, withNew, nodes)).toEqual(["seed:cli.new"]);
  });
});
