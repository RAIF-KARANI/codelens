/** Where each node of a review's call graph goes: four columns, read left to
 * right as "who calls it → what changed → what it calls → other context". Our
 * own ~50 lines instead of a layout engine (dagre, elk): the graph is one hop
 * deep, so columns say everything, and they say it the same way every time.
 * Pure, so it's unit-tested. */

export type LayoutNode = {
  id: string;
  qualified_name: string;
  path: string | null;
  is_seed: boolean;
  depth: number | null;
};

export type LayoutEdge = { source: string; target: string };

export type Column = "callers" | "changed" | "callees" | "context";

export const COLUMNS: { key: Column; title: string }[] = [
  { key: "callers", title: "Callers" },
  { key: "changed", title: "Changed code" },
  { key: "callees", title: "Called by the change" },
  { key: "context", title: "Other context shown to the model" },
];

export const COLUMN_WIDTH = 300;
export const ROW_HEIGHT = 64;

export type Placed = { id: string; column: Column; x: number; y: number };

export function columnOf(node: LayoutNode, edges: LayoutEdge[], seeds: Set<string>): Column {
  if (node.is_seed) return "changed";
  if (node.depth === null) return "context";
  // A neighbour that calls (or inherits from) a seed is its caller; anything
  // else one hop away is called by one. A node that is both counts as a caller:
  // a change is most likely to break what depends on it.
  const calls = edges.some((e) => e.source === node.id && seeds.has(e.target));
  return calls ? "callers" : "callees";
}

export function layout(nodes: LayoutNode[], edges: LayoutEdge[]): Placed[] {
  const seeds = new Set(nodes.filter((n) => n.is_seed).map((n) => n.id));
  const groups = new Map<Column, LayoutNode[]>(COLUMNS.map((c) => [c.key, []]));
  for (const node of nodes) groups.get(columnOf(node, edges, seeds))!.push(node);

  const tallest = Math.max(1, ...[...groups.values()].map((g) => g.length));
  const placed: Placed[] = [];
  COLUMNS.forEach(({ key }, index) => {
    const group = groups.get(key)!;
    // By file, then name, so related nodes sit together.
    group.sort(
      (a, b) =>
        (a.path ?? "").localeCompare(b.path ?? "") ||
        a.qualified_name.localeCompare(b.qualified_name),
    );
    // Centre each column against the tallest one.
    const top = ((tallest - group.length) * ROW_HEIGHT) / 2;
    group.forEach((node, row) => {
      placed.push({ id: node.id, column: key, x: index * COLUMN_WIDTH, y: top + row * ROW_HEIGHT });
    });
  });
  return placed;
}
