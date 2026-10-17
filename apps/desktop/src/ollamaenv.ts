/** The user's Ollama and a 16k window (planning-v2.md D146, D66): it fits an
 * 8 GB GPU only with flash attention and a q8_0 KV cache, two settings of the
 * Ollama server, kept as the user's own environment variables, which Ollama
 * reads when it starts. Set only when the user asks (the Settings page asks
 * first), and Ollama restarted so they apply. */

import { spawn } from "node:child_process";

export const LONG_WINDOW_ENV = { OLLAMA_FLASH_ATTENTION: "1", OLLAMA_KV_CACHE_TYPE: "q8_0" } as const;

/** `reg query HKCU\Environment` output as name -> value. */
export function parseRegQuery(output: string): Map<string, string> {
  const values = new Map<string, string>();
  for (const line of output.split(/\r?\n/)) {
    const match = /^\s+(\S+)\s+REG_(?:EXPAND_)?SZ\s+(.*)$/.exec(line);
    if (match?.[1]) values.set(match[1].toUpperCase(), (match[2] ?? "").trim());
  }
  return values;
}

/** Are both settings what a 16k window needs? */
export function readyForLong(env: Map<string, string>): boolean {
  return Object.entries(LONG_WINDOW_ENV).every(([name, value]) => env.get(name) === value);
}

function run(command: string, args: string[], env?: NodeJS.ProcessEnv): Promise<{ code: number; out: string }> {
  return new Promise((resolve) => {
    let out = "";
    const child = spawn(command, args, { windowsHide: true, ...(env ? { env } : {}) });
    child.stdout?.on("data", (d: Buffer) => (out += d.toString()));
    child.on("error", () => resolve({ code: -1, out }));
    child.on("close", (code) => resolve({ code: code ?? -1, out }));
  });
}

/** The user's environment variables, as Windows keeps them. */
export async function userEnvironment(): Promise<Map<string, string>> {
  return parseRegQuery((await run("reg.exe", ["query", "HKCU\\Environment"])).out);
}

/** Sets both for the user (setx: they persist, and Ollama started from now on
 * reads them), stops every process of the user's Ollama (its runners too:
 * a runner left behind keeps holding video memory), and starts its app again
 * with the settings in its environment. */
export async function prepareOllamaForLong(ollamaAppExe: string): Promise<void> {
  for (const [name, value] of Object.entries(LONG_WINDOW_ENV)) {
    const set = await run("setx.exe", [name, value]);
    if (set.code !== 0) throw new Error(`couldn't set ${name}`);
  }
  const stop =
    "Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($env:CODELENS_OLLAMA_DIR, 'OrdinalIgnoreCase') } | Stop-Process -Force";
  const dir = ollamaAppExe.slice(0, ollamaAppExe.lastIndexOf("\\") + 1);
  await run("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", stop], { ...process.env, CODELENS_OLLAMA_DIR: dir });
  spawn(ollamaAppExe, [], { detached: true, stdio: "ignore", env: { ...process.env, ...LONG_WINDOW_ENV } }).unref();
}
