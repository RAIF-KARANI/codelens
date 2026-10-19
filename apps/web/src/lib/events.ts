/** A review's timeline events (api/sse.py, core/review/recorder.py) in plain
 * words. Pure, so it's unit-tested. */

export type ReviewEvent = { id: string | null; type: string; data: Record<string, unknown> };

/** Every event name the API sends. An EventSource only delivers named events
 * it listens for, so the timeline subscribes to each. */
export const EVENT_TYPES = [
  "waiting_for_model",
  "stage",
  "chunk_done",
  "chunk_failed",
  "think_fallback",
  "verified",
  "done",
  "published",
  "superseded",
  "error",
  "end",
] as const;

export type Tone = "info" | "good" | "bad";

const num = (v: unknown): number => (typeof v === "number" ? v : 0);
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
/** Chunks are counted from 0 in the events, from 1 for people. */
const chunk = (data: Record<string, unknown>) => num(data.chunk) + 1;

export function describe(event: ReviewEvent): { text: string; tone: Tone } {
  const d = event.data;
  switch (event.type) {
    case "stage":
      if (d.stage === "seeds") {
        return {
          text: `Found ${plural(num(d.seeds), "changed definition")} in ${plural(num(d.files), "file")}`,
          tone: "info",
        };
      }
      if (d.stage === "packed") {
        const left = Array.isArray(d.left_out) ? d.left_out.length : 0;
        return {
          text:
            `Packed the change and its context into ${plural(num(d.chunks), "chunk")}` +
            (left ? `; ${plural(left, "file")} left out` : ""),
          tone: "info",
        };
      }
      if (d.stage === "generating") {
        return {
          text: `Reviewing chunk ${chunk(d)} of ${num(d.of)} (${num(d.prompt_tokens).toLocaleString("en")} prompt tokens)`,
          tone: "info",
        };
      }
      return { text: `Stage: ${String(d.stage)}`, tone: "info" };
    case "chunk_done":
      return {
        text: `Chunk ${chunk(d)} done in ${(num(d.llm_ms) / 1000).toFixed(1)} s: ${plural(num(d.findings), "finding")}, ${num(d.output_tokens).toLocaleString("en")} tokens written`,
        tone: "info",
      };
    case "chunk_failed":
      return { text: `Chunk ${chunk(d)} failed: ${String(d.error ?? "")}`, tone: "bad" };
    case "think_fallback":
      return { text: `Chunk ${chunk(d)}: thinking ran out, reviewed again without it`, tone: "info" };
    case "verified":
      return { text: "Verification pass finished", tone: "info" };
    case "done":
      return {
        text: `Validated: ${num(d.posted)} posted, ${num(d.dropped)} dropped`,
        tone: d.status === "completed" ? "good" : "bad",
      };
    case "published":
      return {
        text: `Posted on GitHub: ${num(d.new)} new, ${num(d.repeated)} already posted, ${num(d.inline)} inline`,
        tone: "good",
      };
    case "superseded":
      // By a newer review, or (a retry, D181) for a reason in words.
      return typeof d.why === "string"
        ? { text: `Not reviewed: ${d.why}`, tone: "info" }
        : { text: `Superseded by review #${num(d.by_review)}, not posted`, tone: "info" };
    case "waiting_for_model":
      return { text: "Waiting for Ollama to answer; trying again every 30 s", tone: "info" };
    case "error":
      return { text: `Failed: ${String(d.message ?? "")}`, tone: "bad" };
    case "end":
      return { text: `Finished (${String(d.status ?? "")})`, tone: d.status === "failed" ? "bad" : "good" };
    default:
      return { text: event.type, tone: "info" };
  }
}
