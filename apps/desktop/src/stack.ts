/** CodeLens Desktop's stack: its data folder, the secrets generated on this
 * machine, and the Compose project that runs everything (planning-v2.md §4,
 * D143, D147). Plain functions over Node's standard library, so the Electron
 * app (step 1b) and the development CLI (cli.ts) drive it the same way. */

import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync } from "node:fs";
import { chmod, mkdir, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

/** The Compose project's name: its containers, network and database volume
 * are all prefixed with it, apart from a developer's `codelens` stack. */
export const PROJECT = "codelens-desktop";

/** The repo's apps/desktop folder, where compose.yaml and images.json are
 * (dist/stack.js is one level down). The installed app keeps them in its
 * resources folder instead (Stack.configDir): docker reads compose.yaml as a
 * plain file, which it couldn't inside the app's archive. */
export const APP_DIR = fileURLToPath(new URL("..", import.meta.url));

/** Where everything of this install lives: CODELENS_HOME if set, else
 * %LOCALAPPDATA%\CodeLens (Windows, D148), else ~/.codelens. */
export function codelensHome(env: NodeJS.ProcessEnv = process.env): string {
  if (env.CODELENS_HOME) return env.CODELENS_HOME;
  if (env.LOCALAPPDATA) return join(env.LOCALAPPDATA, "CodeLens");
  return join(homedir(), ".codelens");
}

/** The secrets every install generates for itself, never shared (D139). */
const GENERATED = ["POSTGRES_PASSWORD", "API_JWT_SECRET"] as const;

/** 32 random bytes, as URL-safe text: what the API and the dashboard want of
 * a secret (at least 32 bytes). */
export function newSecret(): string {
  return randomBytes(32).toString("base64url");
}

/** `KEY=value` lines; blank lines and `#` comments skipped. Windows tools may
 * write CRLF, so the line break's CR is optional. */
export function parseEnv(text: string): Map<string, string> {
  const values = new Map<string, string>();
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq > 0) values.set(trimmed.slice(0, eq).trim(), trimmed.slice(eq + 1).trim());
  }
  return values;
}

/** The data folder, created if missing: `codelens.env` (the secrets, then
 * later the GitHub App's settings), `data/` (the repos' mirrors) and
 * `secrets/` (the App's key). Safe to run again: secrets already there are
 * kept, missing ones added. The folder is in the user's own profile; the
 * modes make it owner-only where the file system has them (D132). */
export async function ensureHome(home: string): Promise<{ envFile: string; created: string[] }> {
  await mkdir(home, { recursive: true, mode: 0o700 });
  await mkdir(join(home, "data"), { recursive: true });
  await mkdir(join(home, "secrets"), { recursive: true, mode: 0o700 });
  await mkdir(join(home, "logs"), { recursive: true });

  const envFile = join(home, "codelens.env");
  const text = existsSync(envFile) ? await readFile(envFile, "utf8") : "";
  const present = parseEnv(text);
  const created = GENERATED.filter((name) => !present.get(name));
  if (created.length > 0) {
    const header = text ? "" : "# CodeLens Desktop's settings and secrets, made on this machine. Keep private.\n";
    const lines = created.map((name) => `${name}=${newSecret()}\n`).join("");
    const before = text && !text.endsWith("\n") ? `${text}\n` : text;
    await writeFile(envFile, before + header + lines, { mode: 0o600 });
  }
  await chmod(envFile, 0o600);
  return { envFile, created };
}

/** CodeLens's own three images. Published ones are pinned by digest, so an
 * install runs exactly what CI built and tested (D143, D150). */
export type Images = { api: string; worker: string; web: string };

const PINNED = /^[a-z0-9][a-z0-9._\/-]*(:[\w.-]+)?@sha256:[0-9a-f]{64}$/;

/** The images in images.json, refused unless each is pinned by digest. */
export function pinnedImages(json: unknown): Images {
  const value = json as Record<string, unknown> | null;
  const images = {} as Images;
  for (const name of ["api", "worker", "web"] as const) {
    const ref = value?.[name];
    if (typeof ref !== "string" || !ref) {
      throw new Error(
        `images.json has no ${name} image yet: run with --local to build them here, ` +
          "or copy the digests from the images workflow's summary",
      );
    }
    if (!PINNED.test(ref)) {
      throw new Error(`images.json: ${name} must be pinned by digest (name@sha256:...), not ${ref}`);
    }
    images[name] = ref;
  }
  return images;
}

export async function readImages(file = join(APP_DIR, "images.json")): Promise<Images> {
  return pinnedImages(JSON.parse(await readFile(file, "utf8")));
}

/** Names for images built from this checkout (compose.build.yaml). */
export const LOCAL_IMAGES: Images = {
  api: "codelens-desktop/api:local",
  worker: "codelens-desktop/worker:local",
  web: "codelens-desktop/web:local",
};

/** A port nobody is listening on, on loopback: ask the OS for one, then
 * give it back. Another program could take it in between; Compose then fails
 * to bind, and starting again picks another. */
export function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => {
        if (address && typeof address === "object") resolve(address.port);
        else reject(new Error("no port"));
      });
    });
  });
}

/** Which stack: where its data is, and which images. */
export type Stack = {
  home: string;
  images: Images;
  /** Use compose.build.yaml: build the images from this checkout. */
  build?: boolean;
  /** Where compose.yaml is; APP_DIR unless set. */
  configDir?: string;
};

/** What one launch adds: the dashboard's port and key. The key is new every
 * launch and only ever in memory and in the containers' environment, never on
 * disk (D147). */
export type Launch = { port: number; appKey: string };

/** A new launch: a free port and a new key. */
export async function newLaunch(): Promise<Launch> {
  return { port: await freePort(), appKey: newSecret() };
}

/** `docker` arguments for a compose command on this project. */
export function composeArgs(
  stack: Pick<Stack, "home" | "build" | "configDir">,
  ...command: string[]
): string[] {
  const dir = stack.configDir ?? APP_DIR;
  const files = ["-f", join(dir, "compose.yaml")];
  if (stack.build) files.push("-f", join(dir, "compose.build.yaml"));
  return [
    "compose",
    "-p",
    PROJECT,
    ...files,
    "--env-file",
    join(stack.home, "codelens.env"),
    ...command,
  ];
}

/** The variables compose.yaml reads from the environment. Without a launch
 * (stopping, listing), the dashboard's port and key are left out: compose.yaml
 * then has none, and a dashboard started that way refuses every request. */
export function composeEnv(stack: Stack, launch?: Launch): Record<string, string> {
  return {
    CODELENS_HOME: stack.home,
    CODELENS_API_IMAGE: stack.images.api,
    CODELENS_WORKER_IMAGE: stack.images.worker,
    CODELENS_WEB_IMAGE: stack.images.web,
    ...(launch && { CODELENS_WEB_PORT: String(launch.port), CODELENS_APP_KEY: launch.appKey }),
  };
}

export type Run = { code: number; output: string };

/** Runs `docker` with these arguments, collecting its output (and passing it
 * on, if asked). Never through a shell: the paths can hold spaces. */
export function docker(
  args: string[],
  env: Record<string, string> = {},
  onOutput?: (chunk: string) => void,
): Promise<Run> {
  return new Promise((resolve, reject) => {
    const child = spawn("docker", args, { env: { ...process.env, ...env }, windowsHide: true });
    let output = "";
    const collect = (data: Buffer) => {
      const text = data.toString();
      output += text;
      onOutput?.(text);
    };
    child.stdout.on("data", collect);
    child.stderr.on("data", collect);
    child.on("error", reject);
    child.on("close", (code) => resolve({ code: code ?? 1, output }));
  });
}

/** Starts (or updates) the stack and waits until every service is healthy or
 * done. With images already pulled this is the time to a running stack. */
export async function up(
  stack: Stack,
  launch: Launch,
  onOutput?: (chunk: string) => void,
): Promise<Run> {
  const command = stack.build ? ["up", "-d", "--build"] : ["up", "-d", "--pull", "missing"];
  return docker(
    composeArgs(stack, ...command, "--wait", "--wait-timeout", "600"),
    composeEnv(stack, launch),
    onOutput,
  );
}

/** Stops the containers; the database volume and the data folder stay. The
 * next start reuses the containers, so it is quick. */
export async function stop(stack: Stack, onOutput?: (chunk: string) => void): Promise<Run> {
  return docker(composeArgs(stack, "stop"), composeEnv(stack), onOutput);
}

/** Are all of CodeLens's images already on this machine? If not, `up` pulls
 * them first: the first start after an install, or an update. */
export async function imagesPresent(images: Images): Promise<boolean> {
  const run = await docker(["image", "inspect", "--format", "{{.Id}}", ...Object.values(images)]);
  return run.code === 0;
}

/** One `docker stats` memory figure ("155.3MiB / 7.568GiB", or kB, GiB) in
 * MiB; null if it doesn't read as one. */
export function parseMemory(usage: string): number | null {
  const match = /^([\d.]+)\s*(B|kB|KiB|MB|MiB|GB|GiB)\b/.exec(usage.trim());
  if (!match) return null;
  const scale: Record<string, number> = {
    B: 1 / 1024 ** 2,
    kB: 1000 / 1024 ** 2,
    KiB: 1 / 1024,
    MB: 1000 ** 2 / 1024 ** 2,
    MiB: 1,
    GB: 1000 ** 3 / 1024 ** 2,
    GiB: 1024,
  };
  return Number(match[1]) * (scale[match[2] ?? "MiB"] ?? 1);
}

/** Memory in use per running container of the project, in MiB. */
export async function containerMemory(): Promise<Map<string, number>> {
  const run = await docker([
    "stats",
    "--no-stream",
    "--format",
    "{{.Name}}\t{{.MemUsage}}",
  ]);
  const usage = new Map<string, number>();
  for (const line of run.output.split(/\r?\n/)) {
    const [name, memory] = line.split("\t");
    const mib = memory ? parseMemory(memory) : null;
    if (name?.startsWith(`${PROJECT}-`) && mib !== null) {
      usage.set(name.slice(PROJECT.length + 1).replace(/-\d+$/, ""), mib);
    }
  }
  return usage;
}

/** Each service's state, from `docker compose ps`. */
export async function status(stack: Stack): Promise<Run> {
  return docker(
    composeArgs(stack, "ps", "--all", "--format", "table {{.Service}}\t{{.State}}\t{{.Status}}"),
    composeEnv(stack),
  );
}
