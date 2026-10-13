import { describe, expect, it } from "vitest";

import type { TraceItem } from "@/lib/api";
import { outcome, retrievedFor, score, tokensUsed } from "@/lib/trace";

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
