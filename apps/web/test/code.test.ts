import { describe, expect, it } from "vitest";

import type { Finding } from "@/lib/api";
import { findingOrder, hunkTokens, langOf, plain, sides, type Token } from "@/lib/code";
import { parseDiff } from "@/lib/diff";
import { highlight, highlightDiff } from "@/lib/highlight";

const DIFF = [
  "diff --git a/shop/cart.py b/shop/cart.py",
  "--- a/shop/cart.py",
  "+++ b/shop/cart.py",
  "@@ -1,4 +1,4 @@",
  " def total(items):",
  '-    """Sum the prices."""',
  '+    """Sum the prices, with tax."""',
  "     return sum(i.price for i in items)",
  " ",
  "diff --git a/web/app.ts b/web/app.ts",
  "--- a/web/app.ts",
  "+++ b/web/app.ts",
  "@@ -1,1 +1,2 @@",
  " export const x = 1;",
  "+export const y = 2;",
  "",
].join("\n");

const text = (line: Token[]) => line.map((t) => t.text).join("");

function finding(id: number, file_path: string, line_start: number): Finding {
  return {
    id,
    file_path,
    line_start,
    line_end: line_start,
    severity: "warning",
    category: "bug",
    title: "t",
    explanation: "e",
    suggested_fix: null,
    evidence_node_ids: [],
    confidence: 0.9,
    status: "posted",
    drop_reason: null,
    votes: { up: 0, down: 0 },
    my_vote: null,
  };
}

describe("langOf", () => {
  it("knows the languages CodeLens reviews, by extension", () => {
    expect(langOf("shop/cart.py")).toBe("python");
    expect(langOf("src/App.TSX")).toBe("tsx");
    expect(langOf("lib/index.mjs")).toBe("javascript");
  });

  it("is null for anything else, including a dot in a folder name", () => {
    expect(langOf("README.md")).toBeNull();
    expect(langOf("Makefile")).toBeNull();
    expect(langOf("v1.2/Makefile")).toBeNull();
  });
});

describe("a hunk's two sides", () => {
  const [cart] = parseDiff(DIFF);
  const [hunk] = cart.hunks;

  it("splits into the old file's lines and the new file's", () => {
    const { old, new: now } = sides(hunk);
    expect(old.split("\n")[1]).toBe('    """Sum the prices."""');
    expect(now.split("\n")[1]).toBe('    """Sum the prices, with tax."""');
    expect(old.split("\n")).toHaveLength(4);
    expect(now.split("\n")).toHaveLength(4);
  });

  it("maps each side's tokens back onto the hunk's lines", () => {
    const { old, new: now } = sides(hunk);
    const lines = hunkTokens(hunk, plain(old), plain(now));
    expect(lines.map(text)).toEqual(hunk.lines.map((l) => l.text));
  });

  it("falls back to the plain line when a side comes up short", () => {
    const lines = hunkTokens(hunk, [], []);
    expect(lines.map(text)).toEqual(hunk.lines.map((l) => l.text));
  });
});

describe("findingOrder", () => {
  it("walks file by file in the diff's order, then by line; other files last", () => {
    const files = parseDiff(DIFF);
    const ordered = findingOrder(files, [
      finding(1, "web/app.ts", 2),
      finding(2, "elsewhere.py", 1),
      finding(3, "shop/cart.py", 3),
      finding(4, "shop/cart.py", 1),
    ]);
    expect(ordered.map((f) => f.id)).toEqual([4, 3, 1, 2]);
  });
});

describe("highlighting (Shiki)", () => {
  it("never changes the text, and colours it for both themes", async () => {
    const code = 'def f(x):\n    return "hi"  # done';
    const lines = await highlight(code, "python");
    expect(lines.map(text).join("\n")).toBe(code);
    const keyword = lines[1].find((t) => t.text === "return");
    expect(keyword?.light).toMatch(/^#/);
    expect(keyword?.dark).toMatch(/^#/);
    expect(keyword?.light).not.toBe(keyword?.dark);
  });

  it("leaves unknown languages plain", async () => {
    expect(await highlight("a\nb", null)).toEqual([[{ text: "a" }], [{ text: "b" }]]);
  });

  it("highlights a whole diff without changing a line", async () => {
    const files = parseDiff(DIFF);
    const tokens = await highlightDiff(files);
    files.forEach((file, f) =>
      file.hunks.forEach((hunk, h) =>
        expect(tokens[f][h].map(text)).toEqual(hunk.lines.map((l) => l.text)),
      ),
    );
  });
});
