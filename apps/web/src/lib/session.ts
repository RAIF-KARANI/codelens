import "server-only";

import { cookies } from "next/headers";

import { config } from "@/lib/config";
import {
  openSession,
  sealSession,
  SESSION_COOKIE,
  SESSION_TTL_SECONDS,
  type SessionUser,
} from "@/lib/tokens";

/** httpOnly: page scripts can't read it. sameSite lax: it's sent when the
 * user follows a link here, but not on cross-site form posts. */
function cookieOptions(maxAge: number) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge,
  };
}

/** The user in this request's session cookie, or null. Callers that render
 * should go through lib/dal.ts instead. */
export async function readSession(): Promise<SessionUser | null> {
  const value = (await cookies()).get(SESSION_COOKIE)?.value;
  return openSession(value, config().sessionSecret);
}

export async function startSession(user: SessionUser): Promise<void> {
  const value = await sealSession(user, config().sessionSecret);
  (await cookies()).set(SESSION_COOKIE, value, cookieOptions(SESSION_TTL_SECONDS));
}

export async function endSession(): Promise<void> {
  (await cookies()).delete(SESSION_COOKIE);
}
