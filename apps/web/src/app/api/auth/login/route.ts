import { randomBytes } from "node:crypto";

import { type NextRequest, NextResponse } from "next/server";

import { OAUTH_COOKIE, redirectUri } from "@/lib/oauth";
import { config, isDemo } from "@/lib/config";
import { authorizeUrl } from "@/lib/github";
import { safeNext } from "@/lib/paths";

/** Step 1 of login: send the browser to GitHub. The random `state` goes to
 * GitHub and into a short-lived cookie; the callback accepts only a request
 * that brings both back, so another site can't complete a login of its own
 * choosing in this browser (CSRF). */
export async function GET(request: NextRequest) {
  // An open demo has no sign-in (D117).
  if (isDemo()) return NextResponse.redirect(new URL("/repos", request.url));
  // The request first (see the callback).
  const next = safeNext(request.nextUrl.searchParams.get("next"));
  const { appUrl, githubClientId } = config();
  const state = randomBytes(32).toString("base64url");

  const response = NextResponse.redirect(authorizeUrl(githubClientId, redirectUri(appUrl), state));
  response.cookies.set(OAUTH_COOKIE, JSON.stringify({ state, next }), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    // lax, not strict: the cookie must come back on GitHub's redirect.
    sameSite: "lax",
    path: "/api/auth",
    maxAge: 10 * 60,
  });
  return response;
}
