import { randomBytes } from "node:crypto";
import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

/** End-to-end tests (planning-v2.md Phase 10 step 8, D162): the dashboard,
 * built for production, against the API in demo mode over the demo export
 * (e2e/fixtures, loaded by scripts/e2e_data.py). Playwright starts both
 * servers; the database must already be loaded.
 *
 *   E2E_DATABASE_URL  the loaded database (default: CI's)
 *   E2E_PYTHON        how to run Python with the repo's packages (default: uv run python)
 *   E2E_BROWSER_WS    a browser in Playwright's Linux image (`playwright run-server`),
 *                     so screenshots match CI's from Windows or macOS (e2e/README.md)
 */

const REPO_ROOT = path.resolve(__dirname, "../..");
const API_PORT = 8002;
const WEB_PORT = 3100;

// A throwaway secret per run: both servers get it from here, so nothing is
// stored or shared. (Only the main process starts servers.)
const API_JWT_SECRET = process.env.E2E_API_JWT_SECRET ?? randomBytes(32).toString("base64url");
process.env.E2E_API_JWT_SECRET = API_JWT_SECRET;

const DATABASE_URL =
  process.env.E2E_DATABASE_URL ??
  "postgresql+asyncpg://codelens:codelens@localhost:5433/codelens_e2e";
const PYTHON = process.env.E2E_PYTHON ?? "uv run python";
const BROWSER_WS = process.env.E2E_BROWSER_WS;

// Dates are rendered on the server: one timezone for both sides, so the same
// review reads the same everywhere.
const TZ = "UTC";

export default defineConfig({
  testDir: "e2e",
  // Screenshots are compared against images rendered on Linux (CI), in one
  // folder per spec; e2e/README.md says how to update them.
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFilePath}/{arg}-{projectName}{ext}",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    // A browser in Docker reaches this machine's servers by the host's name.
    baseURL: `http://${BROWSER_WS ? "host.docker.internal" : "localhost"}:${WEB_PORT}`,
    connectOptions: BROWSER_WS ? { wsEndpoint: BROWSER_WS } : undefined,
    locale: "en-US",
    timezoneId: TZ,
    colorScheme: "light",
    trace: "retain-on-failure",
  },
  expect: {
    toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: "disabled", caret: "hide" },
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } },
    {
      name: "tablet",
      use: { ...devices["Desktop Chrome"], viewport: { width: 820, height: 1180 }, hasTouch: true },
      testMatch: ["layout.spec.ts"],
    },
    {
      name: "phone",
      use: { ...devices["Pixel 7"] },
      testMatch: ["layout.spec.ts", "visual.spec.ts"],
    },
    {
      // README_SHOTS=1 pnpm e2e --project=readme: the README's images, 2240 ×
      // 1600. A whole-number scale: a fractional one spaces letters unevenly.
      name: "readme",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1120, height: 800 }, deviceScaleFactor: 2 },
      testMatch: ["readme.shots.ts"],
    },
  ],
  webServer: [
    {
      command: `${PYTHON} -m uvicorn api.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: REPO_ROOT,
      url: `http://127.0.0.1:${API_PORT}/health`,
      env: { ...process.env, DATABASE_URL, DEMO_MODE: "true", API_JWT_SECRET, TZ } as Record<string, string>,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      // Built here rather than in CI's own step, so the build always has the
      // demo's settings.
      command: `pnpm build && pnpm start --port ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}`,
      env: {
        ...process.env,
        DEMO_MODE: "true",
        API_URL: `http://127.0.0.1:${API_PORT}`,
        API_JWT_SECRET,
        TZ,
      } as Record<string, string>,
      reuseExistingServer: false,
      timeout: 300_000,
    },
  ],
});
