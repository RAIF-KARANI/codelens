/** CodeLens Desktop's main process (planning-v2.md Phase 11 step 1b, the
 * spike): start the stack, give the window this launch's key, show the
 * dashboard, and stop the stack on quit. The tray, restarts and the wizard
 * come in later steps.
 *
 * Set CODELENS_MEASURE=1 to log the spike's numbers (time to a running stack,
 * memory) to logs/desktop.log, then quit after a minute idle. */

import { appendFile } from "node:fs/promises";
import { join } from "node:path";

import { app, BrowserWindow, type Session, session, shell } from "electron";

import {
  APP_DIR,
  codelensHome,
  containerMemory,
  ensureHome,
  imagesPresent,
  LOCAL_IMAGES,
  newLaunch,
  readImages,
  type Stack,
  stop,
  up,
} from "./stack.js";
import { APP_KEY_COOKIE } from "./key.js";
import { appOrigin, isAppUrl, isExternalLink, statusPage } from "./window.js";

const MEASURE = process.env.CODELENS_MEASURE === "1";
/** The spike's own measuring: how long after the dashboard loads to sample
 * memory, so start-up work has settled. */
const IDLE_MS = 60_000;

let stack: Stack | null = null;
let stopped = false;
const launchedAt = performance.now();

async function log(message: string): Promise<void> {
  const line = `${new Date().toISOString()} ${message}\n`;
  process.stdout.write(line);
  if (stack) await appendFile(join(stack.home, "logs", "desktop.log"), line).catch(() => {});
}

const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

/** One window, on its own session: the key cookie lives there, and nothing
 * else this app loads ever shares it. Sandboxed, with no Node in the page. */
function createWindow(ses: Session, port: () => number | null): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 600,
    title: "CodeLens",
    backgroundColor: "#131120",
    autoHideMenuBar: true,
    webPreferences: {
      session: ses,
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  // Only the dashboard navigates inside the window; links elsewhere open in
  // the user's browser, and only https ones.
  const guard = (event: { preventDefault(): void }, url: string) => {
    const current = port();
    if (current !== null && isAppUrl(url, current)) return;
    event.preventDefault();
    if (isExternalLink(url)) void shell.openExternal(url);
  };
  win.webContents.on("will-navigate", guard);
  win.webContents.on("will-redirect", guard);
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isExternalLink(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  return win;
}

async function start(): Promise<void> {
  const local = process.env.CODELENS_LOCAL_IMAGES === "1";
  // Installed, compose.yaml and images.json sit in resources/, outside the
  // app's archive, where docker can read them.
  const configDir = app.isPackaged ? process.resourcesPath : APP_DIR;
  const home = codelensHome();
  stack = {
    home,
    // CODELENS_LOCAL_IMAGES=1: a developer's images built from the checkout.
    images: local ? LOCAL_IMAGES : await readImages(join(configDir, "images.json")),
    build: local,
    configDir,
  };
  await ensureHome(home);

  let port: number | null = null;
  const ses = session.fromPartition("persist:codelens");
  // The dashboard asks for nothing (camera, notifications, ...): refuse all.
  ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  const win = createWindow(ses, () => port);
  win.on("closed", () => app.quit());

  const cached = await imagesPresent(stack.images);
  await win.loadURL(
    statusPage(
      "Starting CodeLens…",
      cached
        ? "Starting the database, the API, the workers and the dashboard."
        : "The first start downloads CodeLens's containers (about 500 MB). This takes a minute or a few, with your connection.",
    ),
  );

  const launch = await newLaunch();
  port = launch.port;
  const upStarted = performance.now();
  await log(`starting the stack on port ${launch.port} (images ${cached ? "cached" : "to pull"})`);
  const run = await up(stack, launch, (chunk) => void appendFile(join(home, "logs", "compose.log"), chunk));
  const upTook = performance.now() - upStarted;
  if (run.code !== 0) {
    await log(`docker compose up failed (exit ${run.code}) after ${seconds(upTook)}`);
    await win.loadURL(
      statusPage(
        "CodeLens couldn't start",
        `Is Docker Desktop running?\n\nDetails are in ${join(home, "logs", "compose.log")}`,
      ),
    );
    return;
  }

  // A session cookie (no expiry): the window holds it until it closes, and
  // the next launch brings a new key anyway (D147, D165).
  await ses.cookies.set({
    url: appOrigin(launch.port),
    name: APP_KEY_COOKIE,
    value: launch.appKey,
    httpOnly: true,
    sameSite: "strict",
  });
  await win.loadURL(`${appOrigin(launch.port)}/`);
  const total = performance.now() - launchedAt;
  await log(
    `dashboard shown: stack up in ${seconds(upTook)} (${cached ? "warm" : "cold, with pulls"}), ` +
      `${seconds(total)} from launch; page title "${win.getTitle()}"`,
  );

  if (MEASURE) {
    setTimeout(() => void measure(), IDLE_MS);
  }
}

/** The spike's idle-memory reading: the app's own processes (Electron's
 * working sets) and each container. */
async function measure(): Promise<void> {
  const electronMiB = app
    .getAppMetrics()
    .reduce((sum, process) => sum + process.memory.workingSetSize / 1024, 0);
  const containers = await containerMemory();
  const containersMiB = [...containers.values()].reduce((a, b) => a + b, 0);
  const each = [...containers].map(([name, mib]) => `${name} ${mib.toFixed(0)}`).join(", ");
  await log(
    `idle after ${IDLE_MS / 1000} s: app ${electronMiB.toFixed(0)} MiB, ` +
      `containers ${containersMiB.toFixed(0)} MiB (${each}), total ${(electronMiB + containersMiB).toFixed(0)} MiB`,
  );
  app.quit();
}

// One app at a time: a second launch focuses the first one's window.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const win = BrowserWindow.getAllWindows()[0];
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(start, async (error: unknown) => {
    await log(`start failed: ${error instanceof Error ? error.message : String(error)}`);
    app.quit();
  });

  // Quitting stops the stack first; the data stays for the next start.
  app.on("before-quit", (event) => {
    if (stopped || !stack) return;
    event.preventDefault();
    stopped = true;
    const started = performance.now();
    void stop(stack).then(async (run) => {
      await log(`stack stopped (exit ${run.code}) in ${seconds(performance.now() - started)}`);
      app.quit();
    });
  });
}
