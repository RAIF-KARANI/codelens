import { describe as group, expect, it } from "vitest";

import { describe } from "@/lib/events";

// The events of a real review (1380), as the API sent them.
const REAL = [
  { type: "stage", data: { files: 4, seeds: 4, stage: "seeds" } },
  { type: "stage", data: { stage: "packed", chunks: 1, left_out: [] } },
  { type: "stage", data: { of: 1, chunk: 0, stage: "generating", prompt_tokens: 4957 } },
  { type: "chunk_done", data: { chunk: 0, llm_ms: 21461, findings: 4, output_tokens: 558 } },
  { type: "done", data: { posted: 4, status: "completed", dropped: 0 } },
  { type: "published", data: { new: 4, inline: 4, repeated: 0 } },
  { type: "end", data: { status: "completed" } },
];

group("describe", () => {
  it("tells a review's story in plain words", () => {
    expect(REAL.map((e) => describe({ id: null, ...e }).text)).toEqual([
      "Found 4 changed definitions in 4 files",
      "Packed the change and its context into 1 chunk",
      "Reviewing chunk 1 of 1 (4,957 prompt tokens)",
      "Chunk 1 done in 21.5 s: 4 findings, 558 tokens written",
      "Validated: 4 posted, 0 dropped",
      "Posted on GitHub: 4 new, 0 already posted, 4 inline",
      "Finished (completed)",
    ]);
  });

  it("marks failures", () => {
    expect(describe({ id: "3", type: "error", data: { message: "boom" } })).toEqual({
      text: "Failed: boom",
      tone: "bad",
    });
    expect(describe({ id: null, type: "end", data: { status: "failed" } }).tone).toBe("bad");
    // Step 8 (D181): a review waiting for Ollama, and a retry that stood down.
    expect(describe({ id: "1", type: "waiting_for_model", data: {} }).text).toContain("Waiting for Ollama");
    expect(describe({ id: "2", type: "superseded", data: { why: "octo/shop#1 is closed" } }).text).toBe(
      "Not reviewed: octo/shop#1 is closed",
    );
  });

  it("survives fields it doesn't expect", () => {
    expect(describe({ id: null, type: "stage", data: { stage: "packed" } }).text).toBe(
      "Packed the change and its context into 0 chunks",
    );
    expect(describe({ id: null, type: "something_new", data: {} }).text).toBe("something_new");
  });
});
