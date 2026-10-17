import { describe, expect, it } from "vitest";

import { missingModels, type PullProgress, PullTally, pullModel, wantedModels } from "../src/models.js";

describe("the models", () => {
  it("are CodeLens's defaults unless codelens.env says otherwise", () => {
    expect(wantedModels("API_JWT_SECRET=x\n")).toEqual(["qwen3:8b", "qwen3-embedding:0.6b"]);
    expect(wantedModels("LLM_MODEL=qwen3:14b\r\n")).toEqual(["qwen3:14b", "qwen3-embedding:0.6b"]);
  });

  it("are missing unless Ollama lists them (a bare name is :latest)", () => {
    const tags = { models: [{ name: "qwen3:8b" }, { name: "llama3.2:latest" }] };
    expect(missingModels(tags, ["qwen3:8b", "qwen3-embedding:0.6b", "llama3.2"])).toEqual(["qwen3-embedding:0.6b"]);
    expect(missingModels({}, ["qwen3:8b"])).toEqual(["qwen3:8b"]);
    expect(missingModels(null, ["qwen3:8b"])).toEqual(["qwen3:8b"]);
  });
});

describe("PullTally", () => {
  it("adds up every layer into one bar", () => {
    const tally = new PullTally();
    expect(tally.add('{"status":"pulling manifest"}')).toEqual({ completed: 0, total: 0, status: "pulling manifest" });
    tally.add('{"status":"pulling a1","digest":"sha256:a1","total":100,"completed":40}');
    expect(tally.add('{"status":"pulling b2","digest":"sha256:b2","total":300,"completed":60}')).toEqual({
      completed: 100,
      total: 400,
      status: "pulling b2",
    });
    expect(tally.add('{"status":"pulling a1","digest":"sha256:a1","total":100,"completed":100}').completed).toBe(160);
    expect(tally.add('{"status":"success"}').status).toBe("success");
  });

  it("stops at Ollama's error", () => {
    expect(() => new PullTally().add('{"error":"pull model manifest: file does not exist"}')).toThrow(/does not exist/);
  });
});

describe("pullModel", () => {
  const stream = (lines: string[], chunkAt = 7) => {
    const text = lines.join("\n");
    const bytes = new TextEncoder().encode(text);
    const chunks: Uint8Array[] = [];
    for (let i = 0; i < bytes.length; i += chunkAt) chunks.push(bytes.slice(i, i + chunkAt));
    return (async (url: string, init: RequestInit) => {
      expect(url).toBe("http://127.0.0.1:11434/api/pull");
      expect(JSON.parse(String(init.body))).toEqual({ model: "qwen3:8b", stream: true });
      return new Response(new ReadableStream({ start(c) { for (const chunk of chunks) c.enqueue(chunk); c.close(); } }));
    }) as unknown as typeof fetch;
  };

  it("reports progress across chunk boundaries and finishes on success", async () => {
    const seen: PullProgress[] = [];
    await pullModel(
      "qwen3:8b",
      (p) => seen.push(p),
      stream([
        '{"status":"pulling manifest"}',
        '{"status":"pulling a1","digest":"sha256:a1","total":10,"completed":5}',
        '{"status":"pulling a1","digest":"sha256:a1","total":10,"completed":10}',
        '{"status":"verifying sha256 digest"}',
        '{"status":"success"}',
      ]),
    );
    expect(seen.map((p) => p.completed)).toEqual([0, 5, 10, 10, 10]);
    expect(seen.at(-1)?.status).toBe("success");
  });

  it("fails when the stream ends without success", async () => {
    await expect(
      pullModel("qwen3:8b", () => {}, stream(['{"status":"pulling a1","digest":"sha256:a1","total":10,"completed":5}'])),
    ).rejects.toThrow(/stopped before it finished/);
  });
});
