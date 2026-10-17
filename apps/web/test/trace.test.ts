import { describe, expect, it } from "vitest";

import type { TraceItem } from "@/lib/api";
import {
  filterRows,
  outcome,
  retrievedFor,
  score,
  sortRows,
  tokensUsed,
  traceRows,
} from "@/lib/trace";

const item = (extra: Partial<TraceItem>): TraceItem => ({
  label: "N1",
  node_id: 1,
  qualified_name: "pkg.f",
  included: true,
  truncated: false,
  token_cost: 100,
  stage_scores: {},
  ...extra,
});

describe("trace", () => {
  it("says what happened to each candidate", () => {
    expect(outcome(item({})).kind).toBe("included");
    expect(outcome(item({ truncated: true })).kind).toBe("signature");
    expect(outcome(item({ included: false, stage_scores: { excluded: "over_budget" } }))).toEqual({
      kind: "excluded",
      text: "didn't fit the context budget",
    });
    // A reason the dashboard doesn't know yet still reads.
    expect(
      outcome(item({ included: false, stage_scores: { excluded: "some_new_rule" } })).text,
    ).toBe("some new rule");
  });

  it("reads the stage scores, ignoring junk", () => {
    const real = item({
      stage_scores: {
        vector_rank: 1,
        vector_similarity: 0.7536,
        retrieved_for: ["sqlite_utils.db.Database.table", 3],
      },
    });
    expect(score(real, "vector_similarity")).toBe(0.7536);
    expect(score(real, "rerank_score")).toBeNull();
    expect(retrievedFor(real)).toEqual(["sqlite_utils.db.Database.table"]);
    expect(retrievedFor(item({}))).toEqual([]);
  });

  it("counts only the tokens of what was shown", () => {
    expect(tokensUsed([item({ token_cost: 202 }), item({ included: false, token_cost: 0 })])).toBe(
      202,
    );
  });
});

describe("the trace table's rows", () => {
  const rows = traceRows([
    item({ label: "N1", qualified_name: "a.load", included: true, token_cost: 300, stage_scores: { vector_rank: 2, vector_similarity: 0.7, retrieved_for: ["m.save"] } }),
    item({ label: "N2", qualified_name: "b.Store", included: true, truncated: true, token_cost: 40, stage_scores: { vector_rank: 1, vector_similarity: 0.9, retrieved_for: ["m.save", "m.load"] } }),
    item({ label: "N3", qualified_name: "c.helper", included: false, token_cost: 0, stage_scores: { vector_rank: 3, vector_similarity: 0.5, excluded: "over_budget", retrieved_for: ["m.load"] } }),
    item({ label: "N4", qualified_name: "d.other", included: false, token_cost: 0, stage_scores: {} }),
  ]);
  const labels = (r: typeof rows) => r.map((row) => row.item.label);

  it("sorts by each column, and puts rows without a value last either way", () => {
    expect(labels(sortRows(rows, "rank", "asc"))).toEqual(["N2", "N1", "N3", "N4"]);
    expect(labels(sortRows(rows, "rank", "desc"))).toEqual(["N3", "N1", "N2", "N4"]);
    expect(labels(sortRows(rows, "similarity", "desc"))).toEqual(["N2", "N1", "N3", "N4"]);
    // Tokens count only what the model was shown.
    expect(labels(sortRows(rows, "tokens", "desc"))).toEqual(["N1", "N2", "N3", "N4"]);
    expect(labels(sortRows(rows, "name", "asc"))).toEqual(["N1", "N2", "N3", "N4"]);
  });

  it("filters by outcome, by the changed code, and by name", () => {
    const none = new Set<"included" | "signature" | "excluded">();
    expect(labels(filterRows(rows, { outcomes: new Set(["excluded"]), seed: null, query: "" }))).toEqual(["N3", "N4"]);
    expect(labels(filterRows(rows, { outcomes: none, seed: "m.load", query: "" }))).toEqual(["N2", "N3"]);
    expect(labels(filterRows(rows, { outcomes: none, seed: null, query: " STORE " }))).toEqual(["N2"]);
    expect(filterRows(rows, { outcomes: none, seed: null, query: "" })).toHaveLength(4);
  });
});
