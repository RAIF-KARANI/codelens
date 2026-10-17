/** Keeping the stack up while the app runs (planning-v2.md Phase 11 step 4).
 *
 * Every TICK_MS it asks Docker how things are and does the least that fixes
 * them:
 * - Docker not answering (quit, crashed, the machine waking up): say so, and
 *   wait. When it's back, start the stack again.
 * - A service that should run but doesn't (killed, stopped, removed): start it
 *   again, with backoff. Docker's own `restart: unless-stopped` restarts a
 *   service that crashes; it doesn't restart one stopped or killed by hand
 *   (measured), nor one that runs but fails its health check.
 * - Ollama, on the host, not answering: say so; reviews would fail (and say
 *   so on the PR, D171), but nothing here can start the user's own Ollama.
 *
 * The decisions are plain functions over what Docker reports, so they are
 * tested without Docker. */

/** How often the supervisor looks. */
export const TICK_MS = 10_000;

/** What should be running once the stack is up (migrate runs once, then
 * exits). */
export const LONG_RUNNING = ["postgres", "redis", "api", "worker", "worker-gpu", "poller", "web"];

/** What Pause stops: everything that does work. The dashboard, the API and
 * the data stay up, so past reviews can still be read. */
export const PAUSABLE = ["worker", "worker-gpu", "poller"];

/** Backoff between two attempts to bring one service back: 5 s doubling to
 * 5 minutes; reset once it has been fine for a while. */
const FIRST_RETRY_MS = 5_000;
const MAX_RETRY_MS = 5 * 60_000;
const SETTLED_MS = 2 * 60_000;

export type ServiceState = {
  service: string;
  state: string; // running | exited | restarting | created | paused | dead | removing
  health: string; // healthy | unhealthy | starting | "" (no health check)
  exitCode: number;
};

/** `docker compose ps --format json` as states: JSON lines, or one array. */
export function parseServiceStates(output: string): ServiceState[] {
  const text = output.trim();
  if (!text) return [];
  const rows: unknown[] = text.startsWith("[")
    ? (JSON.parse(text) as unknown[])
    : text.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line) as unknown);
  return rows.map((row) => {
    const r = row as Record<string, unknown>;
    return {
      service: String(r.Service ?? ""),
      state: String(r.State ?? ""),
      health: String(r.Health ?? ""),
      exitCode: Number(r.ExitCode ?? 0),
    };
  });
}

export type Repair = { service: string; action: "start" | "restart"; why: string };

/** What to do about each service that should be running. `restarting` and a
 * health check still `starting` are Docker's own work in progress: left
 * alone. */
export function repairs(states: ServiceState[], { paused }: { paused: boolean }): Repair[] {
  const expected = LONG_RUNNING.filter((s) => !(paused && PAUSABLE.includes(s)));
  const found = new Map(states.map((s) => [s.service, s]));
  const out: Repair[] = [];
  for (const service of expected) {
    const s = found.get(service);
    if (!s) {
      out.push({ service, action: "start", why: "missing" });
    } else if (["exited", "dead", "created", "paused"].includes(s.state)) {
      out.push({ service, action: "start", why: `${s.state} (exit ${s.exitCode})` });
    } else if (s.state === "running" && s.health === "unhealthy") {
      out.push({ service, action: "restart", why: "unhealthy" });
    }
  }
  return out;
}

/** When each service may be tried again. */
export class Backoff {
  private next = new Map<string, { at: number; delay: number }>();
  private fineSince = new Map<string, number>();

  ready(service: string, now: number): boolean {
    const entry = this.next.get(service);
    return !entry || now >= entry.at;
  }

  /** An attempt was made: the next one waits twice as long as the last. */
  tried(service: string, now: number): number {
    const delay = Math.min((this.next.get(service)?.delay ?? FIRST_RETRY_MS / 2) * 2, MAX_RETRY_MS);
    this.next.set(service, { at: now + delay, delay });
    this.fineSince.delete(service);
    return delay;
  }

  /** The service looks fine; after SETTLED_MS of that, its backoff is forgotten. */
  fine(service: string, now: number): void {
    const since = this.fineSince.get(service) ?? now;
    this.fineSince.set(service, since);
    if (now - since >= SETTLED_MS) this.next.delete(service);
  }
}

/** What the app shows about the machine, from the supervisor's last look. */
export type Health = {
  docker: "missing" | "stopped" | "running";
  ollama: boolean;
  /** Services being brought back, and why. */
  repairing: Repair[];
};

/** What changed between two looks that the user should hear about. */
export function changes(before: Health | null, now: Health): string[] {
  const said: string[] = [];
  if (before?.docker === "running" && now.docker !== "running") {
    said.push("Docker Desktop stopped: CodeLens is waiting for it.");
  }
  if (before && before.docker !== "running" && now.docker === "running") {
    said.push("Docker Desktop is back: starting CodeLens again.");
  }
  if ((before === null || before.ollama) && !now.ollama) {
    said.push("Ollama isn't running: reviews will fail until it's started.");
  }
  if (before && !before.ollama && now.ollama) {
    said.push("Ollama is running again.");
  }
  return said;
}

/** Postgres refusing the generated password: the database volume was made
 * by an earlier install whose data folder is gone (the spike's finding). */
export function isVolumeFromAnotherInstall(composeOutput: string): boolean {
  return /password authentication failed for user "codelens"/.test(composeOutput);
}
