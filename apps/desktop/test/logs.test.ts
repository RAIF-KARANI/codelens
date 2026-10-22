import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { appendLog, MAX_LOG_BYTES, ServiceLogs, splitLogLine, stopFollowersScript } from "../src/logs.js";

const P = "codelens-desktop";

describe("splitLogLine", () => {
  it("names the service from Compose v2's prefix, as `docker compose logs` prints it", () => {
    // Copied from this machine's Compose (step 4): "<service>-<n>  | ...".
    expect(splitLogLine("redis-1  | 1:M 17 Oct 2026 15:36:12.987 * DB saved on disk", P)).toEqual([
      "redis",
      "1:M 17 Oct 2026 15:36:12.987 * DB saved on disk",
    ]);
    expect(splitLogLine("worker-gpu-1  | ready", P)).toEqual(["worker-gpu", "ready"]);
    expect(splitLogLine("web-1  |", P)).toEqual(["web", ""]);
  });

  it("drops an older Compose's project prefix", () => {
    expect(splitLogLine("codelens-desktop-api-1  | GET /health 200", P)).toEqual(["api", "GET /health 200"]);
  });

  it("skips what isn't a service's line", () => {
    expect(splitLogLine("no bar here", P)).toBeNull();
    expect(splitLogLine("api  | no replica number", P)).toBeNull();
    expect(splitLogLine("../../x-1  | a path is never a service", P)).toBeNull();
  });
});

describe("ServiceLogs", () => {
  let dir: string;
  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), "codelens-logs-"));
  });
  afterEach(async () => rm(dir, { recursive: true, force: true }));

  it("writes each service's lines to its own file, across chunk boundaries", async () => {
    const logs = new ServiceLogs(dir, P);
    logs.write("api-1  | one\r\nweb-1  | tw");
    logs.write("o\napi-1  | three\n");
    expect(await readFile(join(dir, "api.log"), "utf8")).toBe("one\nthree\n");
    expect(await readFile(join(dir, "web.log"), "utf8")).toBe("two\n");
  });

  it("moves a full file aside, keeping one older file", async () => {
    await writeFile(join(dir, "api.log"), "x".repeat(MAX_LOG_BYTES + 1));
    const logs = new ServiceLogs(dir, P);
    logs.write("api-1  | fresh\n");
    expect(await readFile(join(dir, "api.log"), "utf8")).toBe("fresh\n");
    expect((await stat(join(dir, "api.log.1"))).size).toBe(MAX_LOG_BYTES + 1);
  });
});

describe("stopFollowersScript", () => {
  it("targets this project's log followers only, by their command line", () => {
    const script = stopFollowersScript("codelens-desktop");
    expect(script).toContain("$_.CommandLine -match ' -p codelens-desktop '");
    expect(script).toContain("$_.CommandLine -match ' logs --follow'");
    expect(script).toContain("Name='docker-compose.exe' OR Name='docker.exe'");
  });

  it("refuses a project name that could change the script", () => {
    expect(() => stopFollowersScript("x'; Remove-Item C:\ -Recurse; '")).toThrow();
  });
});

describe("appendLog (security audit run-2)", () => {
  it("moves the app's own log aside past MAX_LOG_BYTES", async () => {
    const dir = await mkdtemp(join(tmpdir(), "codelens-applog-"));
    try {
      const file = join(dir, "desktop.log");
      await writeFile(file, "x".repeat(MAX_LOG_BYTES + 1));
      await appendLog(file, "next line\n");
      expect(await readFile(file, "utf8")).toBe("next line\n");
      expect((await stat(`${file}.1`)).size).toBe(MAX_LOG_BYTES + 1);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
});
