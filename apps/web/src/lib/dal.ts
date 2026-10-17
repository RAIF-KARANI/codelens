import "server-only";

import { cacheLife } from "next/cache";
import { redirect } from "next/navigation";

import { hasSignIn } from "@/lib/config";
import { readSession, requestUser } from "@/lib/session";
import type { SessionUser } from "@/lib/tokens";

/** The data access layer's one question: who is this? Every page and every
 * API call asks here, not in the proxy (src/proxy.ts only redirects early, for
 * speed; on the desktop it is also the gate that makes the owner the owner). `use cache: private` reads the cookie at request time and lets one
 * request, and the browser's router for a few minutes, reuse the answer; it is
 * never stored on the server. */

/** The signed-in user (on a demo, its anonymous visitor; on the desktop,
 * its owner), or a redirect to the login page. */
export async function currentUser(): Promise<SessionUser> {
  "use cache: private";
  cacheLife("minutes");
  const user = await requestUser();
  if (!user) {
    // redirect() throws, so a missing user is never cached.
    redirect("/login");
  }
  return user;
}

/** The signed-in user, or null (for UI that is shown either way). A demo's
 * visitor and the desktop's owner aren't signed in, so there it's always
 * null. */
export async function optionalUser(): Promise<SessionUser | null> {
  "use cache: private";
  cacheLife("minutes");
  return hasSignIn() ? readSession() : null;
}
