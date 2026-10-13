import "server-only";

import { cacheLife } from "next/cache";
import { redirect } from "next/navigation";

import { readSession } from "@/lib/session";
import type { SessionUser } from "@/lib/tokens";

/** The data access layer's one question: who is this? Every page and every
 * API call asks here, not in the proxy (src/proxy.ts only redirects early, for
 * speed). `use cache: private` reads the cookie at request time and lets one
 * request, and the browser's router for a few minutes, reuse the answer; it is
 * never stored on the server. */

/** The signed-in user, or a redirect to the login page. */
export async function currentUser(): Promise<SessionUser> {
  "use cache: private";
  cacheLife("minutes");
  const user = await readSession();
  if (!user) {
    // redirect() throws, so a missing user is never cached.
    redirect("/login");
  }
  return user;
}

/** The signed-in user, or null (for UI that is shown either way). */
export async function optionalUser(): Promise<SessionUser | null> {
  "use cache: private";
  cacheLife("minutes");
  return readSession();
}
