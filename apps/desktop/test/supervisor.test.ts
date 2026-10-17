import { describe, expect, it } from "vitest";

import {
  Backoff,
  changes,
  type Health,
  isVolumeFromAnotherInstall,
  LONG_RUNNING,
  PAUSABLE,
  parseServiceStates,
  repairs,
  type ServiceState,
} from "../src/supervisor.js";

const running = (service: string, health = ""): ServiceState => ({
  service,
  state: "running",
  health,
  exitCode: 0,
});
const all = () => [
  ...LONG_RUNNING.map((s) => running(s, s === "api" || s === "web" ? "healthy" : "")),
  { service: "migrate", state: "exited", health: "", exitCode: 0 },
];

describe("parseServiceStates", () => {
  it("reads JSON lines (Compose 2.21+) and a JSON array (older)", () => {
    const line = (service: string, state: string) =>
      JSON.stringify({ Service: service, State: state, Health: "", ExitCode: 137, Name: "x" });
    const lines = `${line("api", "running")}\r\n${line("web", "exited")}\n`;
    expect(parseServiceStates(lines)).toEqual([
      { service: "api", state: "running", health: "", exitCode: 137 },
      { service: "web", state: "exited", health: "", exitCode: 137 },
    ]);
    expect(parseServiceStates(`[${line("api", "running")}]`)).toHaveLength(1);
    expect(parseServiceStates("  ")).toEqual([]);
  });
});

describe("repairs", () => {
  it("leaves a healthy stack alone (migrate is meant to exit)", () => {
    expect(repairs(all(), { paused: false })).toEqual([]);
  });

  it("starts what was killed or stopped, and what is missing", () => {
    const states = all()
      .filter((s) => s.service !== "poller")
      .map((s) => (s.service === "web" ? { ...s, state: "exited", exitCode: 137 } : s));
    expect(repairs(states, { paused: false })).toEqual([
      { service: "poller", action: "start", why: "missing" },
      { service: "web", action: "start", why: "exited (exit 137)" },
    ]);
  });

  it("restarts what runs but fails its health check", () => {
    const states = all().map((s) => (s.service === "api" ? { ...s, health: "unhealthy" } : s));
    expect(repairs(states, { paused: false })).toEqual([
      { service: "api", action: "restart", why: "unhealthy" },
    ]);
  });

  it("lets Docker finish what it is doing", () => {
    const states = all().map((s) =>
      s.service === "worker" ? { ...s, state: "restarting" } : s.service === "web" ? { ...s, health: "starting" } : s,
    );
    expect(repairs(states, { paused: false })).toEqual([]);
  });

  it("doesn't start what Pause stopped", () => {
    const states = all().map((s) => (PAUSABLE.includes(s.service) ? { ...s, state: "exited" } : s));
    expect(repairs(states, { paused: true })).toEqual([]);
    expect(repairs(states, { paused: false }).map((r) => r.service)).toEqual(PAUSABLE);
  });
});

describe("Backoff", () => {
  it("waits 5 s, then twice as long each time, up to 5 minutes", () => {
    const b = new Backoff();
    expect(b.ready("web", 0)).toBe(true);
    expect(b.tried("web", 0)).toBe(5_000);
    expect(b.ready("web", 4_999)).toBe(false);
    expect(b.ready("web", 5_000)).toBe(true);
    expect(b.tried("web", 5_000)).toBe(10_000);
    let delay = 0;
    for (let i = 0; i < 10; i++) delay = b.tried("web", 0);
    expect(delay).toBe(300_000);
  });

  it("forgets once the service has been fine for two minutes", () => {
    const b = new Backoff();
    b.tried("web", 0);
    b.tried("web", 0);
    b.fine("web", 10_000);
    b.fine("web", 60_000);
    expect(b.tried("web", 60_000)).toBe(20_000); // not settled yet
    b.fine("web", 70_000);
    b.fine("web", 190_000);
    expect(b.tried("web", 190_000)).toBe(5_000);
  });
});

describe("changes", () => {
  const h = (docker: Health["docker"], ollama: boolean): Health => ({ docker, ollama, repairing: [] });

  it("says when Docker or Ollama stops, and when they're back", () => {
    expect(changes(h("running", true), h("stopped", true))).toEqual([
      "Docker Desktop stopped: CodeLens is waiting for it.",
    ]);
    expect(changes(h("stopped", true), h("running", true))).toEqual([
      "Docker Desktop is back: starting CodeLens again.",
    ]);
    expect(changes(h("running", true), h("running", false))).toEqual([
      "Ollama isn't running: reviews will fail until it's started.",
    ]);
    expect(changes(h("running", false), h("running", true))).toEqual(["Ollama is running again."]);
  });

  it("says nothing twice, and the first look only what's wrong", () => {
    expect(changes(h("running", false), h("running", false))).toEqual([]);
    expect(changes(null, h("running", true))).toEqual([]);
    expect(changes(null, h("running", false))).toHaveLength(1);
  });
});

describe("isVolumeFromAnotherInstall", () => {
  it("recognises Postgres refusing this install's password", () => {
    const log =
      'migrate-1  | asyncpg.exceptions.InvalidPasswordError: password authentication failed for user "codelens"';
    expect(isVolumeFromAnotherInstall(log)).toBe(true);
    expect(isVolumeFromAnotherInstall("migrate-1  | connection refused")).toBe(false);
  });
});
