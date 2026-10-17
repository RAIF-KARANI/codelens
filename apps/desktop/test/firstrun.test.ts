import { describe, expect, it } from "vitest";

import { type FirstRunDeps, firstRun, RESTART_NEEDED } from "../src/firstrun.js";
import type { Installer, Signature } from "../src/installers.js";
import type { Check } from "../src/machine.js";
import type { DockerState } from "../src/stack.js";
import type { AppAction } from "../src/window.js";
import type { WizardView } from "../src/wizard.js";

const OK: Check[] = [{ name: "Windows", status: "ok", detail: "Windows 11" }];
const DOCKER_SIGNED: Signature = { status: "Valid", subject: "CN=Docker Inc, O=Docker Inc, C=US" };
const OLLAMA_SIGNED: Signature = { status: "Valid", subject: "CN=Ollama Inc., O=Ollama Inc., C=CA" };

/** A machine the flow runs on: what it has, what the user clicks, and a
 * record of everything the flow did. */
class Machine {
  docker: DockerState = "running";
  cli: string | null = null;
  ollama = true;
  ollamaInstalled = true;
  checks: Check[][] = [OK];
  clicks: AppAction[] = [];
  signatures: Record<string, Signature | null> = {};
  exitCodes: Record<string, number> = {};
  missing: string[] = [];
  app = true;
  appError: Error | null = null;
  doctorChecks: Check[][] = [];
  /** What starting a program does to the machine. */
  onLaunch: (exe: string) => void = (exe) => {
    if (exe.includes("Docker")) this.docker = "running";
    if (exe.includes("ollama")) this.ollama = true;
  };
  /** What a successful installer does. */
  onInstall: (name: string) => void = (name) => {
    if (name === "Docker Desktop") this.docker = "stopped";
    if (name === "Ollama") this.ollamaInstalled = true;
  };
  did: string[] = [];
  views: WizardView[] = [];
  clock = 0;

  deps(): FirstRunDeps {
    return {
      downloadsDir: "D",
      show: async (v) => void this.views.push(v),
      progress: async (fraction, label) => void this.did.push(`progress ${fraction ?? "?"} ${label}`),
      waitFor: async (actions) => {
        // At the end, "Ready to start": the user clicks Start unless a test
        // scripts otherwise.
        if (!this.clicks.length && this.views.at(-1)?.title === "Ready to start") return "continue";
        const click = this.clicks.shift();
        if (!click || !actions.includes(click)) throw new Error(`waiting for ${actions.join("/")}, clicked ${click}`);
        this.did.push(`click ${click}`);
        return click;
      },
      log: async () => {},
      machineChecks: async () => this.checks.shift() ?? OK,
      dockerState: async () => this.docker,
      dockerCli: () => this.cli,
      useDocker: (cli) => {
        this.did.push(`use ${cli}`);
        this.docker = "running";
      },
      ollamaAnswers: async () => this.ollama,
      isOllamaInstalled: () => this.ollamaInstalled,
      download: async (installer: Installer, dir, onProgress) => {
        this.did.push(`download ${installer.name}`);
        onProgress({ received: 50, total: 100 });
        onProgress({ received: 100, total: 100 });
        return `${dir}/${installer.file}`;
      },
      readSignature: async (path) => this.signatures[path] ?? null,
      runInstaller: async (installer) => {
        this.did.push(`run ${installer.name}`);
        const code = this.exitCodes[installer.name] ?? 0;
        if (code === 0) this.onInstall(installer.name);
        return code;
      },
      launch: (exe) => {
        this.did.push(`launch ${exe}`);
        this.onLaunch(exe);
      },
      dockerDesktopExe: () => "Docker Desktop.exe",
      ollamaAppExe: () => "ollama app.exe",
      missingModels: async () => [...this.missing],
      pullModel: async (name, onProgress) => {
        this.did.push(`pull ${name}`);
        onProgress({ completed: 5, total: 10 });
        onProgress({ completed: 10, total: 10 });
        this.missing = this.missing.filter((m) => m !== name);
      },
      hasApp: async () => this.app,
      createApp: async (openBrowser) => {
        this.did.push("create app");
        if (this.appError) {
          const error = this.appError;
          this.appError = null;
          throw error;
        }
        openBrowser("http://127.0.0.1:5555/?t=launch");
        this.app = true;
        return { installUrl: "https://github.com/apps/codelens-me/installations/new" };
      },
      openExternal: (url) => void this.did.push(`open ${url}`),
      doctor: async () => this.doctorChecks.shift() ?? OK,
      resumeAfterRestart: async (on) => void this.did.push(`resume ${on ? "on" : "off"}`),
      removeFile: async (path) => void this.did.push(`rm ${path}`),
      sleep: async (ms) => void (this.clock += ms),
      now: () => this.clock,
    };
  }
}

describe("first run", () => {
  it("goes straight through on a machine that has everything", async () => {
    const m = new Machine();
    expect(await firstRun(m.deps())).toBe("ready");
    expect(m.did.filter((d) => !d.startsWith("resume"))).toEqual([]);
    expect(m.views.at(-1)?.marks).toMatchObject({
      machine: "done",
      docker: "done",
      ollama: "done",
      models: "done",
      app: "done",
      start: "active",
    });
  });

  it("waits for a failed check to be fixed, and for a warning to be read", async () => {
    const m = new Machine();
    m.checks = [
      [{ name: "Virtualization", status: "fail", detail: "off" }],
      [{ name: "GPU", status: "warn", detail: "none" }],
    ];
    m.clicks = ["check-again", "continue"];
    expect(await firstRun(m.deps())).toBe("ready");
    expect(m.views[1]?.title).toBe("This computer needs a change first");
    expect(m.views[1]?.checks?.[0]?.status).toBe("fail");
    expect(m.views.at(-1)?.marks.machine).toBe("warn");
  });

  it("finds Docker Desktop's own docker.exe when the PATH doesn't have it yet", async () => {
    const m = new Machine();
    m.docker = "missing";
    m.cli = "C:/Program Files/Docker/Docker/resources/bin/docker.exe";
    expect(await firstRun(m.deps())).toBe("ready");
    expect(m.did).toContain(`use ${m.cli}`);
    expect(m.did.some((d) => d.startsWith("download"))).toBe(false);
  });

  it("starts Docker Desktop when it is installed but not running", async () => {
    const m = new Machine();
    m.docker = "stopped";
    expect(await firstRun(m.deps())).toBe("ready");
    expect(m.did).toContain("launch Docker Desktop.exe");
  });

  it("says so when Docker Desktop doesn't start", async () => {
    const m = new Machine();
    m.docker = "stopped";
    m.onLaunch = () => {}; // it never answers
    m.clicks = ["check-again"];
    const run = firstRun(m.deps());
    await expect(run).rejects.toThrow(/waiting for check-again, clicked undefined/);
    expect(m.views.map((v) => v.title)).toContain("Docker Desktop didn't start");
    expect(m.clock).toBeGreaterThanOrEqual(5 * 60_000);
  });

  it("installs Docker Desktop: downloaded, checked, run, started", async () => {
    const m = new Machine();
    m.docker = "missing";
    m.clicks = ["install-docker"];
    m.signatures["D/Docker Desktop Installer.exe"] = DOCKER_SIGNED;
    expect(await firstRun(m.deps())).toBe("ready");
    // The bar moved: half way, then full (the last update is never skipped).
    expect(m.did.filter((d) => d.startsWith("progress")).map((d) => d.split(" ")[1])).toEqual(["0.5", "1"]);
    expect(m.did.filter((d) => !d.startsWith("progress"))).toEqual([
      "click install-docker",
      "download Docker Desktop",
      "resume on", // before the installer, which may restart Windows itself
      "run Docker Desktop",
      "rm D/Docker Desktop Installer.exe",
      "launch Docker Desktop.exe",
      "resume off", // no restart was needed
    ]);
  });

  it("never runs an installer whose signature isn't the publisher's", async () => {
    const m = new Machine();
    m.docker = "missing";
    m.clicks = ["install-docker", "check-again"];
    m.signatures["D/Docker Desktop Installer.exe"] = { status: "Valid", subject: "CN=Evil Ltd, O=Evil Ltd" };
    await expect(firstRun(m.deps())).rejects.toThrow(/waiting for install-docker\/check-again/);
    expect(m.did).not.toContain("run Docker Desktop");
    expect(m.did).toContain("rm D/Docker Desktop Installer.exe");
    const refused = m.views.find((v) => v.title === "Docker Desktop wasn't installed");
    expect(refused?.detail).toContain("signed by Evil Ltd, not Docker Inc");
  });

  it("stops for a restart, with CodeLens set to open again after it", async () => {
    const m = new Machine();
    m.docker = "missing";
    m.clicks = ["install-docker"];
    m.signatures["D/Docker Desktop Installer.exe"] = DOCKER_SIGNED;
    m.exitCodes["Docker Desktop"] = RESTART_NEEDED;
    expect(await firstRun(m.deps())).toBe("restart");
    expect(m.did.indexOf("resume on")).toBeLessThan(m.did.indexOf("run Docker Desktop"));
    expect(m.did).not.toContain("resume off");
    expect(m.views.at(-1)?.title).toBe("Restart Windows to finish");
  });

  it("installs Ollama silently, for the user only, and waits for it", async () => {
    const m = new Machine();
    m.ollama = false;
    m.ollamaInstalled = false;
    m.onInstall = (name) => {
      if (name === "Ollama") {
        m.ollamaInstalled = true;
        m.ollama = true; // its installer starts it
      }
    };
    m.clicks = ["install-ollama"];
    m.signatures["D/OllamaSetup.exe"] = OLLAMA_SIGNED;
    expect(await firstRun(m.deps())).toBe("ready");
    expect(m.did).toContain("run Ollama");
    expect(m.did).not.toContain("launch ollama app.exe");
  });

  it("starts an installed Ollama that isn't running", async () => {
    const m = new Machine();
    m.ollama = false;
    expect(await firstRun(m.deps())).toBe("ready");
    expect(m.did).toContain("launch ollama app.exe");
  });

  it("explains a download that failed", async () => {
    const m = new Machine();
    m.ollama = false;
    m.ollamaInstalled = false;
    m.clicks = ["install-ollama", "check-again"];
    const deps = m.deps();
    deps.download = async () => {
      throw new Error("HTTP 503");
    };
    await expect(firstRun(deps)).rejects.toThrow(/waiting for install-ollama\/check-again/);
    expect(m.views.find((v) => v.title === "Ollama wasn't installed")?.detail).toContain("HTTP 503");
  });
});

describe("first run, second half (5b)", () => {
  it("downloads the missing models, one after the other, with a bar", async () => {
    const m = new Machine();
    m.missing = ["qwen3:8b", "qwen3-embedding:0.6b"];
    m.clicks = ["download-models"];
    expect(await firstRun(m.deps())).toBe("ready");
    expect(m.did.filter((d) => d.startsWith("pull"))).toEqual(["pull qwen3:8b", "pull qwen3-embedding:0.6b"]);
    expect(m.did).toContain("progress 0.5 qwen3:8b: 0 MB of 0 MB");
    expect(m.views.find((v) => v.title === "Download the models")?.detail).toContain("qwen3:8b and qwen3-embedding:0.6b");
  });

  it("creates the App in the browser, then waits for its installation", async () => {
    const m = new Machine();
    m.app = false;
    m.clicks = ["create-app", "installed"];
    expect(await firstRun(m.deps())).toBe("ready");
    expect(m.did.filter((d) => !d.startsWith("progress") && !d.startsWith("resume"))).toEqual([
      "click create-app",
      "create app",
      "open http://127.0.0.1:5555/?t=launch",
      "click installed",
    ]);
    const install = m.views.find((v) => v.title === "Install your App on your repositories");
    expect(install?.buttons?.[0]).toEqual({
      label: "Open the install page",
      href: "https://github.com/apps/codelens-me/installations/new",
    });
  });

  it("offers to try again when the App wasn't created", async () => {
    const m = new Machine();
    m.app = false;
    m.appError = new Error("timed out waiting for GitHub's redirect");
    m.clicks = ["create-app", "check-again", "create-app", "installed"];
    expect(await firstRun(m.deps())).toBe("ready");
    expect(m.views.find((v) => v.title === "The App wasn't created")?.detail).toContain("timed out");
    expect(m.did.filter((d) => d === "create app")).toHaveLength(2);
  });

  it("skips the App on a machine that already has one (a resumed wizard)", async () => {
    const m = new Machine();
    expect(await firstRun(m.deps())).toBe("ready");
    expect(m.did).not.toContain("create app");
  });

  it("shows the stack's checks last, with the install page if it isn't installed yet", async () => {
    const m = new Machine();
    m.doctorChecks = [
      [
        { name: "model qwen3:8b", status: "fail", detail: "not pulled" },
        {
          name: "GitHub App",
          status: "warn",
          detail: "codelens-me works, but isn't installed on any repo yet",
          fix: "install it: https://github.com/apps/codelens-me/installations/new",
        },
      ],
    ];
    m.clicks = ["check-again"];
    expect(await firstRun(m.deps())).toBe("ready");
    const notReady = m.views.find((v) => v.title === "Something isn't ready yet");
    expect(notReady?.buttons?.map((b) => b.label)).toEqual(["Check again", "Start anyway", "Open the install page"]);
    expect(notReady?.buttons?.[2]?.href).toBe("https://github.com/apps/codelens-me/installations/new");
    expect(m.views.filter((v) => v.title === "Ready to start")).toHaveLength(1);
  });
});
