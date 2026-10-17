import "server-only";

/** CodeLens Desktop's control API (planning-v2.md D175), called from this
 * server only, in local mode: how the Settings page asks the app to read and
 * change codelens.env. The app checks everything; this file only carries the
 * request, with the launch's key the app put in this container's environment
 * (CODELENS_CONTROL_URL, CODELENS_CONTROL_KEY). The browser never sees it. */

export type SettingsView = {
  models: {
    llm: string;
    embed: string;
    pulled: Record<string, boolean>;
    contextWindow: number;
    ollamaReadyForLong: boolean;
  };
  app: {
    configured: boolean;
    appId: string | null;
    installUrl: string | null;
    manageUrl: string | null;
    allowedAccounts: string[];
    reviewsPerHour: number;
  };
  events: { pollIntervalS: number; relay: "unavailable" };
};

export class ControlError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    /** Per field, why the app refused it. */
    readonly details: Record<string, string> = {},
  ) {
    super(message);
  }
}

export async function control<T>(path: string, init: RequestInit = {}): Promise<T> {
  const url = process.env.CODELENS_CONTROL_URL;
  const key = process.env.CODELENS_CONTROL_KEY;
  if (!url || !key) throw new ControlError(503, "not_configured", "this dashboard isn't running in the CodeLens app");
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${key}`);
  if (init.body) headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`${url.replace(/\/$/, "")}/v1${path}`, { ...init, headers, cache: "no-store" });
  } catch {
    throw new ControlError(503, "unreachable", "the CodeLens app didn't answer");
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { code?: string; message?: string; details?: Record<string, string> };
    } | null;
    throw new ControlError(
      response.status,
      body?.error?.code ?? "http_error",
      body?.error?.message ?? `HTTP ${response.status}`,
      body?.error?.details ?? {},
    );
  }
  return (await response.json()) as T;
}

export const getSettings = () => control<SettingsView>("/settings");

/** One service as `docker compose ps` reports it. */
export type ServiceState = { service: string; state: string; health: string; exitCode: number };

export type SystemView = {
  docker: "missing" | "stopped" | "running";
  ollama: boolean;
  paused: boolean;
  services: ServiceState[];
  storage: {
    dataFolder: string;
    mirrorsBytes: number;
    logsBytes: number;
    databaseBytes: number | null;
    freeBytes: number | null;
  };
};

export const getSystem = () => control<SystemView>("/system");

export const getLogs = (service: string, lines = 200) =>
  control<{ service: string; lines: string[] }>(
    `/system/logs?${new URLSearchParams({ service, lines: String(lines) })}`,
  );

/** The services whose logs the page offers, in the order a reader looks for
 * them (the app checks the name again). */
export const LOG_SERVICES = ["api", "worker", "worker-gpu", "poller", "web", "postgres", "redis", "migrate"] as const;

/** A list of accounts as people type it: commas, spaces or new lines. */
export function parseAccounts(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((a) => a.trim())
    .filter(Boolean);
}
