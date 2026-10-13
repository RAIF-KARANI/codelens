// No "server-only" here: src/proxy.ts uses openSession too. Nothing in this
// file is secret; the secrets are arguments.
import { jwtVerify, SignJWT } from "jose";

/** The signed-in user, as kept in the session cookie. It's what GitHub told
 * us at login; the GitHub token itself is not kept (D113). */
export type SessionUser = {
  /** GitHub's numeric user id, as a string (the API's `sub`). */
  id: string;
  login: string;
  name: string | null;
  avatarUrl: string;
  /** The ids of the App installations this user can access, from
   * GET /user/installations at login. The API shows only their repos (D111). */
  installations: number[];
};

export const SESSION_COOKIE = "codelens_session";

const ALGORITHM = "HS256";
/** How long a login lasts. Installations are read from GitHub once per login,
 * so this is also how stale they can get (a new install shows after the next
 * login). GitHub's own user tokens last as long. */
export const SESSION_TTL_SECONDS = 8 * 60 * 60;
/** The API's own limit (services/api/src/api/auth.py). */
export const API_TOKEN_TTL_SECONDS = 5 * 60;

function key(secret: string): Uint8Array {
  return new TextEncoder().encode(secret);
}

/** The session cookie's value: the user, signed (not encrypted: it holds
 * nothing the user can't already see on GitHub). */
export async function sealSession(
  user: SessionUser,
  secret: string,
  now: Date = new Date(),
): Promise<string> {
  return new SignJWT({
    login: user.login,
    name: user.name,
    avatar_url: user.avatarUrl,
    installations: user.installations,
  })
    .setProtectedHeader({ alg: ALGORITHM })
    .setSubject(user.id)
    .setIssuedAt(now)
    .setExpirationTime(Math.floor(now.getTime() / 1000) + SESSION_TTL_SECONDS)
    .sign(key(secret));
}

/** The user in a session cookie, or null if it's missing, forged, expired or
 * malformed. Never throws: a bad cookie just means "signed out". */
export async function openSession(
  value: string | undefined,
  secret: string,
  now: Date = new Date(),
): Promise<SessionUser | null> {
  if (!value) return null;
  try {
    // algorithms is fixed: the token's own `alg` header is never trusted.
    const { payload } = await jwtVerify(value, key(secret), {
      algorithms: [ALGORITHM],
      currentDate: now,
      requiredClaims: ["sub", "exp"],
    });
    const { sub, login, name, avatar_url, installations } = payload;
    if (
      typeof sub !== "string" ||
      typeof login !== "string" ||
      typeof avatar_url !== "string" ||
      (name !== null && typeof name !== "string") ||
      !isIdList(installations)
    ) {
      return null;
    }
    return { id: sub, login, name, avatarUrl: avatar_url, installations };
  } catch {
    return null;
  }
}

/** A token for one call to the API: HS256 with the shared API_JWT_SECRET,
 * five minutes, `sub` = GitHub user id, `login`, `installations` (planning.md
 * §7, D111). The API decides from the login whether the user is an admin. */
export async function apiToken(
  user: SessionUser,
  secret: string,
  now: Date = new Date(),
): Promise<string> {
  return new SignJWT({ login: user.login, installations: user.installations })
    .setProtectedHeader({ alg: ALGORITHM })
    .setSubject(user.id)
    .setIssuedAt(now)
    .setExpirationTime(Math.floor(now.getTime() / 1000) + API_TOKEN_TTL_SECONDS)
    .sign(key(secret));
}

function isIdList(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((v) => Number.isSafeInteger(v));
}
