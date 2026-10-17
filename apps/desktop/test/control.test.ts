import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { applyChanges, checkChanges, manageUrl, readSettings, type SettingsView } from "../src/appsettings.js";
import { checkRequest, startControl } from "../src/control.js";
import { parseRegQuery, readyForLong } from "../src/ollamaenv.js";

const ENV = "POSTGRES_PASSWORD=p\nGITHUB_APP_ID=5\nGITHUB_ALLOWED_ACCOUNTS=RAIF-KARANI:1,friend\nGITHUB_REVIEWS_PER_HOUR=7\n";
const EXTRAS = { pulled: { "qwen3:8b": true }, ollamaReadyForLong: false, installUrl: "https://github.com/apps/codelens-me/installations/new" };

describe("readSettings", () => {
  it("reads codelens.env, with CodeLens's defaults for what it doesn't set", () => {
    const view = readSettings(ENV, EXTRAS);
    expect(view.app).toEqual({
      configured: true,
      appId: "5",
      installUrl: EXTRAS.installUrl,
      manageUrl: "https://github.com/settings/apps/codelens-me",
      allowedAccounts: ["RAIF-KARANI:1", "friend"],
      reviewsPerHour: 7,
    });
    expect(view.events).toEqual({ pollIntervalS: 60, relay: "unavailable" });
    expect(view.models).toMatchObject({ llm: "qwen3:8b", contextWindow: 8192, pulled: { "qwen3:8b": true, "qwen3-embedding:0.6b": false } });
  });

  it("makes a settings link only from GitHub's own install page", () => {
    expect(manageUrl("https://evil.example/apps/x/installations/new")).toBeNull();
    expect(manageUrl(null)).toBeNull();
  });
});

describe("checkChanges", () => {
  it("accepts what Settings sends", () => {
    expect(checkChanges({ allowedAccounts: [" RAIF-KARANI:1 ", "friend", "friend"], reviewsPerHour: 0, pollIntervalS: 30 })).toEqual({
      ok: true,
      changes: { allowedAccounts: ["RAIF-KARANI:1", "friend"], reviewsPerHour: 0, pollIntervalS: 30 },
    });
    expect(checkChanges({ allowedAccounts: [] })).toEqual({ ok: true, changes: { allowedAccounts: [] } });
  });

  it.each([
    [{ reviewsPerHour: -1 }, "reviewsPerHour"],
    [{ reviewsPerHour: 1.5 }, "reviewsPerHour"],
    [{ pollIntervalS: 5 }, "pollIntervalS"],
    [{ pollIntervalS: "60" }, "pollIntervalS"],
    [{ allowedAccounts: ["bad login"] }, "allowedAccounts"],
    [{ allowedAccounts: ["-leading"] }, "allowedAccounts"],
    [{ allowedAccounts: ["a--b"] }, "allowedAccounts"],
    [{ allowedAccounts: ["x:id"] }, "allowedAccounts"],
    [{ allowedAccounts: "RAIF-KARANI" }, "allowedAccounts"],
    // Nothing else is settable this way: not the App's id, not a URL.
    [{ GITHUB_APP_ID: "6" }, "GITHUB_APP_ID"],
    [{ contextWindow: 32768 }, "contextWindow"],
  ])("refuses %j", (raw, field) => {
    const checked = checkChanges(raw);
    expect(checked.ok).toBe(false);
    expect(checked.ok ? {} : checked.errors).toHaveProperty(field);
  });

  it("refuses what isn't an object", () => {
    for (const raw of [null, [], "x", 3]) expect(checkChanges(raw).ok).toBe(false);
  });
});

describe("applyChanges", () => {
  it("writes the changes in place and keeps every other line", () => {
    const after = applyChanges(ENV, { reviewsPerHour: 0, pollIntervalS: 30, allowedAccounts: ["a:1"] });
    expect(after).toBe(
      "POSTGRES_PASSWORD=p\nGITHUB_APP_ID=5\nGITHUB_ALLOWED_ACCOUNTS=a:1\nGITHUB_REVIEWS_PER_HOUR=0\nGITHUB_POLL_INTERVAL_S=30\n",
    );
    expect(applyChanges(ENV, {})).toBe(ENV);
    // Read back, it's what was saved: the round trip.
    expect(readSettings(after, EXTRAS).app.reviewsPerHour).toBe(0);
    expect(readSettings(after, EXTRAS).events.pollIntervalS).toBe(30);
  });
});

describe("the control API", () => {
  const KEY = "k".repeat(43);
  const view = readSettings(ENV, EXTRAS);
  let api: Awaited<ReturnType<typeof startControl>>;
  let saved: unknown[];

  beforeEach(async () => {
    saved = [];
    api = await startControl(
      {
        getSettings: async () => view,
        saveSettings: async (body) => {
          saved.push(body);
          const checked = checkChanges(body);
          return checked.ok ? { settings: view as SettingsView } : { errors: checked.errors };
        },
        prepareLongWindow: async () => view,
        getSystem: async () => { throw new Error("unused"); },
        getLogs: async () => [],
        restartService: async () => {},
        clearMirrors: async () => { throw new Error("unused"); },
        openDataFolder: async () => {},
      },
      KEY,
    );
  });
  afterEach(async () => api.close());

  const call = (path: string, init: RequestInit & { host?: string; key?: string | null } = {}) => {
    const headers = new Headers(init.headers);
    if (init.key !== null) headers.set("Authorization", `Bearer ${init.key ?? KEY}`);
    // fetch won't set Host; a raw request would. The check reads the header Node sees.
    return fetch(`http://127.0.0.1:${api.port}${path}`, { ...init, headers });
  };

  it("answers the dashboard's server, with the key", async () => {
    const response = await call("/v1/settings");
    expect(response.status).toBe(200);
    expect(((await response.json()) as SettingsView).app.reviewsPerHour).toBe(7);
  });

  it("refuses a request without the key, or with another", async () => {
    expect((await call("/v1/settings", { key: null })).status).toBe(403);
    expect((await call("/v1/settings", { key: "guess" })).status).toBe(403);
  });

  it("checks the Host it was called by (DNS rebinding)", () => {
    expect(checkRequest(`host.docker.internal:${api.port}`, `Bearer ${KEY}`, api.port, KEY)).toBeNull();
    expect(checkRequest(`127.0.0.1:${api.port}`, `Bearer ${KEY}`, api.port, KEY)).toBeNull();
    expect(checkRequest(`evil.example:${api.port}`, `Bearer ${KEY}`, api.port, KEY)).toBe("bad_host");
    expect(checkRequest(`localhost:${api.port}`, `Bearer ${KEY}`, api.port, KEY)).toBe("bad_host");
    expect(checkRequest(`127.0.0.1:${api.port}`, KEY, api.port, KEY)).toBe("bad_key");
  });

  it("saves, or says which field was refused", async () => {
    const ok = await call("/v1/settings", { method: "PUT", body: JSON.stringify({ reviewsPerHour: 3 }) });
    expect(ok.status).toBe(200);
    const refused = await call("/v1/settings", { method: "PUT", body: JSON.stringify({ reviewsPerHour: -3 }) });
    expect(refused.status).toBe(422);
    expect(await refused.json()).toMatchObject({ error: { code: "invalid", details: { reviewsPerHour: expect.any(String) } } });
    expect(saved).toEqual([{ reviewsPerHour: 3 }, { reviewsPerHour: -3 }]);
  });

  it("refuses bodies that aren't a few settings, and unknown routes", async () => {
    expect((await call("/v1/settings", { method: "PUT", body: "not json" })).status).toBe(400);
    expect((await call("/v1/settings", { method: "PUT", body: "x".repeat(20_000) })).status).toBe(413);
    expect((await call("/v1/anything")).status).toBe(404);
    expect(saved).toEqual([]);
  });
});

describe("the user's Ollama settings", () => {
  it("reads them as `reg query` prints them", () => {
    // Copied from this machine (step 6a).
    const out = "\r\nHKEY_CURRENT_USER\\Environment\r\n    Path    REG_EXPAND_SZ    C:\\x;C:\\y\r\n    OLLAMA_FLASH_ATTENTION    REG_SZ    1\r\n    OLLAMA_KV_CACHE_TYPE    REG_SZ    q8_0\r\n";
    const env = parseRegQuery(out);
    expect(env.get("OLLAMA_KV_CACHE_TYPE")).toBe("q8_0");
    expect(readyForLong(env)).toBe(true);
    env.set("OLLAMA_KV_CACHE_TYPE", "f16");
    expect(readyForLong(env)).toBe(false);
    expect(readyForLong(new Map())).toBe(false);
  });
});
