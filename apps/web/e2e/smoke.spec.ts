import { expect, test } from "@playwright/test";

/** Every page loads with the demo's data, and the main interactions work.
 * The ids are the demo export's (e2e/fixtures): repo 19, reviews 1384–1386. */

test("the overview shows the last 7 days and the latest reviews", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
  await expect(page.getByText("Findings posted")).toBeVisible();
  await expect(page.getByRole("link", { name: "Cleanup pass across the library" })).toBeVisible();
});

test("the reviews list filters by status, and says when nothing matches", async ({ page }) => {
  await page.goto("/reviews");
  await expect(page.getByRole("link", { name: "Simplify the trigger lookup query" })).toBeVisible();

  await page.getByRole("link", { name: "failed", exact: true }).click();
  await expect(page).toHaveURL(/status=failed/);
  await expect(page.getByText("No failed reviews")).toBeVisible();

  await page.getByRole("link", { name: "Show all reviews" }).click();
  await expect(page.getByRole("link", { name: "Simplify the trigger lookup query" })).toBeVisible();
});

test("a repo shows its reviews and its index, and a demo can't re-index", async ({ page }) => {
  await page.goto("/repos/19");
  await expect(page.getByRole("heading", { name: "RAIF-KARANI/codelens-demo" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Re-index" })).toHaveCount(0);

  await page.getByRole("link", { name: "Indexing" }).click();
  await expect(page.getByText("Definitions").first()).toBeVisible();
  await expect(page.getByRole("meter", { name: "Calls resolved" })).toBeVisible();
});

test("a review lists its findings, and j/k move between them", async ({ page }) => {
  await page.goto("/reviews/1386");
  const findings = page.getByRole("button", { name: /^(critical|warning|suggestion): / });
  await expect(findings).toHaveCount(4);

  await page.locator("body").press("j");
  const current = page.locator('button[aria-current="true"]');
  await expect(current).toHaveCount(1);
  const first = await current.textContent();
  await page.locator("body").press("j");
  await expect(current).not.toHaveText(first ?? "");
  await page.locator("body").press("k");
  await expect(current).toHaveText(first ?? "");
});

test("the graph opens a node's code with Enter, and Escape gives focus back", async ({ page }) => {
  await page.goto("/reviews/1386/graph");
  // A code node, not a column's heading.
  const node = page.locator(".react-flow__node-code").first();
  await expect(node).toBeVisible();

  await node.focus();
  await page.keyboard.press("Enter");
  const sheet = page.getByRole("dialog");
  await expect(sheet).toBeVisible();
  // React Flow's own Enter handling once closed the sheet straight away (D160).
  await page.waitForTimeout(300);
  await expect(sheet).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(node).toBeFocused();
});

test("the trace lists what was retrieved, sortable", async ({ page }) => {
  await page.goto("/reviews/1386/trace");
  const table = page.getByRole("table").first();
  await expect(table).toBeVisible();
  const rows = table.locator("tbody tr");
  expect(await rows.count()).toBeGreaterThan(1);

  const header = table.locator("th", { has: page.getByRole("button", { name: /similarity/i }) });
  await header.getByRole("button").click();
  await expect(header).toHaveAttribute("aria-sort", /ascending|descending/);
});

test("the command palette goes to a review", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Control+k");
  const palette = page.getByRole("dialog");
  await expect(palette).toBeVisible();
  await palette.getByRole("combobox").fill("Cleanup pass");
  // Its list loads the first time it opens.
  await expect(palette.getByRole("option", { name: /Cleanup pass/ }).first()).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/reviews\/1386$/);
});

test("the theme choice applies at once and survives a reload", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Theme" }).first().click();
  await page.getByRole("menuitemradio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

// The status stays 200: the page streams, so its headers have gone before the
// review turns out not to exist (Next adds a noindex tag instead).
test("a review that doesn't exist says so", async ({ page }) => {
  await page.goto("/reviews/999999");
  await expect(page.getByText("Not found", { exact: true })).toBeVisible();
  await expect(page.getByText("isn't part of the demo")).toBeVisible();
});

// The download page (D179): reached from the demo's sidebar and its notice.
// The button goes to the latest release's installer on GitHub, or, if GitHub
// didn't answer when the page was built, to the Releases page.
test("the demo leads to the download, and the download to GitHub", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "download CodeLens Desktop" }).click();
  await expect(page).toHaveURL(/\/download$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("on your own PC");
  const download = page.getByRole("link", { name: /^Download (CodeLens \d+\.\d+\.\d+|from GitHub)$/ });
  await expect(download).toHaveAttribute(
    "href",
    /^https:\/\/github\.com\/RAIF-KARANI\/codelens\/releases\/(download\/v[\d.]+\/CodeLens-Setup-[\d.]+\.exe|latest)$/,
  );
  await expect(page.getByRole("link", { name: "Download", exact: true })).toBeVisible();
});

// Settings belong to CodeLens Desktop's local mode (D175): the demo (and the
// self-hosted dashboard) neither link to them nor serve them.
test("there are no Settings or System pages outside the desktop app", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Repositories" }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Settings" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "System" })).toHaveCount(0);
  for (const path of ["/settings", "/system"]) {
    await page.goto(path);
    await expect(page.getByText("Not found", { exact: true })).toBeVisible();
  }
});
