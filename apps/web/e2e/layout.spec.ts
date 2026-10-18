import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/** Every page fits its screen (runs on desktop, tablet and phone): nothing
 * wider than the viewport, so the page never scrolls sideways. Wide content
 * (diffs, tables, the graph) scrolls inside its own box instead. */

const PAGES = [
  "/",
  "/overview",
  "/reviews",
  "/repos",
  "/repos/19",
  "/repos/19/indexing",
  "/reviews/1386",
  "/reviews/1386/graph",
  "/reviews/1386/trace",
  "/reviews/999999",
];

for (const path of PAGES) {
  test(`${path} doesn't scroll sideways`, async ({ page }) => {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test("the navigation is reachable on every screen size", async ({ page, isMobile }) => {
  await page.goto("/overview");
  const viewport = page.viewportSize();
  if (isMobile || (viewport && viewport.width < 768)) {
    // A phone gets a top bar whose menu opens the sidebar in a sheet.
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(page.getByRole("dialog").getByRole("link", { name: "Reviews" })).toBeVisible();
  } else {
    await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Reviews" })).toBeVisible();
  }
});

for (const path of ["/", "/overview", "/reviews/1386", "/reviews/1386/trace"]) {
  test(`${path}: buttons and links are big enough to tap`, async ({ page, hasTouch }) => {
    test.skip(!hasTouch, "a touch screen's concern");
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    // WCAG 2.2's minimum target size (2.5.8): 24 × 24 CSS px, or far enough
    // from its neighbours; axe measures both.
    const results = await new AxeBuilder({ page }).withRules(["target-size"]).analyze();
    const small = results.violations.flatMap((v) => v.nodes.map((n) => n.target.join(" ")));
    expect(small).toEqual([]);
  });
}
