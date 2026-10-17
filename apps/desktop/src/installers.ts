/** Installing what CodeLens needs and doesn't ship: Docker Desktop and Ollama
 * (D143, D146), from their publishers, checked before anything runs (D150).
 *
 * 1. Download the publisher's current installer over HTTPS, with progress, to
 *    the data folder's `downloads/` (a `.part` file until it's complete).
 * 2. Check its Windows signature: valid, and signed by the expected
 *    organisation. Their installers change with every release, so the
 *    signer is what can be pinned; a swapped or tampered file fails here.
 * 3. Run it, with the user's yes, in the publisher's own window (Docker's
 *    asks for administrator rights and shows its own terms).
 *
 * A Docker install can need a Windows restart (WSL2). Before running it, a
 * one-time entry (RunOnce) opens CodeLens at the next sign-in, so the wizard
 * goes on where it was; it is removed if no restart was needed. */

import { spawn } from "node:child_process";
import { createWriteStream, existsSync } from "node:fs";
import { mkdir, rename, rm } from "node:fs/promises";
import { join } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

export type Installer = {
  name: string;
  url: string;
  file: string;
  /** The `O=` of the signing certificate's subject, exactly. */
  publisher: string;
  /** How to run it: Docker's own window (it needs administrator rights and
   * shows its terms); Ollama installs per user, silently with progress. */
  args: string[];
  elevated: boolean;
};

export const INSTALLERS = {
  docker: {
    name: "Docker Desktop",
    url: "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe",
    file: "Docker Desktop Installer.exe",
    publisher: "Docker Inc",
    args: [],
    elevated: true,
  },
  ollama: {
    name: "Ollama",
    url: "https://ollama.com/download/OllamaSetup.exe",
    file: "OllamaSetup.exe",
    publisher: "Ollama Inc.",
    args: ["/SILENT"],
    elevated: false,
  },
} satisfies Record<string, Installer>;

export type Progress = { received: number; total: number | null };

/** Downloads `url` to `dir/file`, reporting progress; returns the path. Only
 * a complete download gets the real name. */
export async function download(
  installer: Installer,
  dir: string,
  onProgress: (progress: Progress) => void,
  fetchImpl: typeof fetch = fetch,
): Promise<string> {
  await mkdir(dir, { recursive: true });
  const target = join(dir, installer.file);
  const part = `${target}.part`;
  const response = await fetchImpl(installer.url, { redirect: "follow" });
  if (!response.ok || !response.body) {
    throw new Error(`downloading ${installer.name} failed: HTTP ${response.status}`);
  }
  const length = Number(response.headers.get("content-length"));
  const total = Number.isFinite(length) && length > 0 ? length : null;
  let received = 0;
  const body = Readable.fromWeb(response.body as Parameters<typeof Readable.fromWeb>[0]);
  body.on("data", (chunk: Buffer) => {
    received += chunk.length;
    onProgress({ received, total });
  });
  try {
    await pipeline(body, createWriteStream(part));
  } catch (error) {
    await rm(part, { force: true });
    throw error;
  }
  if (total !== null && received !== total) {
    await rm(part, { force: true });
    throw new Error(`downloading ${installer.name} stopped early (${received} of ${total} bytes)`);
  }
  await rename(part, target);
  return target;
}

export type Signature = { status: string; subject: string };

/** The `O=` part of a certificate subject ("CN=Docker Inc, O=Docker Inc, L=..."). */
export function organisation(subject: string): string | null {
  const match = /(?:^|,\s*)O=("([^"]*)"|[^,]*)/.exec(subject);
  if (!match) return null;
  return (match[2] ?? match[1] ?? "").trim();
}

/** Whether a file's signature is one to trust: Windows says Valid (signed,
 * unchanged since, by a certificate it trusts), by exactly the publisher. */
export function signatureProblem(signature: Signature | null, publisher: string): string | null {
  if (!signature) return "its signature couldn't be read";
  if (signature.status !== "Valid") return `its signature isn't valid (${signature.status})`;
  const org = organisation(signature.subject);
  if (org !== publisher) return `it is signed by ${org ?? "an unknown publisher"}, not ${publisher}`;
  return null;
}

/** Windows' own check of the file's Authenticode signature. The path goes
 * through an environment variable, so no file name is ever parsed as code. */
export async function readSignature(path: string): Promise<Signature | null> {
  const script =
    "$s = Get-AuthenticodeSignature -LiteralPath $env:CODELENS_FILE; " +
    "[pscustomobject]@{ status = [string]$s.Status; subject = [string]$s.SignerCertificate.Subject } | ConvertTo-Json -Compress";
  const text = await powershell(script, { CODELENS_FILE: path });
  try {
    const value = JSON.parse(text.output) as { status: unknown; subject: unknown };
    return { status: String(value.status ?? ""), subject: String(value.subject ?? "") };
  } catch {
    return null;
  }
}

/** Runs a checked installer and waits for it; returns its exit code (3010:
 * done, a restart is needed). Elevated ones ask Windows (UAC) first. */
export async function runInstaller(installer: Installer, path: string): Promise<number> {
  const verb = installer.elevated ? "-Verb RunAs " : "";
  const args = installer.args.length ? "-ArgumentList $env:CODELENS_ARGS.Split(' ') " : "";
  const script = `$p = Start-Process -LiteralPath $env:CODELENS_FILE ${args}${verb}-Wait -PassThru; exit $p.ExitCode`;
  const run = await powershell(script, { CODELENS_FILE: path, CODELENS_ARGS: installer.args.join(" ") });
  return run.code;
}

const RUN_ONCE = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce";

/** Opens CodeLens once at the next sign-in (after Docker's restart), or
 * forgets that again. Only for the installed app: a development build has no
 * single exe to start. */
export async function resumeAfterRestart(exe: string | null, on: boolean): Promise<void> {
  if (!exe) return;
  const args = on
    ? ["add", RUN_ONCE, "/v", "CodeLens", "/t", "REG_SZ", "/d", `"${exe}" --resume`, "/f"]
    : ["delete", RUN_ONCE, "/v", "CodeLens", "/f"];
  await new Promise<void>((resolve) => {
    const child = spawn("reg.exe", args, { windowsHide: true });
    child.on("error", () => resolve());
    child.on("close", () => resolve());
  });
}

// --- where the installed programs are

const programFiles = () => process.env.ProgramFiles ?? "C:\\Program Files";
const localPrograms = () => join(process.env.LOCALAPPDATA ?? "", "Programs");

export function dockerDesktopExe(): string {
  return join(programFiles(), "Docker", "Docker", "Docker Desktop.exe");
}

/** The docker command Docker Desktop installs. A process started before the
 * install doesn't have it on its PATH yet, so it's found here too. */
export function dockerCli(): string | null {
  const path = join(programFiles(), "Docker", "Docker", "resources", "bin", "docker.exe");
  return existsSync(path) ? path : null;
}

export function ollamaAppExe(): string {
  return join(localPrograms(), "Ollama", "ollama app.exe");
}

export function isOllamaInstalled(): boolean {
  return existsSync(ollamaAppExe());
}

/** Starts a program the way a double-click would, without waiting for it. */
export function launch(exe: string): void {
  spawn(exe, [], { detached: true, stdio: "ignore" }).unref();
}

async function powershell(script: string, env: Record<string, string>): Promise<{ code: number; output: string }> {
  return new Promise((resolve) => {
    let out = "";
    const child = spawn("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
      env: { ...process.env, ...env },
      windowsHide: true,
    });
    child.stdout.on("data", (data: Buffer) => (out += data.toString()));
    child.on("error", () => resolve({ code: -1, output: out }));
    child.on("close", (code) => resolve({ code: code ?? -1, output: out }));
  });
}
