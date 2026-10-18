import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DEFAULTS, loadSettings, saveSettings } from "../src/settings.js";

describe("desktop settings", () => {
  let home: string;
  beforeEach(async () => {
    home = await mkdtemp(join(tmpdir(), "codelens-settings-"));
  });
  afterEach(async () => rm(home, { recursive: true, force: true }));

  it("starts from the defaults: not asked yet, not paused", async () => {
    expect(await loadSettings(home)).toEqual(DEFAULTS);
  });

  it("round-trips", async () => {
    const all = {
      askedStartAtLogin: true,
      paused: true,
      setupDone: true,
      installUrl: "https://github.com/apps/x/installations/new",
      checkForUpdates: false,
      lastVersion: "0.1.0",
    };
    await saveSettings(home, all);
    expect(await loadSettings(home)).toEqual(all);
  });

  it("falls back to the defaults for a broken file or wrong types", async () => {
    await writeFile(join(home, "desktop.json"), "{not json");
    expect(await loadSettings(home)).toEqual(DEFAULTS);
    await writeFile(join(home, "desktop.json"), '{"paused": "yes", "askedStartAtLogin": true}');
    expect(await loadSettings(home)).toEqual({ ...DEFAULTS, askedStartAtLogin: true });
  });
});
