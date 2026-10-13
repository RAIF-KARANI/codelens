import { timingSafeEqual } from "node:crypto";

/** The login's pending state, between /api/auth/login and the callback. */
export const OAUTH_COOKIE = "codelens_oauth";

export function redirectUri(appUrl: string): string {
  return `${appUrl}/api/auth/callback`;
}

export type PendingLogin = { state: string; next: string };

export function readPending(value: string | undefined): PendingLogin | null {
  if (!value) return null;
  try {
    const { state, next } = JSON.parse(value) as Partial<PendingLogin>;
    return typeof state === "string" && typeof next === "string" ? { state, next } : null;
  } catch {
    return null;
  }
}

/** Constant-time, so the comparison's timing says nothing about the state. */
export function sameState(expected: string, actual: string | null): boolean {
  if (actual === null) return false;
  const a = Buffer.from(expected);
  const b = Buffer.from(actual);
  return a.length === b.length && timingSafeEqual(a, b);
}
