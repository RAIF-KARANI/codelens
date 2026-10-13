import { describe, expect, it } from "vitest";

import { filePath, parseDiff, placeFinding } from "@/lib/diff";

const DIFF = `diff --git a/shop/pricing.py b/shop/pricing.py
index 1111111..2222222 100644
--- a/shop/pricing.py
+++ b/shop/pricing.py
@@ -5,6 +5,7 @@ def total(items):
 def apply_discount(price, rate):
-    return price * rate
+    if rate > 1:
+        raise ValueError(rate)
+    return price * (1 - rate)


 def tax(price):
@@ -40,3 +41,3 @@ class Cart:
     def count(self):
-        return len(self.items)
+        return len(self.items) + 0
     # end
\\ No newline at end of file
diff --git a/shop/new.py b/shop/new.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/shop/new.py
@@ -0,0 +1,2 @@
+def hello():
+    return 1
diff --git a/old.py b/old.py
deleted file mode 100644
index 4444444..0000000
--- a/old.py
+++ /dev/null
@@ -1 +0,0 @@
-x = 1
diff --git a/logo.png b/logo.png
index 5555555..6666666 100644
Binary files a/logo.png and b/logo.png differ
diff --git a/a.py b/b.py
similarity index 90%
rename from a.py
rename to b.py
`;

describe("parseDiff", () => {
  const files = parseDiff(DIFF);

  it("finds every file with its status and paths", () => {
    expect(files.map((f) => [filePath(f), f.status, f.binary])).toEqual([
      ["shop/pricing.py", "modified", false],
      ["shop/new.py", "added", false],
      ["old.py", "deleted", false],
      ["logo.png", "modified", true],
      ["b.py", "renamed", false],
    ]);
    expect(files[1].oldPath).toBeNull();
    expect(files[2].newPath).toBeNull();
  });

  it("numbers old and new lines through each hunk", () => {
    const [first, second] = files[0].hunks;
    expect(first.header).toBe("def total(items):");
    expect(first.lines.map((l) => [l.kind, l.oldNo, l.newNo])).toEqual([
      ["context", 5, 5],
      ["del", 6, null],
      ["add", null, 6],
      ["add", null, 7],
      ["add", null, 8],
      ["context", 7, 9],
      ["context", 8, 10],
      ["context", 9, 11],
    ]);
    expect(first.lines[2].text).toBe("    if rate > 1:");
    // Each hunk restarts from its own header; "\ No newline" isn't a line.
    expect(second.lines.map((l) => [l.kind, l.oldNo, l.newNo])).toEqual([
      ["context", 40, 41],
      ["del", 41, null],
      ["add", null, 42],
      ["context", 42, 43],
    ]);
  });

  it("is empty for an empty diff", () => {
    expect(parseDiff("")).toEqual([]);
  });
});

describe("placeFinding", () => {
  const [pricing] = parseDiff(DIFF);

  it("anchors after the finding's last line", () => {
    expect(placeFinding(pricing, { lineStart: 6, lineEnd: 7 })).toEqual({
      anchor: 7,
      lines: [6, 7],
    });
  });

  it("clips a range wider than the hunk to the hunk (D109)", () => {
    // The model flagged the whole function, 1–30; the hunk shows 5–11.
    expect(placeFinding(pricing, { lineStart: 1, lineEnd: 30 })).toEqual({
      anchor: 11,
      lines: [5, 11],
    });
  });

  it("uses the first hunk the range overlaps", () => {
    expect(placeFinding(pricing, { lineStart: 42, lineEnd: 42 })?.anchor).toBe(42);
  });

  it("is null outside every hunk", () => {
    expect(placeFinding(pricing, { lineStart: 20, lineEnd: 30 })).toBeNull();
  });
});
