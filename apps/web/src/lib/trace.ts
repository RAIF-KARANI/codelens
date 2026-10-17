/** Reading a review's retrieval trace (api: GET /reviews/{id}/trace): what
 * happened to each candidate, in plain words. Pure, so it's unit-tested. */

import type { TraceItem } from "@/lib/api";

export type Outcome = { kind: "included" | "signature" | "excluded"; text: string };

/** Why the packer left a candidate out (core/review/packing.py). */
const EXCLUSIONS: Record<string, string> = {
  over_budget: "didn't fit the context budget",
  // A stored class or module of an edited file holds the *old* changed code.
  contains_changed_code: "holds the old version of the changed code",
  context_window: "cut so the prompt fits the model's window",
};

export function outcome(item: TraceItem): Outcome {
  if (item.included) {
    return item.truncated
      ? { kind: "signature", text: "signature only (the full code didn't fit)" }
      : { kind: "included", text: "shown in full" };
  }
  const reason = item.stage_scores.excluded;
  const text =
    typeof reason === "string" ? (EXCLUSIONS[reason] ?? reason.replaceAll("_", " ")) : "not shown";
  return { kind: "excluded", text };
}

/** The changed code a candidate was retrieved for. */
export function retrievedFor(item: TraceItem): string[] {
  const value = item.stage_scores.retrieved_for;
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

export function score(item: TraceItem, name: string): number | null {
  const value = item.stage_scores[name];
  return typeof value === "number" ? value : null;
}

/** Tokens of context actually spent per chunk, against the budget. */
export function tokensUsed(items: TraceItem[]): number {
  return items.reduce((sum, item) => sum + (item.included ? item.token_cost : 0), 0);
}

/** One candidate as the trace table shows it: one row per candidate, with the
 * changed code it was retrieved for (several, when more than one seed found it). */
export type TraceRow = {
  item: TraceItem;
  rank: number | null;
  similarity: number | null;
  outcome: Outcome;
  forSeeds: string[];
};

export function traceRows(items: TraceItem[]): TraceRow[] {
  return items.map((item) => ({
    item,
    rank: score(item, "vector_rank"),
    similarity: score(item, "vector_similarity"),
    outcome: outcome(item),
    forSeeds: retrievedFor(item),
  }));
}

export type SortKey = "rank" | "similarity" | "tokens" | "name";
export type SortDir = "asc" | "desc";

/** The direction a column sorts in when first clicked: best first. */
export const FIRST_DIR: Record<SortKey, SortDir> = {
  rank: "asc",
  similarity: "desc",
  tokens: "desc",
  name: "asc",
};

/** Sorted by `key`; rows without a value (no rank, no similarity) always last,
 * and ties broken by rank, so the order never jumps between renders. */
export function sortRows(rows: TraceRow[], key: SortKey, dir: SortDir): TraceRow[] {
  const value = (r: TraceRow): number | string | null =>
    key === "rank"
      ? r.rank
      : key === "similarity"
        ? r.similarity
        : key === "tokens"
          ? r.item.included
            ? r.item.token_cost
            : null
          : r.item.qualified_name;
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    if (va === null || vb === null) {
      if (va !== vb) return va === null ? 1 : -1;
    } else if (va !== vb) {
      return (typeof va === "string" ? va.localeCompare(vb as string) : va - (vb as number)) * sign;
    }
    return (a.rank ?? Infinity) - (b.rank ?? Infinity);
  });
}

export type RowFilter = {
  /** Outcomes to keep; empty keeps all. */
  outcomes: ReadonlySet<Outcome["kind"]>;
  /** Only candidates retrieved for this changed code. */
  seed: string | null;
  /** Part of the candidate's name, any case. */
  query: string;
};

export function filterRows(rows: TraceRow[], filter: RowFilter): TraceRow[] {
  const query = filter.query.trim().toLowerCase();
  return rows.filter(
    (r) =>
      (filter.outcomes.size === 0 || filter.outcomes.has(r.outcome.kind)) &&
      (filter.seed === null || r.forSeeds.includes(filter.seed)) &&
      (query === "" || r.item.qualified_name.toLowerCase().includes(query)),
  );
}
