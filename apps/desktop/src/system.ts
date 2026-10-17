/** What the dashboard's System page shows (planning-v2.md Phase 11 step 6b,
 * D176): each service's state and health, Docker and Ollama, what the data
 * folder holds, and each service's recent log. Read by the app, which has the
 * files and Docker; the page asks through the control API (D175). */

import { open, readdir, stat } from "node:fs/promises";
import { join } from "node:path";

import { LONG_RUNNING, type ServiceState } from "./supervisor.js";
import type { DockerState } from "./stack.js";

/** The services whose logs and restarts the page may ask for: the stack's,
 * and the one-shot migrations. Nothing else names a file or a container. */
export const SERVICES = [...LONG_RUNNING, "migrate"] as const;

export function isService(name: unknown): name is (typeof SERVICES)[number] {
  return typeof name === "string" && (SERVICES as readonly string[]).includes(name);
}

export type SystemView = {
  docker: DockerState;
  ollama: boolean;
  paused: boolean;
  services: ServiceState[];
  storage: {
    dataFolder: string;
    mirrorsBytes: number;
    logsBytes: number;
    /** pg_database_size, or null when the database doesn't answer. */
    databaseBytes: number | null;
    /** Free space on the drive the data folder is on. */
    freeBytes: number | null;
  };
};

/** Every file's size under a folder, 0 if it isn't there. Symlinks aren't
 * followed: a mirror is plain files. */
export async function folderBytes(path: string): Promise<number> {
  let entries;
  try {
    entries = await readdir(path, { withFileTypes: true });
  } catch {
    return 0;
  }
  let total = 0;
  for (const entry of entries) {
    const full = join(path, entry.name);
    if (entry.isDirectory()) total += await folderBytes(full);
    else if (entry.isFile()) total += (await stat(full).catch(() => ({ size: 0 }))).size;
  }
  return total;
}

/** The last `lines` lines of a file, reading at most its last `maxBytes`
 * (a log may be 10 MB). An absent file is no lines. */
export async function tailFile(path: string, lines: number, maxBytes = 256 * 1024): Promise<string[]> {
  let handle;
  try {
    handle = await open(path, "r");
  } catch {
    return [];
  }
  try {
    const { size } = await handle.stat();
    const length = Math.min(size, maxBytes);
    const buffer = Buffer.alloc(length);
    await handle.read(buffer, 0, length, size - length);
    const text = buffer.toString("utf8");
    // A cut first line is dropped, unless the whole file was read.
    const all = text.split(/\r?\n/);
    if (length < size) all.shift();
    if (all.at(-1) === "") all.pop();
    return all.slice(-lines);
  } finally {
    await handle.close();
  }
}

/** `lines` from a query string: 1-1000, 200 by default. */
export function lineCount(value: string | null): number {
  const n = Number(value);
  return Number.isInteger(n) && n >= 1 ? Math.min(n, 1000) : 200;
}
