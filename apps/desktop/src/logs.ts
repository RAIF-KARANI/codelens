/** Each service's log in the data folder (planning-v2.md Phase 11 step 4):
 * `logs/services/<service>.log`, so "what went wrong" never needs a terminal.
 * One `docker compose logs --follow` feeds them all; its lines start with the
 * container's name and a bar ("codelens-desktop-api-1  | ..."). */

import { type ChildProcess, spawn } from "node:child_process";
import { appendFileSync, mkdirSync, renameSync, statSync } from "node:fs";
import { join } from "node:path";

/** A file is moved aside to `<name>.log.1` past this size: two per service at most. */
export const MAX_LOG_BYTES = 10 * 1024 * 1024;

/** A line of `docker compose logs` as (service, text), or null for one that
 * isn't a service's line. Compose v2 starts each line with `<service>-<n>`
 * ("redis-1  | ..."); older versions with the container's full name
 * (`<project>-<service>-<n>`), whose project part is dropped. */
export function splitLogLine(line: string, project: string): [string, string] | null {
  const match = /^(\S+)-\d+\s+\|\s?(.*)$/.exec(line);
  if (!match) return null;
  const name = match[1] ?? "";
  const service = name.startsWith(`${project}-`) ? name.slice(project.length + 1) : name;
  return /^[a-z0-9][a-z0-9_-]*$/.test(service) ? [service, match[2] ?? ""] : null;
}

/** Writes `docker compose logs --follow` into one file per service. */
export class ServiceLogs {
  private partial = "";

  constructor(
    private readonly dir: string,
    private readonly project: string,
  ) {
    mkdirSync(dir, { recursive: true });
  }

  /** Feeds a running `docker compose logs --follow --no-color --timestamps`. */
  follow(child: ChildProcess): void {
    child.stdout?.on("data", (data: Buffer) => this.write(data.toString()));
    child.stderr?.on("data", (data: Buffer) => this.write(data.toString()));
  }

  write(chunk: string): void {
    const text = this.partial + chunk;
    const lines = text.split(/\r?\n/);
    this.partial = lines.pop() ?? "";
    for (const line of lines) {
      const split = splitLogLine(line, this.project);
      if (split) this.append(split[0], split[1]);
    }
  }

  private append(service: string, text: string): void {
    const file = join(this.dir, `${service}.log`);
    try {
      if (statSync(file).size > MAX_LOG_BYTES) renameSync(file, `${file}.1`);
    } catch {
      // No file yet.
    }
    appendFileSync(file, `${text}\n`);
  }
}

/** The PowerShell that ends this project's leftover `docker compose logs
 * --follow` processes (step 8b): the uninstaller force-closes the app, so its
 * follower ran on, for a day in the rehearsal, holding the install folder as
 * its working folder. Only processes of this project's logs, by their command
 * line; nothing from outside goes into the script. */
export function stopFollowersScript(project: string): string {
  if (!/^[a-z0-9-]+$/.test(project)) throw new Error(`unexpected project name ${project}`);
  return (
    "Get-CimInstance Win32_Process -Filter \"Name='docker-compose.exe' OR Name='docker.exe'\" | " +
    `Where-Object { $_.CommandLine -match ' -p ${project} ' -and $_.CommandLine -match ' logs --follow' } | ` +
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId }"
  );
}

/** Ends them (Windows only); resolves with how many there were. Never throws. */
export function stopLeftoverFollowers(project: string): Promise<number> {
  if (process.platform !== "win32") return Promise.resolve(0);
  return new Promise((resolve) => {
    let out = "";
    const child = spawn("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", stopFollowersScript(project)], {
      windowsHide: true,
    });
    child.stdout.on("data", (data: Buffer) => (out += data.toString()));
    child.on("error", () => resolve(0));
    child.on("close", () => resolve(out.split(/\s+/).filter(Boolean).length));
  });
}
