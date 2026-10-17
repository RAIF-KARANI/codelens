// No "server-only": src/proxy.ts uses this. Nothing in this file is secret;
// the key is an argument.
import { createHash, timingSafeEqual } from "node:crypto";

/** CodeLens Desktop's local mode (planning-v2.md D147): the dashboard has no
 * sign-in, so whoever reaches it acts as the machine's owner. Loopback alone
 * doesn't limit that to the app: any program on the machine, and any web page
 * open in a browser, can send requests to 127.0.0.1 (the lesson of D132). So
 * every request must also carry the app's key, a cookie that only the app's
 * own window holds, and name a loopback host. */

/** Set by the desktop app in its window's own cookie store (httpOnly,
 * SameSite=Strict); a browser on the same machine never has it. */
export const APP_KEY_COOKIE = "codelens_app_key";

/** The key is random, at least 32 bytes, like the other secrets. */
const MIN_KEY_BYTES = 32;

const LOOPBACK_HOSTNAMES = new Set(["127.0.0.1", "localhost", "[::1]"]);

/** Why a request is refused, or null when it may go on. */
export type LocalRefusal = "not_configured" | "bad_host" | "bad_key";

/** Is the Host header a loopback name, with an optional port? The check that
 * stops DNS rebinding: a page on evil.example whose name is made to resolve to
 * 127.0.0.1 still sends `Host: evil.example`. */
export function isLoopbackHost(host: string | null | undefined): boolean {
  if (!host) return false;
  const match = /^(\[[^\]]*\]|[^:]+)(?::(\d{1,5}))?$/.exec(host.trim().toLowerCase());
  return match !== null && LOOPBACK_HOSTNAMES.has(match[1]);
}

/** Does the cookie hold the key? Both sides are hashed first, so the
 * comparison takes the same time whatever the cookie holds, and its length
 * says nothing. */
export function keyMatches(given: string | undefined, expected: string): boolean {
  if (!given) return false;
  const digest = (value: string) => createHash("sha256").update(value).digest();
  return timingSafeEqual(digest(given), digest(expected));
}

/** The whole check, for one request. A missing or short key on the server
 * refuses everything: an unconfigured app fails closed, like the API without
 * its secret. */
export function checkLocalRequest(
  host: string | null | undefined,
  cookie: string | undefined,
  expected: string | undefined,
): LocalRefusal | null {
  if (!expected || new TextEncoder().encode(expected).length < MIN_KEY_BYTES) {
    return "not_configured";
  }
  if (!isLoopbackHost(host)) return "bad_host";
  if (!keyMatches(cookie, expected)) return "bad_key";
  return null;
}
