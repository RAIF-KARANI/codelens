import { describe, expect, it } from "vitest";

import { reindexBusy, STALE_CLAIM_MS } from "@/lib/reindex";

describe("reindexBusy", () => {
  const now = Date.UTC(2026, 9, 17, 12, 0, 0);
  const ago = (ms: number) => new Date(now - ms).toISOString();

  it.each([
    ["pending", "Queued…"],
    ["indexing", "Indexing…"],
  ] as const)("a fresh %s claim is busy", (status, label) => {
    expect(reindexBusy({ status, updated_at: ago(60_000) }, now)).toBe(label);
    expect(reindexBusy({ status, updated_at: ago(STALE_CLAIM_MS) }, now)).toBe(label);
  });

  it.each(["pending", "indexing"] as const)(
    "a %s claim older than the API's STALE_CLAIM can be retaken (D163)",
    (status) => {
      expect(reindexBusy({ status, updated_at: ago(STALE_CLAIM_MS + 1000) }, now)).toBeNull();
    },
  );

  it.each(["ready", "failed"] as const)("a %s repo can be re-indexed", (status) => {
    expect(reindexBusy({ status, updated_at: ago(0) }, now)).toBeNull();
  });
});
