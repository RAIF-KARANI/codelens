/** A review's events (lib/events.ts) as steps, for the live progress stepper.
 * Pure, so it's unit-tested. */

import type { ReviewEvent } from "@/lib/events";

export type StepKey = "changes" | "context" | "review" | "validate" | "post";
export type StepState = "done" | "current" | "todo" | "failed" | "skipped";
export type Step = { key: StepKey; label: string; detail?: string; state: StepState };

export type Progress = {
  steps: Step[];
  /** How it ended, once it has: the `end` event's status. */
  ended: string | null;
  /** Posted findings, once validated. */
  posted: number | null;
};

const num = (v: unknown): number => (typeof v === "number" ? v : 0);

/** Where a review is, from the events so far. `posts` is whether it ends by
 * posting on GitHub (a pull request's review); a CLI or eval review stops at
 * validation. A failure marks the step it happened in; a superseded review
 * skips what it won't do. */
export function progress(events: ReviewEvent[], posts: boolean): Progress {
  const has = (type: string, stage?: string) =>
    events.some((e) => e.type === type && (stage === undefined || e.data.stage === stage));

  const generating = events.filter((e) => e.type === "stage" && e.data.stage === "generating");
  const doneChunks = events.filter((e) => e.type === "chunk_done").length;
  const packed = events.find((e) => e.type === "stage" && e.data.stage === "packed");
  const chunks = packed ? num(packed.data.chunks) : num(generating.at(-1)?.data.of);
  const done = events.find((e) => e.type === "done");
  const end = events.find((e) => e.type === "end");
  const failed = has("error") || (end?.data.status === "failed");
  const superseded = has("superseded") || end?.data.status === "superseded";

  const reached: Record<StepKey, boolean> = {
    changes: has("stage", "seeds"),
    context: packed !== undefined,
    review: done !== undefined,
    validate: done !== undefined,
    post: has("published"),
  };

  const order: StepKey[] = posts
    ? ["changes", "context", "review", "validate", "post"]
    : ["changes", "context", "review", "validate"];
  const firstOpen = order.find((key) => !reached[key]);

  const reviewDetail =
    chunks > 0
      ? doneChunks >= chunks
        ? `${chunks} ${chunks === 1 ? "chunk" : "chunks"}`
        : `chunk ${Math.min(doneChunks + 1, chunks)} of ${chunks}`
      : undefined;

  const steps: Step[] = order.map((key) => {
    let state: StepState = reached[key] ? "done" : key === firstOpen ? "current" : "todo";
    if (state !== "done" && end) {
      // It ended before this step: where it stopped failed (or was dropped by
      // a newer push); the rest never ran.
      state = key === firstOpen ? (failed ? "failed" : "skipped") : "skipped";
    } else if (state === "current" && failed) {
      state = "failed";
    }
    if (state === "current" && superseded) state = "skipped";
    return { key, label: LABELS[key], detail: key === "review" ? reviewDetail : undefined, state };
  });

  return {
    steps,
    ended: end ? String(end.data.status ?? "") : null,
    posted: done ? num(done.data.posted) : null,
  };
}

const LABELS: Record<StepKey, string> = {
  changes: "Changes",
  context: "Context",
  review: "Review",
  validate: "Validate",
  post: "Post",
};

/** "0:07", "1:05", "12:40": a running clock, minutes and seconds. */
export function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
