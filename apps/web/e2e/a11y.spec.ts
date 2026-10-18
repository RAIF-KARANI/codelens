import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/** axe's WCAG 2.2 A and AA rules on every page, in both themes: contrast,
 * names, labels, landmarks, ARIA. It can't judge everything (whether a name
 * makes sense, focus order), which the smoke tests and a person cover. */

const PAGES = [
  ["landing", "/"],
  ["overview", "/overview"],
  ["reviews", "/reviews"],
  ["repos", "/repos"],
  ["repo", "/repos/19"],
  ["indexing", "/repos/19/indexing"],
  ["review", "/reviews/1386"],
  ["graph", "/reviews/1386/graph"],
  ["trace", "/reviews/1386/trace"],
  ["empty state", "/reviews?status=failed"],
  ["not found", "/reviews/999999"],
] as const;

for (const theme of ["light", "dark"] as const) {
  test.describe(`${theme} theme`, () => {
    test.use({ colorScheme: theme });

    for (const [name, path] of PAGES) {
      test(`${name} has no WCAG A/AA violations`, async ({ page }) => {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        const results = await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
          .analyze();
        // One line per violation and element, so a failure says what to fix.
        const found = results.violations.flatMap((v) =>
          v.nodes.map((n) => `${v.id}: ${n.target.join(" ")} — ${n.failureSummary?.split("\n")[1] ?? v.help}`),
        );
        expect(found).toEqual([]);
      });
    }
  });
}

test("every element Tab reaches shows that it has focus", async ({ page }) => {
  await page.goto("/reviews/1386");
  await page.waitForLoadState("networkidle");
  const unmarked: string[] = [];
  for (let i = 0; i < 30; i++) {
    await page.keyboard.press("Tab");
    const seen = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return null;
      const s = getComputedStyle(el);
      const outline = s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0;
      const ring = s.boxShadow !== "none";
      const name = (el.getAttribute("aria-label") ?? el.textContent ?? "").trim().slice(0, 40);
      return { marked: outline || ring, what: `${el.tagName.toLowerCase()} "${name}"` };
    });
    if (seen && !seen.marked) unmarked.push(seen.what);
  }
  expect(unmarked).toEqual([]);
});

test.describe("with reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("nothing keeps moving", async ({ page }) => {
    await page.goto("/overview");
    // A pulse, as a running review's badge has (the demo has none running).
    const duration = await page.evaluate(() => {
      const el = document.createElement("span");
      el.className = "animate-pulse";
      document.body.append(el);
      const s = getComputedStyle(el);
      return { duration: parseFloat(s.animationDuration), count: s.animationIterationCount };
    });
    expect(duration.duration).toBeLessThan(0.001);
    expect(duration.count).toBe("1");
  });
});
