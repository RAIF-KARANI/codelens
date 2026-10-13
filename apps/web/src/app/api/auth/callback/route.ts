import { type NextRequest, NextResponse } from "next/server";

import { config, isDemo } from "@/lib/config";
import { exchangeCode, fetchInstallationIds, fetchUser } from "@/lib/github";
import { OAUTH_COOKIE, readPending, redirectUri, sameState } from "@/lib/oauth";
import { safeNext } from "@/lib/paths";
import { startSession } from "@/lib/session";

/** Step 2 of login: GitHub sends the browser back here with a one-time `code`
 * and our `state`. Check the state, trade the code for a user token, ask
 * GitHub who the user is and which App installations they can access, and
 * start the session. The user token is not kept (D113). */
export async function GET(request: NextRequest) {
  // An open demo has no sign-in (D117).
  if (isDemo()) return NextResponse.redirect(new URL("/repos", request.url));
  // The request first: until a handler reads it, Next.js may prerender it at
  // build time, when there are no settings.
  const params = request.nextUrl.searchParams;
  const pending = readPending(request.cookies.get(OAUTH_COOKIE)?.value);
  const { appUrl, githubClientId, githubClientSecret } = config();

  const fail = (error: string) => {
    const response = NextResponse.redirect(new URL(`/login?error=${error}`, appUrl));
    response.cookies.delete({ name: OAUTH_COOKIE, path: "/api/auth" });
    return response;
  };

  if (params.get("error")) {
    // The user pressed Cancel on GitHub (error=access_denied).
    return fail("denied");
  }
  const code = params.get("code");
  if (!pending || !code || !sameState(pending.state, params.get("state"))) {
    // Expired (10 minutes), started in another browser, or forged.
    return fail("state");
  }

  try {
    const token = await exchangeCode({
      clientId: githubClientId,
      clientSecret: githubClientSecret,
      code,
      redirectUri: redirectUri(appUrl),
    });
    const [user, installations] = await Promise.all([
      fetchUser(token),
      fetchInstallationIds(token),
    ]);
    await startSession({
      id: String(user.id),
      login: user.login,
      name: user.name,
      avatarUrl: user.avatar_url,
      installations,
    });
  } catch (error) {
    console.error("GitHub login failed:", error instanceof Error ? error.message : error);
    return fail("github");
  }

  const response = NextResponse.redirect(new URL(safeNext(pending.next), appUrl));
  response.cookies.delete({ name: OAUTH_COOKIE, path: "/api/auth" });
  return response;
}
