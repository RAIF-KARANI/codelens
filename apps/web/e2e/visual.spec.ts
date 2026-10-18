import { expect, type Page, test } from "@playwright/test";

/** The main pages compared with stored screenshots (e2e/__screenshots__), so
 * a change to how something looks is seen in review, not found later. Fonts
 * render differently on each OS, so the stored ones come from Linux, in the
 * Playwright image CI runs in; e2e/README.md says how to update them. */

test.skip(
  process.platform !== "linux" && !process.env.E2E_BROWSER_WS,
  "screenshots are compared on Linux only: run them in CI, or with the browser in Docker (e2e/README.md)",
);

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  // Web fonts in, so text doesn't reflow after the shot.
  await page.evaluate(() => document.fonts.ready);
}

const SHOTS = [
  ["overview", "/overview"],
  ["review", "/reviews/1386"],
  ["graph", "/reviews/1386/graph?finding=1381"],
  ["trace", "/reviews/1386/trace"],
  ["repo-indexing", "/repos/19/indexing"],
] as const;

for (const [name, path] of SHOTS) {
  test(`${name} looks as it did`, async ({ page }, info) => {
    test.skip(info.project.name === "phone" && (name === "graph" || name === "trace"), "desktop views");
    await page.goto(path);
    await settle(page);
    await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: name !== "graph" });
  });
}

test.describe("dark theme", () => {
  test.use({ colorScheme: "dark" });

  for (const [name, path] of [SHOTS[0], SHOTS[1]]) {
    test(`${name} looks as it did`, async ({ page }) => {
      await page.goto(path);
      await settle(page);
      await expect(page).toHaveScreenshot(`${name}-dark.png`, { fullPage: true });
    });
  }
});
