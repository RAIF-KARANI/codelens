/** The app's control API (planning-v2.md Phase 11 step 6a, D175): how the
 * dashboard's Settings page reaches the app, the one part of CodeLens that
 * can change codelens.env and recreate the services.
 *
 * A small HTTP server on the host's 127.0.0.1, at a free port, for one caller:
 * the dashboard's server, in its container, which reaches it as
 * `host.docker.internal:<port>` and holds this launch's key (passed in its
 * environment, never on disk, like the app's key, D165). Every request must
 * name one of those two hosts (DNS rebinding) and carry the key (any local
 * program can reach 127.0.0.1). The window itself never calls it: it keeps
 * its sandbox and no preload. */

import { createHash, timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";

import type { SettingsView } from "./appsettings.js";

/** Bodies are a few settings: anything bigger isn't the dashboard. */
const MAX_BODY = 16 * 1024;

export type ControlHandlers = {
  getSettings(): Promise<SettingsView>;
  /** Changes already checked as JSON; returns field errors or the new view. */
  saveSettings(body: unknown): Promise<{ errors: Record<string, string> } | { settings: SettingsView }>;
  /** Sets the user's Ollama up for a 16k window and restarts it (D146). */
  prepareLongWindow(): Promise<SettingsView>;
};

export type Refusal = "bad_host" | "bad_key";

/** The two names the dashboard's server may use for this port. */
export function hostOk(host: string | undefined, port: number): boolean {
  return host === `host.docker.internal:${port}` || host === `127.0.0.1:${port}`;
}

export function keyOk(header: string | undefined, key: string): boolean {
  const given = header?.startsWith("Bearer ") ? header.slice("Bearer ".length) : "";
  const digest = (value: string) => createHash("sha256").update(value).digest();
  return given.length > 0 && timingSafeEqual(digest(given), digest(key));
}

export function checkRequest(host: string | undefined, authorization: string | undefined, port: number, key: string): Refusal | null {
  if (!hostOk(host, port)) return "bad_host";
  if (!keyOk(authorization, key)) return "bad_key";
  return null;
}

function send(res: ServerResponse, status: number, body: unknown): void {
  const data = JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(data);
}

async function readJson(req: IncomingMessage): Promise<unknown> {
  let size = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY) throw new RangeError("too large");
    chunks.push(chunk as Buffer);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

/** Starts the API on a free loopback port; `port` is where it listens. */
export async function startControl(
  handlers: ControlHandlers,
  key: string,
  onError: (message: string) => void = () => {},
): Promise<{ port: number; server: Server; close(): Promise<void> }> {
  let port = 0;
  const server = createServer(async (req, res) => {
    const refusal = checkRequest(req.headers.host, req.headers.authorization, port, key);
    if (refusal) {
      onError(`control: refused ${req.method} ${req.url} (${refusal})`);
      send(res, 403, { error: { code: "forbidden", message: "not for you" } });
      return;
    }
    try {
      if (req.method === "GET" && req.url === "/v1/settings") {
        send(res, 200, await handlers.getSettings());
      } else if (req.method === "PUT" && req.url === "/v1/settings") {
        let body: unknown;
        try {
          body = await readJson(req);
        } catch (error) {
          const big = error instanceof RangeError;
          send(res, big ? 413 : 400, { error: { code: big ? "too_large" : "invalid_json", message: big ? "too large" : "not JSON" } });
          return;
        }
        const result = await handlers.saveSettings(body);
        if ("errors" in result) send(res, 422, { error: { code: "invalid", message: "some settings were refused", details: result.errors } });
        else send(res, 200, result.settings);
      } else if (req.method === "POST" && req.url === "/v1/settings/long-window") {
        send(res, 200, await handlers.prepareLongWindow());
      } else {
        send(res, 404, { error: { code: "not_found", message: "no such route" } });
      }
    } catch (error) {
      onError(`control: ${req.method} ${req.url} failed: ${String(error)}`);
      send(res, 500, { error: { code: "failed", message: error instanceof Error ? error.message : "failed" } });
    }
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  port = typeof address === "object" && address ? address.port : 0;
  return { port, server, close: () => new Promise((resolve) => server.close(() => resolve())) };
}
