import { describe, expect, it } from "vitest";

import { readPending, sameState } from "@/lib/oauth";
import { safeNext } from "@/lib/paths";

describe("safeNext", () => {
  it("keeps a path on this site", () => {
    expect(safeNext("/repos/3?tab=reviews")).toBe("/repos/3?tab=reviews");
  });

  it.each([
    [null],
    [undefined],
    [""],
    ["https://evil.example/"],
    ["//evil.example"],
    ["/\\evil.example"],
    ["javascript:alert(1)"],
    ["repos"],
  ])("falls back to /repos for %j", (next) => {
    expect(safeNext(next)).toBe("/repos");
  });
});

describe("pending login", () => {
  it("reads what the login route stored", () => {
    expect(readPending(JSON.stringify({ state: "abc", next: "/repos" }))).toEqual({
      state: "abc",
      next: "/repos",
    });
  });

  it.each([[undefined], ["not json"], [JSON.stringify({ state: 1, next: "/" })]])(
    "is null for %j",
    (value) => {
      expect(readPending(value)).toBeNull();
    },
  );

  it("compares states exactly", () => {
    expect(sameState("abc", "abc")).toBe(true);
    expect(sameState("abc", "abd")).toBe(false);
    expect(sameState("abc", "ab")).toBe(false);
    expect(sameState("abc", null)).toBe(false);
  });
});
