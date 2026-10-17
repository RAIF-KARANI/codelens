/** The models CodeLens reviews with (planning-v2.md D146, Phase 11 step 5b),
 * pulled into the user's own Ollama through its API: POST /api/pull streams
 * one JSON line per change, per layer ("pulling <digest>", total, completed),
 * and Ollama checks each layer against its digest before it keeps it (D150:
 * models by Ollama's own digests). */

import { parseEnv } from "./stack.js";

export const OLLAMA = "http://127.0.0.1:11434";

/** What CodeLens reviews and embeds with: the defaults (core/config.py), or
 * what codelens.env sets instead. */
export function wantedModels(envText: string): string[] {
  const env = parseEnv(envText);
  return [env.get("LLM_MODEL") || "qwen3:8b", env.get("EMBED_MODEL") || "qwen3-embedding:0.6b"];
}

/** The wanted models Ollama doesn't have, from GET /api/tags. A name without
 * a tag is Ollama's `:latest`. */
export function missingModels(tags: unknown, wanted: string[]): string[] {
  const models = (tags as { models?: { name?: unknown }[] } | null)?.models ?? [];
  const have = new Set(models.map((m) => String(m.name ?? "")));
  return wanted.filter((name) => !have.has(name) && !have.has(`${name}:latest`));
}

export type PullProgress = { completed: number; total: number; status: string };

/** Adds up the layers seen so far: one bar for the whole model. */
export class PullTally {
  private layers = new Map<string, { completed: number; total: number }>();
  status = "";

  /** One line of /api/pull; throws on Ollama's `error`. */
  add(line: string): PullProgress {
    const event = JSON.parse(line) as { status?: string; digest?: string; total?: number; completed?: number; error?: string };
    if (event.error) throw new Error(event.error);
    this.status = event.status ?? this.status;
    if (event.digest && typeof event.total === "number") {
      this.layers.set(event.digest, { total: event.total, completed: event.completed ?? 0 });
    }
    let completed = 0;
    let total = 0;
    for (const layer of this.layers.values()) {
      completed += layer.completed;
      total += layer.total;
    }
    return { completed, total, status: this.status };
  }
}

/** Pulls one model, reporting progress; resolves once Ollama says success. */
export async function pullModel(
  name: string,
  onProgress: (progress: PullProgress) => void,
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  const response = await fetchImpl(`${OLLAMA}/api/pull`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: name, stream: true }),
  });
  if (!response.ok || !response.body) throw new Error(`Ollama refused to pull ${name}: HTTP ${response.status}`);
  const tally = new PullTally();
  const decoder = new TextDecoder();
  let buffer = "";
  let succeeded = false;
  const handle = (line: string) => {
    if (!line.trim()) return;
    const progress = tally.add(line);
    onProgress(progress);
    if (progress.status === "success") succeeded = true;
  };
  for await (const chunk of response.body as unknown as AsyncIterable<Uint8Array>) {
    buffer += decoder.decode(chunk, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    lines.forEach(handle);
  }
  handle(buffer); // a last line without its newline
  if (!succeeded) throw new Error(`pulling ${name} stopped before it finished`);
}

export async function ollamaTags(fetchImpl: typeof fetch = fetch): Promise<unknown> {
  const response = await fetchImpl(`${OLLAMA}/api/tags`, { signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error(`Ollama answered HTTP ${response.status}`);
  return response.json();
}
