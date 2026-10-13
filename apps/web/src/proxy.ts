import { type NextRequest, NextResponse } from "next/server";

import { openSession, SESSION_COOKIE } from "@/lib/tokens";

/** An early redirect for signed-out visitors, before any page renders. It is
 * only a shortcut: the real check is in lib/dal.ts, next to the data, so a
 * page is safe even if this matcher misses it. */
export async function proxy(request: NextRequest) {
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
  // Everything except the login page, the auth routes and static files.
  matcher: ["/((?!login|api/auth|_next/static|_next/image|favicon.ico|.*\\.svg$).*)"],
};
