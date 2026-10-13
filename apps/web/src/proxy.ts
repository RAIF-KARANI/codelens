import { type NextRequest, NextResponse } from "next/server";

import { openSession, SESSION_COOKIE } from "@/lib/tokens";

/** An early redirect for signed-out visitors, before any page renders. It is
 * only a shortcut: the real check is in lib/dal.ts, next to the data, so a
 * page is safe even if this matcher misses it. */
export async function proxy(request: NextRequest) {
  // An open demo lets everyone in (D117).
  if (process.env.DEMO_MODE === "true") {
    return NextResponse.next();
  }
  const secret = process.env.SESSION_SECRET ?? "";
  const user = await openSession(request.cookies.get(SESSION_COOKIE)?.value, secret);
  if (user) {
    return NextResponse.next();
  }
  const login = new URL("/login", request.url);
  const { pathname, search } = request.nextUrl;
  if (pathname !== "/") {
    login.searchParams.set("next", pathname + search);
  }
  return NextResponse.redirect(login);
}

export const config = {
  // Pages only: not the login page, static files, or /api (whose handlers
  // check the session themselves and answer 401, not a redirect).
  matcher: ["/((?!login|api/|_next/static|_next/image|favicon.ico|.*\\.svg$).*)"],
};
