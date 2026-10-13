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
