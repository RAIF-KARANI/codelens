/** The user's own GitHub App (planning-v2.md D139, Phase 11 step 5b), made by
 * the existing, security-reviewed manifest flow (`python -m core.github
 * register`, D93, D132) run in the stack's `tools` container (the user's
 * choice, D174): the app picks a free loopback port, publishes it to the
 * container, reads the start link the flow prints and opens the user's
 * browser at it; GitHub sends the browser back to the container, which saves
 * the App's key and settings in secrets/; the app then merges those settings
 * into codelens.env. Also `codelens doctor --desktop --json`. */

import { spawn } from "node:child_process";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

import type { Check, CheckStatus } from "./machine.js";
import { composeArgs, composeEnv, docker, dockerBin, freePort, parseEnv, type Stack } from "./stack.js";

/** The lines `register --no-browser` prints for a program to find
 * (core/github/__main__.py). */
export const START_URL_PREFIX = "CODELENS_START_URL=";
export const INSTALL_URL_PREFIX = "CODELENS_INSTALL_URL=";
/** The webhook the desktop's App is made with, off (core/github/manifest.py). */
export const PLACEHOLDER_WEBHOOK_URL = "https://example.com/codelens-webhook-not-in-use";
/** GitHub's limit on an App's name. */
const MAX_NAME = 34;

/** What the App's env file carries into codelens.env (as `codelens setup`
 * copies it into .env). */
export const APP_KEYS = [
  "GITHUB_APP_ID",
  "GITHUB_APP_PRIVATE_KEY_PATH",
  "GITHUB_WEBHOOK_SECRET",
  "GITHUB_ALLOWED_ACCOUNTS",
  "GITHUB_CLIENT_ID",
  "GITHUB_CLIENT_SECRET",
] as const;

/** A suggested name; GitHub's page lets the user change it, and says if it's
 * taken (App names are unique across GitHub). */
export function appName(user: string): string {
  const name = `CodeLens (${user.replace(/[^\w .-]/g, "") || "desktop"})`;
  return name.length <= MAX_NAME ? name : `${name.slice(0, MAX_NAME - 1)})`;
}

export function hasApp(envText: string): boolean {
  const env = parseEnv(envText);
  return Boolean(env.get("GITHUB_APP_ID") && env.get("GITHUB_WEBHOOK_SECRET"));
}

/** `KEY=value` lines set in an env file's text: an existing key's line is
 * replaced where it is, a new one appended; other lines and comments stay. */
export function mergeEnv(text: string, values: Map<string, string>): string {
  const left = new Map(values);
  const lines = text.split(/\r?\n/).map((line) => {
    const key = line.slice(0, line.indexOf("=")).trim();
    if (line.indexOf("=") > 0 && left.has(key)) {
      const value = left.get(key);
      left.delete(key);
      return `${key}=${value}`;
    }
    return line;
  });
  while (lines.length && lines.at(-1) === "") lines.pop();
  for (const [key, value] of left) lines.push(`${key}=${value}`);
  return `${lines.join("\n")}\n`;
}

/** The marked lines in `register`'s output. */
export function markedLines(output: string): { startUrl: string | null; installUrl: string | null } {
  const find = (prefix: string) =>
    output
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find((line) => line.startsWith(prefix))
      ?.slice(prefix.length) ?? null;
  return { startUrl: find(START_URL_PREFIX), installUrl: find(INSTALL_URL_PREFIX) };
}

/** A link the flow printed must be its own loopback page, never anything
 * else the browser would be sent to. */
export function isRegistrationLink(url: string, port: number): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" && parsed.host === `127.0.0.1:${port}` && parsed.pathname === "/";
  } catch {
    return false;
  }
}

/** The install page of an App: GitHub's own, nothing else. */
export function isInstallLink(url: string): boolean {
  return /^https:\/\/github\.com\/apps\/[a-z0-9-]+\/installations\/new$/.test(url);
}

export function registerArgs(stack: Stack, name: string, port: number): string[] {
  return composeArgs(
    stack,
    "run",
    "--rm",
    "--no-deps",
    "-p",
    `127.0.0.1:${port}:${port}`,
    "tools",
    "python",
    "-m",
    "core.github",
    "register",
    "--name",
    name,
    "--webhook-url",
    PLACEHOLDER_WEBHOOK_URL,
    "--webhook-inactive",
    "--no-browser",
    "--bind",
    "0.0.0.0",
    "--port",
    String(port),
    "--secrets-dir",
    "secrets",
  );
}

/** The workers run as uid 1000 (services/worker/Dockerfile). */
export const WORKER_UID = "1000:1000";

/** Hands secrets/ to the workers' user. The flow writes the App's key
 * owner-only (D132) as root, the only user that may lock down a folder made on
 * Windows; the workers then couldn't read it (found live: the first index
 * failed with "Permission denied"). It stays owner-only, theirs. */
export function handOverArgs(stack: Stack): string[] {
  return composeArgs(stack, "run", "--rm", "--no-deps", "tools", "chown", "-R", WORKER_UID, "/app/secrets");
}

export type CreatedApp = { installUrl: string };

/** Runs the manifest flow; `openBrowser` is called with its start link. The
 * App's settings are in codelens.env when this resolves. */
export async function createApp(
  stack: Stack,
  name: string,
  openBrowser: (url: string) => void,
  log: (line: string) => void,
): Promise<CreatedApp> {
  const port = await freePort();
  let output = "";
  let opened = false;
  const code = await new Promise<number>((resolve, reject) => {
    const child = spawn(dockerBin(), registerArgs(stack, name, port), {
      env: { ...process.env, ...composeEnv(stack) },
      windowsHide: true,
    });
    const read = (data: Buffer) => {
      output += data.toString();
      const { startUrl } = markedLines(output);
      if (startUrl && !opened) {
        opened = true;
        if (isRegistrationLink(startUrl, port)) openBrowser(startUrl);
        else log(`ignored an unexpected start link: ${startUrl}`);
      }
    };
    child.stdout.on("data", read);
    child.stderr.on("data", read);
    child.on("error", reject);
    child.on("close", (exit) => resolve(exit ?? 1));
  });
  // The output has no secrets: the flow writes them to files only (D132).
  log(`register exited with ${code}`);
  if (code !== 0) {
    const error = output.split(/\r?\n/).find((line) => line.startsWith("error:"));
    throw new Error(error ? error.slice("error:".length).trim() : `the App wasn't created (exit ${code})`);
  }
  const handOver = await docker(handOverArgs(stack), composeEnv(stack));
  if (handOver.code !== 0) throw new Error(`the App was created, but its key couldn't be given to the workers: ${handOver.output.trim()}`);
  const { installUrl } = markedLines(output);
  const created = parseEnv(await readFile(join(stack.home, "secrets", "github-app.env"), "utf8"));
  const values = new Map<string, string>();
  for (const key of APP_KEYS) {
    const value = created.get(key);
    if (value) values.set(key, value);
  }
  const envFile = join(stack.home, "codelens.env");
  await writeFile(envFile, mergeEnv(await readFile(envFile, "utf8"), values), { mode: 0o600 });
  if (!installUrl || !isInstallLink(installUrl)) throw new Error("the App was created, but its install link wasn't printed");
  return { installUrl };
}

/** `codelens doctor --desktop --json`'s rows as checks. */
export function parseDoctor(json: string): Check[] {
  const rows = JSON.parse(json) as { name: string; status: string; detail: string; fix: string | null }[];
  return rows.map((row) => ({
    name: row.name,
    status: (["ok", "warn", "fail"].includes(row.status) ? row.status : "fail") as CheckStatus,
    detail: row.detail,
    ...(row.fix ? { fix: row.fix } : {}),
  }));
}

/** What the stack says about Ollama, the models and the App. */
export async function runDoctor(stack: Stack): Promise<Check[]> {
  const args = composeArgs(stack, "run", "--rm", "--no-deps", "tools", "python", "-m", "core.setup", "doctor", "--desktop", "--json");
  const out = await new Promise<string>((resolve, reject) => {
    let text = "";
    const child = spawn(dockerBin(), args, { env: { ...process.env, ...composeEnv(stack) }, windowsHide: true });
    child.stdout.on("data", (data: Buffer) => (text += data.toString()));
    child.on("error", reject);
    child.on("close", () => resolve(text));
  });
  const json = out.split(/\r?\n/).reverse().find((line) => line.trim().startsWith("["));
  if (!json) throw new Error("the stack's checks didn't answer");
  return parseDoctor(json);
}
