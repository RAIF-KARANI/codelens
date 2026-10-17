import { describe, expect, it } from "vitest";

import type { ReviewEvent } from "@/lib/events";
import { clock, progress } from "@/lib/progress";

const e = (type: string, data: Record<string, unknown> = {}): ReviewEvent => ({ id: null, type, data });

const seeds = e("stage", { stage: "seeds", files: 2, seeds: 3 });
const packed = e("stage", { stage: "packed", chunks: 2 });
const gen = (chunk: number) => e("stage", { stage: "generating", chunk, of: 2 });
const chunkDone = (chunk: number) => e("chunk_done", { chunk, findings: 1 });

const states = (events: ReviewEvent[], posts = true) =>
  Object.fromEntries(progress(events, posts).steps.map((s) => [s.key, s.state]));

describe("progress", () => {
  it("starts on the first step", () => {
    expect(states([])).toEqual({
      changes: "current",
      context: "todo",
      review: "todo",
      validate: "todo",
      post: "todo",
    });
  });

  it("counts the chunk being reviewed", () => {
    const { steps } = progress([seeds, packed, gen(0), chunkDone(0), gen(1)], true);
    const review = steps.find((s) => s.key === "review")!;
    expect(review).toMatchObject({ state: "current", detail: "chunk 2 of 2" });
    expect(steps.find((s) => s.key === "context")!.state).toBe("done");
  });

  it("is done when posted and ended", () => {
    const events = [
      seeds,
      packed,
      gen(0),
      chunkDone(0),
      gen(1),
      chunkDone(1),
      e("done", { status: "completed", posted: 3, dropped: 1 }),
      e("published", { new: 3 }),
      e("end", { status: "completed" }),
    ];
    const result = progress(events, true);
    expect(Object.values(states(events))).toEqual(["done", "done", "done", "done", "done"]);
    expect(result).toMatchObject({ ended: "completed", posted: 3 });
    expect(result.steps.find((s) => s.key === "review")!.detail).toBe("2 chunks");
  });

  it("has no Post step for a review that doesn't post (CLI, eval)", () => {
    const events = [seeds, packed, e("done", { posted: 0 }), e("end", { status: "completed" })];
    expect(states(events, false)).toEqual({
      changes: "done",
      context: "done",
      review: "done",
      validate: "done",
    });
  });

  it("marks where a failure happened, and skips the rest", () => {
    const events = [seeds, packed, gen(0), e("error", { message: "boom" }), e("end", { status: "failed" })];
    expect(states(events)).toEqual({
      changes: "done",
      context: "done",
      review: "failed",
      validate: "skipped",
      post: "skipped",
    });
  });

  it("shows a failure as soon as it arrives, before the end", () => {
    expect(states([seeds, e("error", { message: "boom" })]).context).toBe("failed");
  });

  it("skips posting when a newer push supersedes the review", () => {
    const events = [
      seeds,
      packed,
      e("done", { posted: 2 }),
      e("superseded", { by_review: 9 }),
      e("end", { status: "superseded" }),
    ];
    expect(states(events)).toMatchObject({ validate: "done", post: "skipped" });
  });
});

describe("clock", () => {
  it.each([
    [0, "0:00"],
    [7_400, "0:07"],
    [65_000, "1:05"],
    [760_000, "12:40"],
    [-5, "0:00"],
  ])("%s ms → %s", (ms, text) => {
    expect(clock(ms)).toBe(text);
  });
});
