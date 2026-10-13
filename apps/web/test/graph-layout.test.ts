import { describe, expect, it } from "vitest";

import { COLUMN_WIDTH, layout, type LayoutNode, ROW_HEIGHT } from "@/lib/graph-layout";

const node = (id: string, extra: Partial<LayoutNode> = {}): LayoutNode => ({
  id,
  qualified_name: id,
  path: "a.py",
  is_seed: false,
  depth: 1,
  ...extra,
});

describe("layout", () => {
  const nodes = [
    node("seed", { is_seed: true, depth: 0 }),
    node("caller"),
    node("callee"),
    node("both"),
    node("context", { depth: null }),
  ];
  const edges = [
    { source: "caller", target: "seed" },
    { source: "seed", target: "callee" },
    { source: "both", target: "seed" },
    { source: "seed", target: "both" },
  ];
  const placed = Object.fromEntries(layout(nodes, edges).map((p) => [p.id, p]));

  it("puts callers left of the change, callees right, context last", () => {
    expect(placed.caller.column).toBe("callers");
    expect(placed.seed.column).toBe("changed");
    expect(placed.callee.column).toBe("callees");
    expect(placed.context.column).toBe("context");
    expect([placed.caller.x, placed.seed.x, placed.callee.x, placed.context.x]).toEqual([
      0,
      COLUMN_WIDTH,
      2 * COLUMN_WIDTH,
      3 * COLUMN_WIDTH,
    ]);
  });

  it("counts a node that both calls and is called as a caller", () => {
    expect(placed.both.column).toBe("callers");
  });

  it("stacks a column by name and centres it against the tallest", () => {
    // callers: "both", "caller" (2 rows); changed: 1 row, centred between them.
    expect([placed.both.y, placed.caller.y]).toEqual([0, ROW_HEIGHT]);
    expect(placed.seed.y).toBe(ROW_HEIGHT / 2);
  });

  it("handles an empty graph", () => {
    expect(layout([], [])).toEqual([]);
  });
});
