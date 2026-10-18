/** What the dashboard's Settings page shows and may change (planning-v2.md
 * Phase 11 step 6a, D175): CodeLens's own settings, kept in codelens.env like
 * every other, read and written here, in the app, which alone can apply them
 * (by recreating the services). The page only asks: every change is checked
 * here before anything is written. */

import { mergeEnv } from "./githubapp.js";
import { parseEnv } from "./stack.js";
import type { UpdateView } from "./updates.js";

/** The context windows CodeLens is measured at: 8k works with any Ollama
 * settings (D66); 16k needs flash attention and a q8_0 KV cache to fit an
 * 8 GB GPU (measured, D66, D146). */
export const WINDOWS = { short: 8192, long: 16384 } as const;

export type SettingsView = {
  models: {
    llm: string;
    embed: string;
    /** Per model: is it in the user's Ollama? */
    pulled: Record<string, boolean>;
    contextWindow: number;
    /** The user's Ollama has the settings a 16k window needs. */
    ollamaReadyForLong: boolean;
  };
  app: {
    configured: boolean;
    appId: string | null;
    /** GitHub's install page for the App, and its settings page. */
    installUrl: string | null;
    manageUrl: string | null;
    allowedAccounts: string[];
    reviewsPerHour: number;
  };
  events: {
    pollIntervalS: number;
    /** Webhooks through a relay: not offered by the app yet (D175). */
    relay: "unavailable";
  };
  /** This version and the update check (step 7b); the switch is desktop.json's. */
  updates: UpdateView;
};

export type SettingsChanges = {
  allowedAccounts?: string[];
  reviewsPerHour?: number;
  pollIntervalS?: number;
  contextWindow?: number;
  /** desktop.json, not codelens.env: no service restarts for it. */
  checkForUpdates?: boolean;
};

/** The changes that are codelens.env's, which need the services recreated. */
export function envChanges(changes: SettingsChanges): SettingsChanges {
  const { checkForUpdates: _, ...env } = changes;
  return env;
}

const DEFAULTS = { llm: "qwen3:8b", embed: "qwen3-embedding:0.6b", reviewsPerHour: 10, pollIntervalS: 60 };

/** A GitHub login (letters, digits, single hyphens, at most 39), optionally
 * pinned to the account's id: what Accounts.parse (core/accounts.py) takes. */
const ACCOUNT = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}(?::\d{1,12})?$/;

function int(value: string | undefined, fallback: number): number {
  const n = Number(value);
  return value !== undefined && value !== "" && Number.isInteger(n) ? n : fallback;
}

/** The App's settings page, from its install page's address. */
export function manageUrl(installUrl: string | null): string | null {
  const slug = installUrl?.match(/^https:\/\/github\.com\/apps\/([a-z0-9-]+)\/installations\/new$/)?.[1];
  return slug ? `https://github.com/settings/apps/${slug}` : null;
}

export function readSettings(
  envText: string,
  extras: { pulled: Record<string, boolean>; ollamaReadyForLong: boolean; installUrl: string | null; updates: UpdateView },
): SettingsView {
  const env = parseEnv(envText);
  const llm = env.get("LLM_MODEL") || DEFAULTS.llm;
  const embed = env.get("EMBED_MODEL") || DEFAULTS.embed;
  const accounts = (env.get("GITHUB_ALLOWED_ACCOUNTS") ?? "")
    .split(",")
    .map((a) => a.trim())
    .filter(Boolean);
  return {
    models: {
      llm,
      embed,
      pulled: { [llm]: extras.pulled[llm] ?? false, [embed]: extras.pulled[embed] ?? false },
      contextWindow: int(env.get("LLM_NUM_CTX"), WINDOWS.short),
      ollamaReadyForLong: extras.ollamaReadyForLong,
    },
    app: {
      configured: Boolean(env.get("GITHUB_APP_ID")),
      appId: env.get("GITHUB_APP_ID") || null,
      installUrl: extras.installUrl,
      manageUrl: manageUrl(extras.installUrl),
      allowedAccounts: accounts,
      reviewsPerHour: int(env.get("GITHUB_REVIEWS_PER_HOUR"), DEFAULTS.reviewsPerHour),
    },
    events: { pollIntervalS: int(env.get("GITHUB_POLL_INTERVAL_S"), DEFAULTS.pollIntervalS), relay: "unavailable" },
    updates: extras.updates,
  };
}

export type Checked = { ok: true; changes: SettingsChanges } | { ok: false; errors: Record<string, string> };

/** The page's request, checked field by field: anything unknown, or out of
 * range, is refused with a reason the page can show next to the field. */
export function checkChanges(raw: unknown): Checked {
  const errors: Record<string, string> = {};
  const changes: SettingsChanges = {};
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return { ok: false, errors: { form: "expected an object" } };
  for (const key of Object.keys(raw)) {
    if (!["allowedAccounts", "reviewsPerHour", "pollIntervalS", "contextWindow", "checkForUpdates"].includes(key)) {
      errors[key] = "isn't a setting";
    }
  }
  const r = raw as Record<string, unknown>;
  if ("allowedAccounts" in r) {
    const list = r.allowedAccounts;
    if (!Array.isArray(list) || !list.every((a) => typeof a === "string")) {
      errors.allowedAccounts = "expected a list of GitHub logins";
    } else {
      const accounts = list.map((a: string) => a.trim()).filter(Boolean);
      const bad = accounts.find((a) => !ACCOUNT.test(a));
      if (bad !== undefined) errors.allowedAccounts = `"${bad.slice(0, 50)}" isn't a GitHub login (or login:id)`;
      else if (accounts.length > 50) errors.allowedAccounts = "at most 50 accounts";
      else changes.allowedAccounts = [...new Set(accounts)];
    }
  }
  const number = (key: "reviewsPerHour" | "pollIntervalS", min: number, max: number, what: string) => {
    if (!(key in r)) return;
    const n = r[key];
    if (typeof n !== "number" || !Number.isInteger(n) || n < min || n > max) errors[key] = `${what}: a whole number from ${min} to ${max}`;
    else changes[key] = n;
  };
  number("reviewsPerHour", 0, 1000, "Reviews per hour");
  number("pollIntervalS", 10, 3600, "Seconds between looks");
  if ("contextWindow" in r) {
    if (r.contextWindow !== WINDOWS.short && r.contextWindow !== WINDOWS.long) errors.contextWindow = "8192 or 16384";
    else changes.contextWindow = r.contextWindow;
  }
  if ("checkForUpdates" in r) {
    if (typeof r.checkForUpdates !== "boolean") errors.checkForUpdates = "on or off";
    else changes.checkForUpdates = r.checkForUpdates;
  }
  return Object.keys(errors).length ? { ok: false, errors } : { ok: true, changes };
}

/** codelens.env with the changes written in (other lines kept). */
export function applyChanges(envText: string, changes: SettingsChanges): string {
  const values = new Map<string, string>();
  if (changes.allowedAccounts) values.set("GITHUB_ALLOWED_ACCOUNTS", changes.allowedAccounts.join(","));
  if (changes.reviewsPerHour !== undefined) values.set("GITHUB_REVIEWS_PER_HOUR", String(changes.reviewsPerHour));
  if (changes.pollIntervalS !== undefined) values.set("GITHUB_POLL_INTERVAL_S", String(changes.pollIntervalS));
  if (changes.contextWindow !== undefined) values.set("LLM_NUM_CTX", String(changes.contextWindow));
  return values.size ? mergeEnv(envText, values) : envText;
}
