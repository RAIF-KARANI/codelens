import { type NextRequest, NextResponse } from "next/server";

import { APP_KEY_COOKIE, checkLocalRequest } from "@/lib/app-key";
import { openSession, SESSION_COOKIE } from "@/lib/tokens";

/** Two jobs, before anything renders:
 *
 * - In CodeLens Desktop's local mode, the gate (D147): every request, pages,
 *   route handlers and Server Actions alike, must name a loopback host and
 *   carry the app's key, or it is refused here. There is no sign-in behind it.
 * - Otherwise, an early redirect for signed-out visitors. That one is only a
 *   shortcut: the real check is in lib/dal.ts, next to the data, so a page is
 *   safe even if this matcher misses it. */
export async function proxy(request: NextRequest) {
  if (process.env.LOCAL_MODE === "true") {
    const refusal = checkLocalRequest(
      request.headers.get("host"),
      request.cookies.get(APP_KEY_COOKIE)?.value,
      process.env.LOCAL_APP_KEY,
    );
    if (refusal === null) {
      // No sign-in on the desktop. The login page's own redirect can't say so:
      // its shell is prerendered when the image is built, not where it runs.
      // To the Host header, checked as loopback above, not request.url: Next.js
      // spells 127.0.0.1 as localhost there, and the app's cookie belongs to
      // the host its window opened.
      if (request.nextUrl.pathname.startsWith("/login")) {
        const host = request.headers.get("host");
        return NextResponse.redirect(new URL("/repos", `${request.nextUrl.protocol}//${host}`));
      }
      return NextResponse.next();
    }
    // The same answer for every refusal: which part failed is for the log.
    console.warn(`local mode: refused ${request.method} ${request.nextUrl.pathname} (${refusal})`);
    return new NextResponse("This dashboard opens only in the CodeLens app.\n", {
      status: refusal === "not_configured" ? 503 : 403,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }
  // An open demo lets everyone in (D117).
  if (process.env.DEMO_MODE === "true") {
    return NextResponse.next();
  }
  const { pathname, search } = request.nextUrl;
  // Not the login page, nor /api, whose handlers check the session themselves
  // and answer 401, not a redirect; nor images and icons, which the login
  // page shows too; nor the download page, which shows no data (D179).
  if (
    pathname.startsWith("/login") ||
    pathname === "/download" ||
    pathname.startsWith("/api/") ||
    pathname.startsWith("/_next/") ||
    pathname === "/favicon.ico" ||
    pathname.endsWith(".svg")
  ) {
    return NextResponse.next();
  }
  const secret = process.env.SESSION_SECRET ?? "";
  const user = await openSession(request.cookies.get(SESSION_COOKIE)?.value, secret);
  if (user) {
    return NextResponse.next();
  }
  const login = new URL("/login", request.url);
  if (pathname !== "/") {
    login.searchParams.set("next", pathname + search);
  }
  return NextResponse.redirect(login);
}

export const config = {
  // Everything but the build's own scripts and styles, which hold no data.
  // Local mode's gate must see every other path: a pattern like `.*\.svg$`
  // here would let `/api/nodes/1.svg` reach a route handler ungated. The
  // function above leaves out what signed-out redirects shouldn't touch.
  matcher: ["/((?!_next/static/).*)"],
};
