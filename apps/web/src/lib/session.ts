import "server-only";

import { cookies } from "next/headers";

import { config, isDemo } from "@/lib/config";
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

/** Every visitor of an open demo (D117): no session, no installations. The
 * API's demo mode shows such a token every repo of the demo (D115); a write
 * is refused all the same. */
export const DEMO_VISITOR: SessionUser = {
  id: "demo-visitor",
  login: "visitor",
  name: "Demo visitor",
  avatarUrl: "",
  installations: [],
};

/** Who is making this request: the demo's visitor on a demo, otherwise the
 * user in the session cookie (or null). Route handlers and Server Actions ask
 * here; pages go through lib/dal.ts. */
export async function requestUser(): Promise<SessionUser | null> {
  return isDemo() ? DEMO_VISITOR : readSession();
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
