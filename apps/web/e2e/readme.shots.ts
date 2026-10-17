import path from "node:path";

import { type Page, test } from "@playwright/test";

/** The README's screenshots (docs/images), taken from the demo export, so they
 * show exactly what the live demo shows. Not a test: it only runs when asked,
 *
 *   README_SHOTS=1 pnpm e2e --project=readme
 *
 * and writes over the images, which are then committed. */

const OUT = path.resolve(__dirname, "../../../docs/images");

test.skip(!process.env.README_SHOTS, "only when refreshing the README (README_SHOTS=1)");

async function shoot(page: Page, url: string, name: string) {
  await page.goto(url);
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
}

test("overview", async ({ page }) => {
  await shoot(page, "/", "overview");
});

test("review", async ({ page }) => {
  await page.goto("/reviews/1384");
  await page.waitForLoadState("networkidle");
  // The finding selected, as after pressing j: its line in view, highlighted.
  await page.locator("body").press("j");
  await page.waitForTimeout(400);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(OUT, "review.png") });
});

test("graph", async ({ page }) => {
  await shoot(page, "/reviews/1386/graph?finding=1381", "graph");
});

test("trace", async ({ page }) => {
  await shoot(page, "/reviews/1386/trace", "trace");
});
