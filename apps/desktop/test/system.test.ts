import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { readSettings } from "../src/appsettings.js";
import { startControl } from "../src/control.js";
import { folderBytes, isService, lineCount, type SystemView, tailFile } from "../src/system.js";

describe("the data folder", () => {
  let dir: string;
  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), "codelens-system-"));
  });
  afterEach(async () => rm(dir, { recursive: true, force: true }));

  it("adds up a folder's files, nested, and an absent one is 0", async () => {
    await mkdir(join(dir, "repos", "1.git", "objects"), { recursive: true });
    await writeFile(join(dir, "repos", "1.git", "HEAD"), "x".repeat(10));
    await writeFile(join(dir, "repos", "1.git", "objects", "pack"), "y".repeat(1000));
    expect(await folderBytes(join(dir, "repos"))).toBe(1010);
    expect(await folderBytes(join(dir, "missing"))).toBe(0);
  });

  it("tails a log: the last lines, from the file's end only", async () => {
    const file = join(dir, "api.log");
    await writeFile(file, Array.from({ length: 50 }, (_, i) => `line ${i}`).join("\r\n") + "\r\n");
    expect(await tailFile(file, 3)).toEqual(["line 47", "line 48", "line 49"]);
    // Reading only the last 20 bytes: the cut line at the start is dropped.
    expect(await tailFile(file, 100, 20)).toEqual(["line 48", "line 49"]);
    expect(await tailFile(join(dir, "none.log"), 5)).toEqual([]);
  });

  it("knows the services by name, and nothing else", () => {
    expect(isService("worker-gpu")).toBe(true);
    expect(isService("migrate")).toBe(true);
    for (const name of ["../codelens.env", "tools", "api.log", "", null, 3]) expect(isService(name), String(name)).toBe(false);
  });

  it("asks for 1-1000 lines, 200 by default", () => {
    expect(lineCount(null)).toBe(200);
    expect(lineCount("50")).toBe(50);
    expect(lineCount("99999")).toBe(1000);
    expect(lineCount("0")).toBe(200);
    expect(lineCount("x")).toBe(200);
  });
});

describe("the control API's System routes", () => {
  const KEY = "k".repeat(43);
  const system: SystemView = {
    docker: "running",
    ollama: true,
    paused: false,
    services: [{ service: "api", state: "running", health: "healthy", exitCode: 0 }],
    storage: { dataFolder: "C:\\CodeLens", mirrorsBytes: 1, logsBytes: 2, databaseBytes: 3, freeBytes: 4 },
  };
  let api: Awaited<ReturnType<typeof startControl>>;
  let did: string[];

  beforeEach(async () => {
    did = [];
    const updates = { current: "0.1.0", checkForUpdates: true, status: "idle" as const, available: null, progress: null, lastChecked: null, error: null };
    const settings = readSettings("", { pulled: {}, ollamaReadyForLong: false, installUrl: null, updates });
    api = await startControl(
      {
        getSettings: async () => settings,
        saveSettings: async () => ({ settings }),
        prepareLongWindow: async () => settings,
        getSystem: async () => system,
        getLogs: async (service, lines) => {
          did.push(`logs ${service} ${lines}`);
          return ["one", "two"];
        },
        restartService: async (service) => void did.push(`restart ${service}`),
        clearMirrors: async () => {
          did.push("clear");
          return system;
        },
        openDataFolder: async () => void did.push("open"),
        checkForUpdates: async () => updates,
        installUpdate: async () => updates,
      },
      KEY,
    );
  });
  afterEach(async () => api.close());

  const call = (path: string, init: RequestInit = {}, key: string | null = KEY) =>
    fetch(`http://127.0.0.1:${api.port}${path}`, {
      ...init,
      headers: key ? { Authorization: `Bearer ${key}` } : {},
    });

  it("reports the system, and each service's log by name", async () => {
    expect(((await (await call("/v1/system")).json()) as SystemView).storage.databaseBytes).toBe(3);
    const logs = await call("/v1/system/logs?service=worker-gpu&lines=50");
    expect(await logs.json()).toEqual({ service: "worker-gpu", lines: ["one", "two"] });
    expect(did).toEqual(["logs worker-gpu 50"]);
  });

  it("never reads a file a name doesn't stand for", async () => {
    for (const service of ["..%2Fcodelens.env", "../codelens.env", "tools", ""]) {
      expect((await call(`/v1/system/logs?service=${service}`)).status, service).toBe(404);
    }
    expect(did).toEqual([]);
  });

  it("restarts a known service only, clears mirrors and opens the folder", async () => {
    expect((await call("/v1/system/restart", { method: "POST", body: JSON.stringify({ service: "web" }) })).status).toBe(200);
    expect((await call("/v1/system/restart", { method: "POST", body: JSON.stringify({ service: "rm -rf" }) })).status).toBe(404);
    expect((await call("/v1/system/clear-mirrors", { method: "POST" })).status).toBe(200);
    expect((await call("/v1/system/open-data-folder", { method: "POST" })).status).toBe(200);
    expect(did).toEqual(["restart web", "clear", "open"]);
  });

  it("refuses all of it without the key", async () => {
    for (const [path, method] of [["/v1/system", "GET"], ["/v1/system/clear-mirrors", "POST"], ["/v1/system/open-data-folder", "POST"]]) {
      expect((await call(path as string, { method }, null)).status).toBe(403);
    }
    expect(did).toEqual([]);
  });
});
