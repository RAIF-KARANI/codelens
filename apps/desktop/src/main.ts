/** CodeLens Desktop's main process (planning-v2.md Phase 11).
 *
 * Starts the stack in order (Docker answering, then `compose up --wait`, then
 * the dashboard), gives the window this launch's key (D165), and then keeps it
 * all up: every TICK_MS the supervisor looks at Docker, each service and
 * Ollama, brings back what stopped, and says what it can't fix (step 4). It
 * lives in the tray: closing the window keeps reviews going; Quit stops the
 * stack.
 *
 * CODELENS_MEASURE=1 logs the spike's numbers (time to a running stack,
 * memory) to logs/desktop.log, then quits after a minute idle (step 1b).
 *
 * Updates (step 7b): looked for on start and daily, installed only after the
 * user's yes, with the database backed up first if the new version migrates
 * it (updates.ts, backup.ts).
 *
 * `--uninstall` is the uninstaller's last call on the app (uninstall.ts,
 * build/installer.nsh): it removes the stack's containers, and the data if
 * the user says so, then exits; no window, no tray. */

import { type ChildProcess, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { appendFile, readFile, rm, statfs, writeFile } from "node:fs/promises";
import { tmpdir, userInfo } from "node:os";
import { join } from "node:path";

import electronUpdater from "electron-updater";
import {
  app,
  BrowserWindow,
  dialog,
  Menu,
  nativeImage,
  Notification,
  type Session,
  session,
  shell,
  Tray,
} from "electron";

import { APP_KEY_COOKIE } from "./key.js";
import { uninstall } from "./uninstall.js";
import { backupBeforeMigrating, removeStaleImages } from "./backup.js";
import { CHECK_EVERY_MS, Updates } from "./updates.js";
import { ServiceLogs, stopLeftoverFollowers } from "./logs.js";
import { applyChanges, checkChanges, envChanges, manageUrl, readSettings, type SettingsView, WINDOWS } from "./appsettings.js";
import { startControl } from "./control.js";
import { firstRun } from "./firstrun.js";
import { appName, createApp, hasApp, runDoctor } from "./githubapp.js";
import {
  dockerCli,
  dockerDesktopExe,
  download,
  isOllamaInstalled,
  launch as launchProgram,
  ollamaAppExe,
  readSignature,
  resumeAfterRestart,
  runInstaller,
} from "./installers.js";
import { machineChecks } from "./machine.js";
import { missingModels, ollamaTags, pullModel, wantedModels } from "./models.js";
import { prepareOllamaForLong, readyForLong, userEnvironment } from "./ollamaenv.js";
import { folderBytes, type SystemView, tailFile } from "./system.js";
import { DEFAULTS, type DesktopSettings, loadSettings, saveSettings } from "./settings.js";
import {
  APP_DIR,
  codelensHome,
  composeArgs,
  composeEnv,
  containerMemory,
  docker,
  dockerBin,
  dockerState,
  downProject,
  ensureHome,
  imagesPresent,
  type Launch,
  LOCAL_IMAGES,
  newLaunch,
  newSecret,
  PROJECT,
  readImages,
  restartServices,
  serviceStates,
  type Stack,
  stop,
  stopServices,
  up,
  upServices,
  useDocker,
} from "./stack.js";
import {
  Backoff,
  changes,
  type Health,
  isVolumeFromAnotherInstall,
  PAUSABLE,
  parseServiceStates,
  repairs,
  TICK_MS,
} from "./supervisor.js";
import { type AppAction, appAction, appOrigin, isAppUrl, isExternalLink, statusPage } from "./window.js";
import { progressScript, wizardPage } from "./wizard.js";

const MEASURE = process.env.CODELENS_MEASURE === "1";
/** The spike's own measuring: how long after the dashboard loads to sample
 * memory, so start-up work has settled. */
const IDLE_MS = 60_000;
/** Started by Windows at sign-in (start at login): straight to the tray. */
const HIDDEN = process.argv.includes("--hidden");
/** Run by the uninstaller (step 7); `--silent` with it for `/S`. */
const UNINSTALL = process.argv.includes("--uninstall");
/** The exit code that tells the uninstaller the data was deleted, so it
 * removes the app's own Electron folder (%APPDATA%\CodeLens) too. */
const DATA_DELETED = 10;
/** The user's own Ollama, on this machine (D146). */
const OLLAMA_URL = "http://127.0.0.1:11434/api/version";
const DOCKER_DOWNLOAD = "https://www.docker.com/products/docker-desktop/";
const ASSETS = join(APP_DIR, "assets");

let stack: Stack | null = null;
let launch: Launch | null = null;
let settings: DesktopSettings = { ...DEFAULTS };
/** The wizard waiting for one of these buttons. */
let waiting: { actions: AppAction[]; resolve: (action: AppAction) => void } | null = null;
let win: BrowserWindow | null = null;
let tray: Tray | null = null;
/** The stack is up and the dashboard shown; false while starting or waiting. */
let stackUp = false;
let starting = false;
let ticking = false;
let quitting = false;
let stopped = false;
let toldAboutTray = false;
/** A start failed for a reason Docker coming back won't fix: wait for Try again. */
let waitForUser = false;
/** Settings are being applied (codelens.env written, services recreated):
 * the supervisor leaves the stack alone meanwhile. */
let applying = false;
/** The control API's port and key (D175), for the dashboard's server. */
let control: { port: number; key: string; close(): Promise<void> } | null = null;
let health: Health | null = null;
/** Looking for, downloading and installing updates (step 7b). */
let updates: Updates | null = null;
let logsChild: ChildProcess | null = null;
const backoff = new Backoff();
const launchedAt = performance.now();

async function log(message: string): Promise<void> {
  const line = `${new Date().toISOString()} ${message}\n`;
  process.stdout.write(line);
  if (stack) await appendFile(join(stack.home, "logs", "desktop.log"), line).catch(() => {});
}

const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

function notify(body: string): void {
  if (Notification.isSupported()) new Notification({ title: "CodeLens", body }).show();
}

// --- the window

/** One window, on its own session: the key cookie lives there, and nothing
 * else this app loads ever shares it. Sandboxed, with no Node in the page. */
function createWindow(ses: Session): BrowserWindow {
  const window = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 600,
    title: "CodeLens",
    icon: join(ASSETS, "icon.png"),
    backgroundColor: "#131120",
    autoHideMenuBar: true,
    show: !HIDDEN,
    webPreferences: { session: ses, sandbox: true, contextIsolation: true, nodeIntegration: false },
  });
  // Only the dashboard navigates inside the window. A status page's buttons
  // are codelens:// links, done here; https links open in the user's browser.
  const guard = (event: { preventDefault(): void }, url: string) => {
    if (launch && stackUp && isAppUrl(url, launch.port)) return;
    event.preventDefault();
    const action = appAction(url);
    if (action) void act(action);
    else if (isExternalLink(url)) void shell.openExternal(url);
  };
  window.webContents.on("will-navigate", guard);
  window.webContents.on("will-redirect", guard);
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (isExternalLink(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  // Closing the window keeps CodeLens running in the tray; Quit stops it.
  window.on("close", (event) => {
    if (quitting) return;
    event.preventDefault();
    window.hide();
    if (!toldAboutTray) {
      toldAboutTray = true;
      notify("CodeLens keeps reviewing in the background. Quit it from the tray icon.");
    }
  });
  return window;
}

function showWindow(): void {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

async function show(page: string): Promise<void> {
  await win?.loadURL(page);
}

// --- what the status pages' buttons do

async function act(action: AppAction): Promise<void> {
  // A button the wizard is waiting for.
  if (waiting?.actions.includes(action)) {
    const { resolve } = waiting;
    waiting = null;
    resolve(action);
    return;
  }
  if (action === "retry") {
    waitForUser = false;
    await bringUp();
  } else if (action === "start-docker") {
    const exe = join(process.env.ProgramFiles ?? "C:\\Program Files", "Docker", "Docker", "Docker Desktop.exe");
    if (existsSync(exe)) {
      spawn(exe, [], { detached: true, stdio: "ignore" }).unref();
      await show(statusPage("Starting Docker Desktop…", "CodeLens starts as soon as Docker answers."));
    } else {
      void shell.openExternal(DOCKER_DOWNLOAD);
    }
  } else if (action === "open-logs" && stack) {
    void shell.openPath(join(stack.home, "logs"));
  }
}

// --- starting

/** Docker answering, then the stack up and healthy, then the dashboard. Says
 * what is missing instead, and the supervisor tries again by itself once
 * Docker answers. */
async function bringUp(): Promise<void> {
  if (!stack || starting) return;
  starting = true;
  try {
    const state = await dockerState();
    if (state === "missing") {
      await show(
        statusPage(
          "CodeLens needs Docker Desktop",
          "It runs CodeLens's database and services. Install it, start it, then try again.",
          { buttons: [{ label: "Get Docker Desktop", href: DOCKER_DOWNLOAD }, { label: "Try again", href: "codelens://retry" }], busy: false },
        ),
      );
      return;
    }
    if (state === "stopped") {
      await show(
        statusPage(
          "Docker Desktop isn't running",
          "CodeLens starts by itself as soon as Docker answers.\n\nIf Docker Desktop won't start on a Windows Insider build, quit it, rename %LOCALAPPDATA%\\Docker\\run aside, and start it again.",
          { buttons: [{ label: "Start Docker Desktop", href: "codelens://start-docker" }, { label: "Try again", href: "codelens://retry" }] },
        ),
      );
      return;
    }
    const cached = await imagesPresent(stack.images);
    await show(
      statusPage(
        "Starting CodeLens…",
        cached
          ? "Starting the database, the API, the workers and the dashboard."
          : "The first start downloads CodeLens's containers (about 500 MB). This takes a minute or a few, with your connection.",
      ),
    );
    launch ??= await newLaunch();
    if (control) {
      launch.controlPort = control.port;
      launch.controlKey = control.key;
    }
    const started = performance.now();
    const home = stack.home;
    const toCompose = (chunk: string) => void appendFile(join(home, "logs", "compose.log"), chunk);
    // The first start of a new version: keep a copy of the database if its
    // migrations are about to change it (D151).
    const version = app.getVersion();
    const updated = settings.setupDone && settings.lastVersion !== version;
    if (updated) {
      await show(statusPage("Updating CodeLens…", "Checking whether this version changes the database, and backing it up first if it does."));
      const backup = await backupBeforeMigrating(stack, launch, settings.lastVersion ?? "unknown", new Date(), toCompose);
      if (backup.kind === "failed") {
        await log(`update to ${version}: no backup (${backup.why}), so the migrations don't run`);
        waitForUser = true;
        await show(
          statusPage(
            "CodeLens couldn't back up its database",
            `This version changes the database, and CodeLens doesn't do that without a copy to go back to: ${backup.why}.`,
            { buttons: [{ label: "Try again", href: "codelens://retry" }, { label: "Open the logs", href: "codelens://open-logs" }], busy: false },
          ),
        );
        return;
      }
      await log(
        backup.kind === "done"
          ? `update to ${version}: database backed up to ${backup.file} (${backup.bytes} bytes) before migrating`
          : `update to ${version}: no new migrations (database at ${backup.revision?.revision ?? "nothing yet"}), no backup needed`,
      );
    }
    await log(`starting the stack on port ${launch.port} (images ${cached ? "cached" : "to pull"})`);
    const run = await up(stack, launch, toCompose);
    const took = performance.now() - started;
    if (run.code !== 0) {
      await log(`docker compose up failed (exit ${run.code}) after ${seconds(took)}`);
      await explainFailure();
      return;
    }
    if (settings.paused) await stopServices(stack, PAUSABLE);
    // A session cookie (no expiry): the window holds it until it closes, and
    // the next launch brings a new key anyway (D147, D165).
    await win?.webContents.session.cookies.set({
      url: appOrigin(launch.port),
      name: APP_KEY_COOKIE,
      value: launch.appKey,
      httpOnly: true,
      sameSite: "strict",
    });
    stackUp = true;
    if (!settings.setupDone || settings.lastVersion !== version) {
      settings = { ...settings, setupDone: true, lastVersion: version };
      await saveSettings(stack.home, settings);
    }
    // Only with published images: a developer's local build pins none of
    // them, so every published one would look stale.
    if (updated && !stack.build) {
      const removed = await removeStaleImages(stack.images).catch(() => []);
      if (removed.length) await log(`update to ${version}: removed ${removed.length} older CodeLens images`);
    }
    await win?.loadURL(`${appOrigin(launch.port)}/`);
    followLogs();
    updateTray();
    await log(
      `dashboard shown: stack up in ${seconds(took)} (${cached ? "warm" : "cold, with pulls"}), ` +
        `${seconds(performance.now() - launchedAt)} from launch; page title "${win?.getTitle()}"`,
    );
    if (MEASURE) setTimeout(() => void measure(), IDLE_MS);
    else {
      await askStartAtLogin();
      void updates?.checkAutomatically();
    }
  } finally {
    starting = false;
  }
}

/** Why `up` failed, in words: the database volume of an earlier install, or
 * else where to look. */
async function explainFailure(): Promise<void> {
  if (!stack) return;
  waitForUser = true;
  const logs = await docker(composeArgs(stack, "logs", "--tail", "50", "migrate", "postgres"), composeEnv(stack));
  if (isVolumeFromAnotherInstall(logs.output)) {
    await show(
      statusPage(
        "The database belongs to an earlier install",
        "CodeLens found the database of an install whose settings folder was deleted, and can't open it.\n\nTo start fresh, and lose that install's reviews, run in a terminal:\ndocker volume rm codelens-desktop_pgdata",
        { buttons: [{ label: "Try again", href: "codelens://retry" }, { label: "Open the logs", href: "codelens://open-logs" }], busy: false },
      ),
    );
    return;
  }
  await show(
    statusPage(
      "CodeLens couldn't start",
      `Docker answered, but a service didn't become healthy. The logs say which: ${join(stack.home, "logs")}`,
      { buttons: [{ label: "Try again", href: "codelens://retry" }, { label: "Open the logs", href: "codelens://open-logs" }], busy: false },
    ),
  );
}

/** Q26, asked once (the user's choice): No is the default. */
async function askStartAtLogin(): Promise<void> {
  if (!stack || settings.askedStartAtLogin || !win) return;
  const { response } = await dialog.showMessageBox(win, {
    type: "question",
    buttons: ["No", "Yes"],
    defaultId: 0,
    cancelId: 0,
    message: "Start CodeLens when you sign in to Windows?",
    detail: "It keeps reviewing your pull requests in the background. You can change this later from the tray icon.",
  });
  setStartAtLogin(response === 1);
  settings = { ...settings, askedStartAtLogin: true };
  await saveSettings(stack.home, settings);
  updateTray();
}

function setStartAtLogin(on: boolean): void {
  // Only the installed app registers itself: in development it would
  // register a bare electron.exe.
  if (app.isPackaged) app.setLoginItemSettings({ openAtLogin: on, args: ["--hidden"] });
}

// --- keeping it up

/** Each service's lines into logs/services/<service>.log, for as long as the
 * stack runs (started again after Docker comes back). */
function followLogs(): void {
  if (!stack || logsChild) return;
  const since = new Date(Date.now() - 60_000).toISOString();
  const logs = new ServiceLogs(join(stack.home, "logs", "services"), PROJECT);
  const child = spawn(dockerBin(), composeArgs(stack, "logs", "--follow", "--no-color", "--since", since), {
    env: { ...process.env, ...composeEnv(stack) },
    windowsHide: true,
  });
  logs.follow(child);
  child.on("close", () => {
    if (logsChild === child) logsChild = null;
  });
  child.on("error", () => {
    if (logsChild === child) logsChild = null;
  });
  logsChild = child;
}

async function ollamaAnswers(): Promise<boolean> {
  try {
    const response = await fetch(OLLAMA_URL, { signal: AbortSignal.timeout(3000) });
    return response.ok;
  } catch {
    return false;
  }
}

async function tick(): Promise<void> {
  if (!stack || !launch || ticking || starting || quitting || applying) return;
  ticking = true;
  try {
    const dockerNow = await dockerState().catch(() => "stopped" as const);
    const now: Health = { docker: dockerNow, ollama: await ollamaAnswers(), repairing: [] };
    if (dockerNow !== "running") waitForUser = false; // its return is worth a new try
    if (dockerNow !== "running" && stackUp) {
      stackUp = false;
      await show(
        statusPage("Docker Desktop stopped", "CodeLens is waiting for it, and comes back by itself once Docker answers.", {
          buttons: [{ label: "Start Docker Desktop", href: "codelens://start-docker" }],
        }),
      );
    }
    if (stackUp) {
      const clock = Date.now();
      const states = parseServiceStates(await serviceStates(stack));
      const needed = repairs(states, { paused: settings.paused });
      for (const service of states.map((s) => s.service)) {
        if (!needed.some((r) => r.service === service)) backoff.fine(service, clock);
      }
      for (const repair of needed) {
        now.repairing.push(repair);
        if (!backoff.ready(repair.service, clock)) continue;
        const wait = backoff.tried(repair.service, clock);
        await log(`${repair.service} is ${repair.why}: ${repair.action}ing it (next try in ${seconds(wait)} if needed)`);
        if (repair.action === "start") await upServices(stack, launch, [repair.service]);
        else await restartServices(stack, [repair.service]);
      }
      if (!logsChild) followLogs();
    }
    for (const message of changes(health, now)) {
      await log(message);
      notify(message);
    }
    health = now;
    updateTray();
    // Docker came back (or never was): start, as the first launch does.
    if (dockerNow === "running" && !stackUp && !waitForUser) await bringUp();
  } catch (error) {
    await log(`supervisor: ${error instanceof Error ? error.message : String(error)}`);
  } finally {
    ticking = false;
  }
}

// --- the tray

async function setPaused(paused: boolean): Promise<void> {
  if (!stack || !launch) return;
  settings = { ...settings, paused };
  await saveSettings(stack.home, settings);
  if (paused) await stopServices(stack, PAUSABLE);
  else await upServices(stack, launch, PAUSABLE);
  await log(paused ? "paused: no reviews or indexing until resumed" : "resumed");
  updateTray();
}

function status(): string {
  const u = updates?.state;
  if (u?.status === "downloading") return `Downloading ${u.available} (${u.progress ?? 0}%)`;
  if (!health || starting) return "Starting…";
  if (health.docker !== "running") return "Waiting for Docker Desktop";
  if (!stackUp) return "Not running";
  if (settings.paused) return "Paused: no reviews";
  if (!health.ollama) return "Ollama isn't running";
  if (health.repairing.length) return `Restarting ${health.repairing.map((r) => r.service).join(", ")}`;
  return "Running";
}

function updateTray(): void {
  if (!tray) return;
  tray.setToolTip(`CodeLens: ${status()}`);
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Open CodeLens", click: showWindow },
      { type: "separator" },
      { label: status(), enabled: false },
      {
        label: settings.paused ? "Resume reviews" : "Pause reviews",
        enabled: stackUp,
        click: () => void setPaused(!settings.paused),
      },
      {
        label: "Start when Windows starts",
        type: "checkbox",
        checked: app.isPackaged && app.getLoginItemSettings().openAtLogin,
        enabled: app.isPackaged,
        click: (item) => setStartAtLogin(item.checked),
      },
      { label: "Open data folder", click: () => stack && void shell.openPath(stack.home) },
      { type: "separator" },
      ...updateItems(),
      { type: "separator" },
      { label: "Quit CodeLens", click: () => app.quit() },
    ]),
  );
}

/** The tray's update items: the one thing to do now, if any; looking now;
 * and the switch for looking by itself (also on the Settings page). */
function updateItems(): Electron.MenuItemConstructorOptions[] {
  const u = updates?.state;
  if (!u) return [];
  const items: Electron.MenuItemConstructorOptions[] = [];
  if (u.status === "available") items.push({ label: `Update to ${u.available}…`, click: () => void updates?.install() });
  if (u.status === "ready") items.push({ label: `Restart to update to ${u.available}…`, click: () => void updates?.install() });
  items.push(
    {
      label: u.status === "checking" ? "Checking for updates…" : "Check for updates now",
      enabled: !["checking", "downloading"].includes(u.status),
      click: () => void updates?.checkNow(),
    },
    {
      label: "Check for updates automatically",
      type: "checkbox",
      checked: u.checkForUpdates,
      click: (item) => void setCheckForUpdates(item.checked),
    },
  );
  return items;
}

async function setCheckForUpdates(on: boolean): Promise<void> {
  if (!stack) return;
  settings = { ...settings, checkForUpdates: on };
  await saveSettings(stack.home, settings);
  updates?.setCheckForUpdates(on);
  await log(on ? "updates: looking daily again" : "updates: no longer looking by itself");
}

/** electron-updater, set so it only ever acts on the user's word, with the
 * flow around it (updates.ts). */
function createUpdates(): Updates {
  const { autoUpdater } = electronUpdater;
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.allowPrerelease = false;
  autoUpdater.disableWebInstaller = true;
  autoUpdater.logger = {
    info: (m: unknown) => void log(`updater: ${String(m)}`),
    warn: (m: unknown) => void log(`updater: ${String(m)}`),
    error: (m: unknown) => void log(`updater: ${String(m)}`),
    debug: () => {},
  };
  return new Updates(
    {
      updater: autoUpdater,
      current: app.getVersion(),
      ask: async (message, detail, buttons) => {
        if (win) showWindow();
        const options = { type: "info" as const, title: "CodeLens", message, detail, buttons, defaultId: 0, cancelId: buttons.length > 1 ? 1 : 0, noLink: true };
        const { response } = win ? await dialog.showMessageBox(win, options) : await dialog.showMessageBox(options);
        return response;
      },
      openExternal: (url) => void shell.openExternal(url),
      // The installer kills whatever runs from the install folder: stop the
      // stack first, as Quit does, so no container is left without an app.
      beforeInstall: async () => {
        quitting = true;
        stopped = true;
        logsChild?.kill();
        await control?.close();
        if (stack) {
          await show(statusPage("Updating CodeLens…", "Stopping its services, then installing the update. CodeLens starts again by itself."));
          const run = await stop(stack);
          await log(`stack stopped for the update (exit ${run.code})`);
        }
      },
      onChange: (view) => {
        win?.setProgressBar(view.status === "downloading" ? (view.progress ?? 0) / 100 : -1);
        updateTray();
      },
      log: (message) => void log(message),
      now: () => new Date(),
    },
    settings.checkForUpdates,
  );
}

// --- Settings (the dashboard asks through the control API, D175)

async function currentSettings(home: string): Promise<SettingsView> {
  const envText = await readFile(join(home, "codelens.env"), "utf8");
  const pulled: Record<string, boolean> = {};
  try {
    const wanted = wantedModels(envText);
    const missing = missingModels(await ollamaTags(), wanted);
    for (const model of wanted) pulled[model] = !missing.includes(model);
  } catch {
    // Ollama not answering: every model reads as not pulled.
  }
  return readSettings(envText, {
    pulled,
    ollamaReadyForLong: readyForLong(await userEnvironment()),
    installUrl: settings.installUrl,
    updates: updates?.state ?? {
      current: app.getVersion(),
      checkForUpdates: settings.checkForUpdates,
      status: "idle",
      available: null,
      progress: null,
      lastChecked: null,
      error: null,
    },
  });
}

/** Writes codelens.env and recreates the services that read it; the
 * supervisor waits meanwhile. The dashboard's own container isn't among them
 * (its settings don't change), so the request that asked survives. */
async function applySettings(home: string, update: (envText: string) => string): Promise<void> {
  if (!stack || !launch) throw new Error("CodeLens isn't running yet");
  applying = true;
  try {
    const envFile = join(home, "codelens.env");
    await writeFile(envFile, update(await readFile(envFile, "utf8")), { mode: 0o600 });
    const run = await up(stack, launch, (chunk) => void appendFile(join(home, "logs", "compose.log"), chunk));
    if (run.code !== 0) throw new Error("the services didn't come back healthy: see logs/compose.log");
  } finally {
    applying = false;
  }
}

function settingsApi(home: string) {
  return {
    getSettings: () => currentSettings(home),
    saveSettings: async (body: unknown) => {
      const checked = checkChanges(body);
      if (!checked.ok) return { errors: checked.errors };
      await log(`settings: ${Object.keys(checked.changes).join(", ")} changed`);
      if (checked.changes.checkForUpdates !== undefined) await setCheckForUpdates(checked.changes.checkForUpdates);
      const env = envChanges(checked.changes);
      if (Object.keys(env).length > 0) await applySettings(home, (text) => applyChanges(text, env));
      return { settings: await currentSettings(home) };
    },
    prepareLongWindow: async () => {
      if (!readyForLong(await userEnvironment())) {
        await log("settings: preparing Ollama for a 16k window, and restarting it");
        await prepareOllamaForLong(ollamaAppExe());
        for (let i = 0; i < 30 && !(await ollamaAnswers()); i++) await new Promise((r) => setTimeout(r, 1000));
      }
      await applySettings(home, (text) => applyChanges(text, { contextWindow: WINDOWS.long }));
      return currentSettings(home);
    },
  };
}

// --- System (the dashboard asks through the control API, D176)

const WORKERS = ["worker", "worker-gpu"];

async function databaseBytes(): Promise<number | null> {
  if (!stack) return null;
  const sql = "select pg_database_size('codelens')";
  const run = await docker(
    composeArgs(stack, "exec", "-T", "postgres", "psql", "-U", "codelens", "-d", "codelens", "-tAc", sql),
    composeEnv(stack),
  );
  const bytes = Number(run.output.trim());
  return run.code === 0 && Number.isFinite(bytes) ? bytes : null;
}

async function currentSystem(home: string): Promise<SystemView> {
  const dockerNow = await dockerState().catch(() => "stopped" as const);
  const services = dockerNow === "running" && stack ? parseServiceStates(await serviceStates(stack).catch(() => "")) : [];
  const disk = await statfs(home).catch(() => null);
  return {
    docker: dockerNow,
    ollama: await ollamaAnswers(),
    paused: settings.paused,
    services,
    storage: {
      dataFolder: home,
      mirrorsBytes: await folderBytes(join(home, "data", "repos")),
      logsBytes: await folderBytes(join(home, "logs")),
      databaseBytes: dockerNow === "running" ? await databaseBytes() : null,
      freeBytes: disk ? disk.bavail * disk.bsize : null,
    },
  };
}

function systemApi(home: string) {
  return {
    getSystem: () => currentSystem(home),
    // The name was checked against the known services (control.ts).
    getLogs: (service: string, lines: number) => tailFile(join(home, "logs", "services", `${service}.log`), lines),
    restartService: async (service: string) => {
      if (!stack || !launch) throw new Error("CodeLens isn't running yet");
      await log(`system: restarting ${service}`);
      const run = await restartServices(stack, [service]);
      // A container that's gone can't be restarted: create it instead.
      if (run.code !== 0) await upServices(stack, launch, [service]);
    },
    /** The repos' bare mirrors, deleted with the workers stopped; each is
     * fetched again in full the next time it is indexed or reviewed. */
    clearMirrors: async () => {
      if (!stack || !launch) throw new Error("CodeLens isn't running yet");
      await log("system: clearing the repos' mirrors");
      applying = true;
      try {
        await stopServices(stack, WORKERS);
        await rm(join(home, "data", "repos"), { recursive: true, force: true });
        if (!settings.paused) await upServices(stack, launch, WORKERS);
      } finally {
        applying = false;
      }
      return currentSystem(home);
    },
    openDataFolder: async () => {
      await shell.openPath(home);
    },
  };
}

// --- the spike's numbers

/** The idle-memory reading: the app's own processes (Electron's working sets)
 * and each container. */
async function measure(): Promise<void> {
  const electronMiB = app.getAppMetrics().reduce((sum, p) => sum + p.memory.workingSetSize / 1024, 0);
  const containers = await containerMemory();
  const containersMiB = [...containers.values()].reduce((a, b) => a + b, 0);
  const each = [...containers].map(([name, mib]) => `${name} ${mib.toFixed(0)}`).join(", ");
  await log(
    `idle after ${IDLE_MS / 1000} s: app ${electronMiB.toFixed(0)} MiB, ` +
      `containers ${containersMiB.toFixed(0)} MiB (${each}), total ${(electronMiB + containersMiB).toFixed(0)} MiB`,
  );
  app.quit();
}

// --- uninstalling (the uninstaller runs `CodeLens.exe --uninstall`)

async function runUninstall(): Promise<void> {
  const home = codelensHome();
  const configDir = app.isPackaged ? process.resourcesPath : APP_DIR;
  // For the log only: nothing here may make the data folder again.
  stack = { home, images: { api: "", worker: "", web: "" }, configDir };
  const images = await readImages(join(configDir, "images.json")).catch(() => null);
  const saved = await loadSettings(home);
  const cli = dockerCli();
  if (cli && (await dockerState().catch(() => "stopped")) === "missing") useDocker(cli);
  await log(`uninstall: started (version ${app.getVersion()})`);
  // The app was force-closed: its log follower may still run.
  const followers = await stopLeftoverFollowers(PROJECT);
  if (followers) await log(`uninstall: ended ${followers} leftover log follower(s)`);
  const result = await uninstall({
    silent: process.argv.includes("--silent"),
    ask: async (q) => {
      const { response } = await dialog.showMessageBox({
        type: "question",
        title: "Uninstall CodeLens",
        message: q.message,
        detail: q.detail,
        buttons: q.buttons,
        defaultId: 0,
        cancelId: q.cancel,
        noLink: true,
      });
      return response;
    },
    dockerState: () => dockerState().catch(() => "stopped" as const),
    down: (removeVolume) => downProject(removeVolume),
    removeImages: async () => {
      if (images) await docker(["image", "rm", ...Object.values(images)]);
    },
    // Docker can take a moment to let go of the folder's bind mounts.
    removeDataFolder: () => rm(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 500 }),
    forgetStartup: async () => {
      app.setLoginItemSettings({ openAtLogin: false, args: ["--hidden"] });
      await resumeAfterRestart(process.execPath, false);
    },
    startDocker: () => {
      if (existsSync(dockerDesktopExe())) launchProgram(dockerDesktopExe());
    },
    openExternal: (url) => void shell.openExternal(url),
    appSettingsUrl: manageUrl(saved.installUrl),
    dataFolder: home,
    log,
  });
  await log(`uninstall: done, data ${result}`);
  app.exit(result === "deleted" ? DATA_DELETED : 0);
}

// --- the app

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
  settings = await loadSettings(home);
  // One from a run that was force-closed would write every line twice.
  const followers = await stopLeftoverFollowers(PROJECT);
  if (followers) await log(`ended ${followers} leftover log follower(s) of an earlier run`);

  const ses = session.fromPartition("persist:codelens");
  // The dashboard asks for nothing (camera, notifications, ...): refuse all.
  ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  win = createWindow(ses);
  tray = new Tray(nativeImage.createFromPath(join(ASSETS, "tray.png")));
  tray.on("click", showWindow);
  updateTray();

  // The control API first: the dashboard's container gets its port and key.
  const controlKey = newSecret();
  updates = createUpdates();
  setInterval(() => void updates?.checkAutomatically(), CHECK_EVERY_MS);
  const updateApi = {
    checkForUpdates: async () => {
      if (!updates) throw new Error("CodeLens isn't running yet");
      await updates.checkNow();
      return updates.state;
    },
    // The download takes a while: answered at once, the page shows its progress.
    installUpdate: async () => {
      if (!updates) throw new Error("CodeLens isn't running yet");
      void updates.install();
      return updates.state;
    },
  };
  const started = await startControl({ ...settingsApi(home), ...systemApi(home), ...updateApi }, controlKey, (message) => void log(message));
  control = { port: started.port, key: controlKey, close: started.close };

  // Docker installed after this process's PATH was set: use its own docker.exe.
  const cli = dockerCli();
  if (cli && (await dockerState()) === "missing") useDocker(cli);

  if (!settings.setupDone && !MEASURE) {
    const result = await firstRun({
      downloadsDir: join(home, "downloads"),
      show: async (view) => {
        await win?.loadURL(wizardPage(view));
      },
      progress: async (fraction, label) => {
        await win?.webContents.executeJavaScript(progressScript(fraction, label)).catch(() => {});
      },
      waitFor: (actions) =>
        new Promise((resolve) => {
          waiting = { actions, resolve };
        }),
      log,
      machineChecks: () => machineChecks(home),
      dockerState: () => dockerState().catch(() => "stopped" as const),
      dockerCli,
      useDocker,
      ollamaAnswers,
      isOllamaInstalled,
      download,
      readSignature,
      runInstaller,
      launch: launchProgram,
      dockerDesktopExe,
      ollamaAppExe,
      missingModels: async () =>
        missingModels(await ollamaTags(), wantedModels(await readFile(join(home, "codelens.env"), "utf8"))),
      pullModel: (name, onProgress) => pullModel(name, onProgress),
      hasApp: async () => hasApp(await readFile(join(home, "codelens.env"), "utf8")),
      createApp: async (openBrowser) => {
        const created = await createApp(stack as Stack, appName(userInfo().username), openBrowser, (line) => void log(line));
        settings = { ...settings, installUrl: created.installUrl };
        await saveSettings(home, settings);
        return created;
      },
      openExternal: (url) => void shell.openExternal(url),
      doctor: () => runDoctor(stack as Stack),
      resumeAfterRestart: (on) => resumeAfterRestart(app.isPackaged ? process.execPath : null, on),
      removeFile: (path) => rm(path, { force: true }),
      sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
      now: () => Date.now(),
    });
    // Docker's restart: the app opens again by itself afterwards (RunOnce).
    if (result === "restart") return;
  }

  await bringUp();
  setInterval(() => void tick(), TICK_MS);
}

// Every process this app starts inherits its working folder. Started from its
// shortcut, that is the install folder, which a leftover child then keeps the
// uninstaller from deleting (found in the rehearsal, step 8b): the temp folder
// instead.
process.chdir(tmpdir());

if (UNINSTALL) {
  // The uninstaller has closed the running app already; this run is alone.
  app.whenReady().then(runUninstall, () => app.exit(1));
} else if (!app.requestSingleInstanceLock()) {
  // One app at a time: a second launch shows the first one's window.
  app.quit();
} else {
  app.setAppUserModelId("io.github.raif-karani.codelens"); // Windows notifications
  app.on("second-instance", showWindow);
  // The window only hides; the tray keeps the app alive.
  app.on("window-all-closed", () => {});

  app.whenReady().then(start, async (error: unknown) => {
    await log(`start failed: ${error instanceof Error ? error.message : String(error)}`);
    app.quit();
  });

  // Quitting stops the stack first; the data stays for the next start.
  app.on("before-quit", (event) => {
    quitting = true;
    if (stopped || !stack) return;
    event.preventDefault();
    stopped = true;
    logsChild?.kill();
    void control?.close();
    const begun = performance.now();
    void stop(stack).then(async (run) => {
      await log(`stack stopped (exit ${run.code}) in ${seconds(performance.now() - begun)}`);
      app.quit();
    });
  });
}
