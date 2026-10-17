/** The first-run wizard's flow (planning-v2.md §4, Phase 11 step 5): this
 * computer's checks, then Docker Desktop, then Ollama, each installed from
 * its publisher if missing, after the user's yes, and only once its
 * signature checks out (D150, 5a); then the models, the user's own GitHub
 * App and its installation, and the stack's own checks (5b). Each step looks before it acts, so closing the
 * app halfway, or the restart Docker may need, picks up at the first step not
 * done yet. Everything with a side effect comes in `FirstRunDeps`, so tests
 * play a machine with nothing on it. */

import type { Installer, Progress, Signature } from "./installers.js";
import { INSTALLERS, signatureProblem } from "./installers.js";
import type { Check } from "./machine.js";
import type { DockerState } from "./stack.js";
import type { AppAction } from "./window.js";
import { progressLabel, type StepId, type WizardView } from "./wizard.js";

export type FirstRunDeps = {
  downloadsDir: string;
  show(view: WizardView): Promise<void>;
  progress(fraction: number | null, label: string): Promise<void>;
  /** Resolves with the first of `actions` the user picks on the screen. */
  waitFor(actions: AppAction[]): Promise<AppAction>;
  log(message: string): Promise<void>;
  machineChecks(): Promise<Check[]>;
  dockerState(): Promise<DockerState>;
  /** Docker Desktop's own docker.exe, if installed (not on the PATH yet). */
  dockerCli(): string | null;
  useDocker(command: string): void;
  ollamaAnswers(): Promise<boolean>;
  isOllamaInstalled(): boolean;
  download(installer: Installer, dir: string, onProgress: (p: Progress) => void): Promise<string>;
  readSignature(path: string): Promise<Signature | null>;
  runInstaller(installer: Installer, path: string): Promise<number>;
  launch(exe: string): void;
  dockerDesktopExe(): string;
  ollamaAppExe(): string;
  /** The models CodeLens needs that Ollama doesn't have yet. */
  missingModels(): Promise<string[]>;
  pullModel(name: string, onProgress: (p: { completed: number; total: number }) => void): Promise<void>;
  /** codelens.env has an App's settings. */
  hasApp(): Promise<boolean>;
  /** The manifest flow; `openBrowser` gets its start link. */
  createApp(openBrowser: (url: string) => void): Promise<{ installUrl: string }>;
  openExternal(url: string): void;
  /** `codelens doctor --desktop --json`, run in the stack's tools container. */
  doctor(): Promise<Check[]>;
  /** Open CodeLens once at the next sign-in (on), or forget that (off). */
  resumeAfterRestart(on: boolean): Promise<void>;
  removeFile(path: string): Promise<void>;
  sleep(ms: number): Promise<void>;
  now(): number;
};

export type FirstRunResult = "ready" | "restart";

/** Exit code of an installer that finished but needs Windows restarted. */
export const RESTART_NEEDED = 3010;
const DOCKER_START_TIMEOUT_MS = 5 * 60_000;
const OLLAMA_START_TIMEOUT_MS = 90_000;
const POLL_MS = 3_000;

const DOCKER_PAGE = "https://docs.docker.com/desktop/setup/install/windows-install/";
const OLLAMA_PAGE = "https://ollama.com/download/windows";

export async function firstRun(d: FirstRunDeps): Promise<FirstRunResult> {
  const marks: Partial<Record<StepId, "active" | "done" | "warn" | "failed">> = {};
  const view = (step: StepId, mark: "active" | "failed", rest: Omit<WizardView, "marks">) =>
    d.show({ ...rest, marks: { ...marks, [step]: mark } });

  // 1. This computer.
  for (;;) {
    await view("machine", "active", { title: "Checking this computer…", detail: "What CodeLens needs before anything is downloaded.", progress: { label: "Checking…", fraction: null } });
    const checks = await d.machineChecks();
    await d.log(`machine: ${checks.map((c) => `${c.name} ${c.status}`).join(", ")}`);
    if (checks.some((c) => c.status === "fail")) {
      await view("machine", "failed", {
        title: "This computer needs a change first",
        detail: "What's marked ✕ has to be fixed for CodeLens to run. Check again once it is.",
        checks,
        buttons: [{ label: "Check again", href: "codelens://check-again" }],
      });
      await d.waitFor(["check-again"]);
      continue;
    }
    if (checks.some((c) => c.status === "warn")) {
      await view("machine", "active", {
        title: "This computer can run CodeLens",
        detail: "With the notes marked ! in mind.",
        checks,
        buttons: [{ label: "Continue", href: "codelens://continue" }],
      });
      await d.waitFor(["continue"]);
      marks.machine = "warn";
    } else {
      marks.machine = "done";
    }
    break;
  }

  // 2. Docker Desktop.
  for (;;) {
    let state = await d.dockerState();
    const cli = state === "missing" ? d.dockerCli() : null;
    if (cli) {
      d.useDocker(cli); // installed by this run, or before PATH caught up
      state = await d.dockerState();
    }
    if (state === "running") break;
    if (state === "stopped") {
      d.launch(d.dockerDesktopExe());
      await view("docker", "active", {
        title: "Starting Docker Desktop…",
        detail: "The first time, it opens a window of its own: accept its terms there, and CodeLens goes on by itself.",
        progress: { label: "Waiting for Docker to answer…", fraction: null },
      });
      if (await waitUntil(d, async () => (await d.dockerState()) === "running", DOCKER_START_TIMEOUT_MS)) break;
      await view("docker", "failed", {
        title: "Docker Desktop didn't start",
        detail: "Open Docker Desktop and look at what it says.\n\nOn a Windows Insider build it sometimes won't start: quit it, rename %LOCALAPPDATA%\\Docker\\run aside, and start it again.",
        buttons: [{ label: "Check again", href: "codelens://check-again" }],
      });
      await d.waitFor(["check-again"]);
      continue;
    }
    await view("docker", "active", {
      title: "Install Docker Desktop",
      detail:
        "CodeLens runs its database and services in Docker Desktop. CodeLens downloads Docker's official installer (about 610 MB) from docker.com, checks it is signed by Docker Inc, and opens it; Windows asks for permission, and Docker's installer shows its terms.\n\nDocker Desktop is free for personal use, education and small businesses; larger companies need a paid plan. It may ask to restart Windows: CodeLens opens again by itself afterwards.",
      buttons: [
        { label: "Download and install", href: "codelens://install-docker" },
        { label: "Get it myself", href: DOCKER_PAGE },
        { label: "Check again", href: "codelens://check-again" },
      ],
    });
    if ((await d.waitFor(["install-docker", "check-again"])) === "check-again") continue;
    const path = await fetchChecked(d, "docker", INSTALLERS.docker);
    if (!path) continue;
    await d.resumeAfterRestart(true); // the installer may restart Windows itself
    await view("docker", "active", { title: "Installing Docker Desktop…", detail: "Its installer is open: follow it there.", progress: { label: "Installing…", fraction: null } });
    const code = await d.runInstaller(INSTALLERS.docker, path);
    await d.log(`Docker Desktop's installer exited with ${code}`);
    await d.removeFile(path);
    if (code === RESTART_NEEDED) {
      marks.docker = "done";
      await d.show({
        marks: { ...marks, ollama: "todo" },
        title: "Restart Windows to finish",
        detail: "Docker Desktop is installed and needs a restart. CodeLens opens again by itself after it, and goes on from here.",
      });
      return "restart";
    }
    if (code !== 0) {
      await d.resumeAfterRestart(false);
      await view("docker", "failed", {
        title: "Docker Desktop wasn't installed",
        detail: `Its installer stopped (code ${code}). Try again, or install it yourself and check again.`,
        buttons: [
          { label: "Check again", href: "codelens://check-again" },
          { label: "Get it myself", href: DOCKER_PAGE },
        ],
      });
      await d.waitFor(["check-again"]);
    }
    // Installed: the next pass finds it stopped, and starts it.
  }
  await d.resumeAfterRestart(false); // no restart pending any more
  marks.docker = "done";

  // 3. Ollama.
  for (;;) {
    if (await d.ollamaAnswers()) break;
    if (d.isOllamaInstalled()) {
      d.launch(d.ollamaAppExe());
      await view("ollama", "active", { title: "Starting Ollama…", detail: "It runs the models on this computer.", progress: { label: "Waiting for Ollama to answer…", fraction: null } });
      if (await waitUntil(d, d.ollamaAnswers, OLLAMA_START_TIMEOUT_MS)) break;
      await view("ollama", "failed", {
        title: "Ollama didn't start",
        detail: "Start Ollama from the Start menu, then check again.",
        buttons: [{ label: "Check again", href: "codelens://check-again" }],
      });
      await d.waitFor(["check-again"]);
      continue;
    }
    await view("ollama", "active", {
      title: "Install Ollama",
      detail:
        "Ollama runs the models that review your code, on this computer. CodeLens downloads Ollama's official installer (about 1.5 GB) from ollama.com, checks it is signed by Ollama Inc., and installs it for you only. Ollama is open source (MIT).",
      buttons: [
        { label: "Download and install", href: "codelens://install-ollama" },
        { label: "Get it myself", href: OLLAMA_PAGE },
        { label: "Check again", href: "codelens://check-again" },
      ],
    });
    if ((await d.waitFor(["install-ollama", "check-again"])) === "check-again") continue;
    const path = await fetchChecked(d, "ollama", INSTALLERS.ollama);
    if (!path) continue;
    await view("ollama", "active", { title: "Installing Ollama…", detail: "Its installer shows its own progress.", progress: { label: "Installing…", fraction: null } });
    const code = await d.runInstaller(INSTALLERS.ollama, path);
    await d.log(`Ollama's installer exited with ${code}`);
    await d.removeFile(path);
    if (code !== 0) {
      await view("ollama", "failed", {
        title: "Ollama wasn't installed",
        detail: `Its installer stopped (code ${code}). Try again, or install it yourself and check again.`,
        buttons: [
          { label: "Check again", href: "codelens://check-again" },
          { label: "Get it myself", href: OLLAMA_PAGE },
        ],
      });
      await d.waitFor(["check-again"]);
    }
    // Installed: the next pass starts it if its installer didn't.
  }
  marks.ollama = "done";

  // 4. The models, into the user's own Ollama (D146).
  for (;;) {
    let missing: string[];
    try {
      missing = await d.missingModels();
    } catch (error) {
      await view("models", "failed", { title: "Ollama didn't answer", detail: String(error), buttons: [{ label: "Check again", href: "codelens://check-again" }] });
      await d.waitFor(["check-again"]);
      continue;
    }
    if (missing.length === 0) break;
    await view("models", "active", {
      title: "Download the models",
      detail: `CodeLens reviews with ${missing.join(" and ")}, which run in Ollama on this computer. Ollama downloads them from its own registry (about 6 GB in all) and checks every part against its digest.`,
      buttons: [{ label: "Download the models", href: "codelens://download-models" }],
    });
    await d.waitFor(["download-models"]);
    try {
      for (const name of missing) {
        await view("models", "active", { title: `Downloading ${name}…`, detail: "Into Ollama, on this computer.", progress: { label: "Starting…", fraction: 0 } });
        let shown = Number.NEGATIVE_INFINITY;
        await d.pullModel(name, ({ completed, total }) => {
          const at = d.now();
          if (at - shown < 250 && completed !== total) return;
          shown = at;
          void d.progress(total ? completed / total : null, total ? `${name}: ${progressLabel(completed, total)}` : name);
        });
        await d.log(`pulled ${name}`);
      }
    } catch (error) {
      await view("models", "failed", {
        title: "The models weren't downloaded",
        detail: `${error instanceof Error ? error.message : String(error)}. Check the connection and try again: what was already downloaded is kept.`,
        buttons: [{ label: "Check again", href: "codelens://check-again" }],
      });
      await d.waitFor(["check-again"]);
    }
  }
  marks.models = "done";

  // 5. The user's own GitHub App (D139), by the manifest flow (D93, D174).
  for (;;) {
    if (await d.hasApp()) break;
    await view("app", "active", {
      title: "Create your GitHub App",
      detail:
        "CodeLens reads your repositories and posts its reviews as a GitHub App of your own, on your account: nobody else's App, server or relay is involved. Your browser opens on GitHub, which shows the App's name (you can change it) and what it may do: read code, and write pull request reviews. Click Create GitHub App there, then come back here.",
      buttons: [{ label: "Create my App", href: "codelens://create-app" }],
    });
    await d.waitFor(["create-app"]);
    await view("app", "active", {
      title: "Finish in your browser",
      detail: "On GitHub: check the name, then Create GitHub App. GitHub sends you back to a page that says it's done; this window goes on by itself.",
      progress: { label: "Waiting for GitHub…", fraction: null },
    });
    let installUrl: string;
    try {
      ({ installUrl } = await d.createApp((url) => d.openExternal(url)));
    } catch (error) {
      await d.log(`creating the App failed: ${String(error)}`);
      await view("app", "failed", {
        title: "The App wasn't created",
        detail: `${error instanceof Error ? error.message : String(error)}. Nothing was saved; try again.`,
        buttons: [{ label: "Try again", href: "codelens://check-again" }],
      });
      await d.waitFor(["check-again"]);
      continue;
    }
    await view("app", "active", {
      title: "Install your App on your repositories",
      detail: "On GitHub, choose Only select repositories, pick the ones to review, and Install. Each is copied to this computer and indexed (a few minutes for a small repository). You can add more later from the App's settings on GitHub.",
      buttons: [
        { label: "Open the install page", href: installUrl },
        { label: "I've installed it", href: "codelens://installed" },
      ],
    });
    await d.waitFor(["installed"]);
    break;
  }
  marks.app = "done";

  // 6. What the stack itself says: Ollama, the models, the App on GitHub.
  for (;;) {
    await view("start", "active", { title: "Checking…", detail: "Ollama, the models, and your App on GitHub.", progress: { label: "Checking…", fraction: null } });
    let checks: Check[];
    try {
      checks = await d.doctor();
    } catch (error) {
      checks = [{ name: "Checks", status: "fail", detail: String(error) }];
    }
    await d.log(`doctor: ${checks.map((c) => `${c.name} ${c.status}`).join(", ")}`);
    const install = checks.find((c) => c.fix?.startsWith("install it: "))?.fix?.slice("install it: ".length);
    const failed = checks.some((c) => c.status === "fail");
    await view("start", failed ? "failed" : "active", {
      title: failed ? "Something isn't ready yet" : "Ready to start",
      detail: failed ? "What's marked ✕ needs a look. CodeLens can start anyway, and its dashboard still works." : "The rest happens by itself: your repositories are indexed, and every push to a pull request is reviewed.",
      checks,
      buttons: [
        ...(failed ? [{ label: "Check again", href: "codelens://check-again" }] : []),
        { label: failed ? "Start anyway" : "Start CodeLens", href: "codelens://continue" },
        ...(install?.startsWith("https://github.com/apps/") ? [{ label: "Open the install page", href: install }] : []),
      ],
    });
    if ((await d.waitFor(failed ? ["check-again", "continue"] : ["continue"])) === "continue") break;
  }
  await d.show({ marks: { ...marks, start: "active" }, title: "Starting CodeLens…", detail: "The database, the API, the workers and the dashboard.", progress: { label: "Starting…", fraction: null } });
  return "ready";
}

/** Downloads an installer and checks its signature; the path, or null after
 * saying why not (and deleting the file, which is never run). */
async function fetchChecked(d: FirstRunDeps, step: StepId, installer: Installer): Promise<string | null> {
  const fail = async (detail: string) => {
    await d.show({
      marks: { [step]: "failed" },
      title: `${installer.name} wasn't installed`,
      detail,
      buttons: [{ label: "Check again", href: "codelens://check-again" }],
    });
    await d.waitFor(["check-again"]);
    return null;
  };
  await d.show({
    marks: { [step]: "active" },
    title: `Downloading ${installer.name}…`,
    detail: `From ${new URL(installer.url).host}, the publisher's own address.`,
    progress: { label: "Starting the download…", fraction: 0 },
  });
  let path: string;
  let shown = Number.NEGATIVE_INFINITY; // the first update always shows
  try {
    path = await d.download(installer, d.downloadsDir, ({ received, total }) => {
      const at = d.now();
      if (at - shown < 250 && received !== total) return; // a few updates a second
      shown = at;
      void d.progress(total ? received / total : null, progressLabel(received, total));
    });
  } catch (error) {
    await d.log(`downloading ${installer.name} failed: ${String(error)}`);
    return fail(`The download didn't finish: ${error instanceof Error ? error.message : String(error)}. Check the connection and try again.`);
  }
  await d.show({ marks: { [step]: "active" }, title: `Checking ${installer.name}…`, detail: "Windows checks who signed the installer.", progress: { label: "Checking the signature…", fraction: null } });
  const problem = signatureProblem(await d.readSignature(path), installer.publisher);
  if (problem) {
    await d.removeFile(path);
    await d.log(`${installer.name}'s installer refused: ${problem}`);
    return fail(`The downloaded installer was deleted without running: ${problem}.`);
  }
  await d.log(`${installer.name}'s installer is signed by ${installer.publisher}`);
  return path;
}

async function waitUntil(d: FirstRunDeps, ready: () => Promise<boolean>, timeoutMs: number): Promise<boolean> {
  const deadline = d.now() + timeoutMs;
  while (d.now() < deadline) {
    if (await ready()) return true;
    await d.sleep(POLL_MS);
  }
  return ready();
}
