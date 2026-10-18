import { describe, expect, it } from "vitest";

import { releasePage, shortError, type UpdateDeps, Updates, type UpdaterLike } from "../src/updates.js";

type Listener = (arg: never) => void;

/** GitHub as electron-updater sees it: a newer release or not, a download
 * that works or fails. The user's answers in order. */
function world(opts: { latest?: string | null; downloadFails?: boolean; checkFails?: boolean; answers?: number[] }) {
  const listeners = new Map<string, Listener[]>();
  const emit = (event: string, arg: unknown) => listeners.get(event)?.forEach((l) => l(arg as never));
  const did: string[] = [];
  const asked: { message: string; buttons: string[] }[] = [];
  const answers = [...(opts.answers ?? [])];
  const updater: UpdaterLike = {
    on: ((event: string, listener: Listener) => {
      listeners.set(event, [...(listeners.get(event) ?? []), listener]);
    }) as UpdaterLike["on"],
    checkForUpdates: async () => {
      did.push("check");
      if (opts.checkFails) throw new Error("getaddrinfo ENOTFOUND api.github.com\nmore detail");
      if (opts.latest === null) return null; // skipped: not packaged
      if (opts.latest) emit("update-available", { version: opts.latest });
      else emit("update-not-available", { version: "0.1.0" });
      return {};
    },
    downloadUpdate: async () => {
      did.push("download");
      if (opts.downloadFails) throw new Error("sha512 checksum mismatch");
      emit("download-progress", { percent: 42.4 });
      emit("update-downloaded", { version: opts.latest });
      return [];
    },
    quitAndInstall: (silent, run) => void did.push(`install silent=${silent} run=${run}`),
  };
  const deps: UpdateDeps = {
    updater,
    current: "0.1.0",
    ask: async (message, _detail, buttons) => {
      asked.push({ message, buttons });
      return answers.shift() ?? 1;
    },
    openExternal: (url) => void did.push(`open ${url}`),
    beforeInstall: async () => void did.push("stop stack"),
    onChange: () => {},
    log: () => {},
    now: () => new Date("2026-10-18T12:00:00Z"),
  };
  return { deps, did, asked };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("updates", () => {
  it("asks, downloads, asks again, stops the stack, then installs", async () => {
    const w = world({ latest: "0.2.0", answers: [0, 0] });
    const u = new Updates(w.deps, true);
    await u.checkAutomatically();
    await settle();
    expect(w.asked.map((a) => a.message)).toEqual(["CodeLens 0.2.0 is available", "Restart CodeLens to update to 0.2.0?"]);
    expect(w.did).toEqual(["check", "download", "stop stack", "install silent=true run=true"]);
    expect(u.state).toMatchObject({ status: "ready", available: "0.2.0", progress: 100 });
  });

  it("does nothing without a yes, and doesn't ask twice about one version", async () => {
    const w = world({ latest: "0.2.0", answers: [1] });
    const u = new Updates(w.deps, true);
    await u.checkAutomatically();
    await settle();
    await u.checkAutomatically(); // the next day
    await settle();
    expect(w.did).toEqual(["check", "check"]);
    expect(w.asked).toHaveLength(1);
    expect(u.state).toMatchObject({ status: "available", available: "0.2.0" });
  });

  it("restarts only on the second yes; Later keeps it ready for the tray", async () => {
    const w = world({ latest: "0.2.0", answers: [0, 1] });
    const u = new Updates(w.deps, true);
    await u.checkAutomatically();
    await settle();
    expect(w.did).toEqual(["check", "download"]);
    expect(u.state.status).toBe("ready");
    w.asked.length = 0;
    await u.install(); // "Restart to update…" in the tray
    expect(w.asked.map((a) => a.message)).toEqual(["Restart CodeLens to update to 0.2.0?"]);
  });

  it("opens the release's page for What's new", async () => {
    const w = world({ latest: "0.2.0", answers: [2] });
    await new Updates(w.deps, true).checkAutomatically();
    await settle();
    expect(w.did).toContain(`open ${releasePage("0.2.0")}`);
    expect(releasePage("0.2.0")).toBe("https://github.com/RAIF-KARANI/codelens/releases/tag/v0.2.0");
  });

  it("never looks by itself when turned off, but Check now still works", async () => {
    const w = world({ latest: undefined });
    const u = new Updates(w.deps, false);
    await u.checkAutomatically();
    expect(w.did).toEqual([]);
    await u.checkNow();
    expect(w.did).toEqual(["check"]);
    expect(w.asked.map((a) => a.message)).toEqual(["CodeLens is up to date"]);
    expect(u.state.status).toBe("up-to-date");
  });

  it("stays quiet about errors it looked for by itself, and says them when asked", async () => {
    const quiet = world({ checkFails: true });
    const u = new Updates(quiet.deps, true);
    await u.checkAutomatically();
    expect(quiet.asked).toEqual([]);
    expect(u.state).toMatchObject({ status: "error", error: "GitHub couldn't be reached" });

    const loud = world({ checkFails: true });
    await new Updates(loud.deps, true).checkNow();
    expect(loud.asked.map((a) => a.message)).toEqual(["CodeLens couldn't look for updates"]);
  });

  it("keeps the version on offer when its download fails, and says so", async () => {
    const w = world({ latest: "0.2.0", downloadFails: true, answers: [0] });
    const u = new Updates(w.deps, true);
    await u.checkAutomatically();
    await settle();
    expect(w.did).not.toContain("stop stack");
    expect(w.asked.at(-1)?.message).toBe("The update didn't download");
    expect(u.state).toMatchObject({ status: "available", error: "sha512 checksum mismatch" });
  });

  it("goes back to idle when electron-updater skips the look (development)", async () => {
    const w = world({ latest: null });
    const u = new Updates(w.deps, true);
    await u.checkNow();
    expect(u.state.status).toBe("idle");
  });

  it("shortens errors to their first line", () => {
    expect(shortError(new Error("HttpError: 404\nheaders..."))).toBe("HttpError: 404");
    expect(shortError("x".repeat(300))).toHaveLength(158);
  });
});
