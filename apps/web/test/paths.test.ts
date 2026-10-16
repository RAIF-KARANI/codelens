import { describe, expect, it } from "vitest";

import { readPending, sameState } from "@/lib/oauth";
import { postLoginUrl, safeNext } from "@/lib/paths";

// Every way found to turn a "path" into another host (D128). The URL parser
// drops tab, LF and CR, and dot segments normalize, so most of these only become
// `//evil.example` after parsing.
const OFF_SITE = [
  "https://evil.example/",
  "//evil.example",
  "/\\evil.example",
  "javascript:alert(1)",
  "/\t/evil.example",
  "/\n/evil.example",
  "/\r/evil.example",
  "/%09/evil.example",
  "/.//evil.example",
  "/..//evil.example",
  "/x/..//evil.example",
  "/%2e//evil.example",
  "/\u0000/evil.example",
];

describe("safeNext", () => {
  it("keeps a path on this site", () => {
    expect(safeNext("/repos/3?tab=reviews")).toBe("/repos/3?tab=reviews");
    expect(safeNext("/reviews/7#finding-2")).toBe("/reviews/7#finding-2");
  });

  const inputs: [string | null | undefined][] = [
    [null],
    [undefined],
    [""],
    ["repos"],
    ...OFF_SITE.map((next): [string] => [next]),
  ];

  it.each(inputs)(
    "keeps %j on this site",
    (next) => {
      // Most fall back to /repos. `/%09/evil.example` is harmless as raw text (the
      // parser keeps %09 encoded), so it stays a path; it just can't leave the site.
      const result = safeNext(next);
      expect(result === "/repos" || !result.startsWith("//")).toBe(true);
      expect(new URL(result, "https://dash.example").origin).toBe("https://dash.example");
    },
  );

  it("rejects tab, newline and dot-segment tricks outright", () => {
    for (const next of ["/\t/evil.example", "/\n/evil.example", "/\r/evil.example", "/.//evil.example"]) {
      expect(safeNext(next)).toBe("/repos");
    }
  });
});

describe("postLoginUrl", () => {
  const appUrl = "https://dash.example";

  it("stays on the dashboard for every off-site trick", () => {
    for (const next of OFF_SITE) {
      expect(postLoginUrl(next, appUrl).origin).toBe(appUrl);
    }
  });

  it("follows the dashboard's own scheme and host", () => {
    expect(postLoginUrl("/repos/3", "http://localhost:3000").href).toBe(
      "http://localhost:3000/repos/3",
    );
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
