import { NextResponse } from "next/server";

import { config, hasSignIn } from "@/lib/config";
import { endSession } from "@/lib/session";

/** Sign out. POST only: a GET could be triggered by any page linking or
 * embedding it. 303 makes the browser follow with a GET. */
export async function POST(request: Request) {
  // Nobody signs in on a demo or the desktop, so there is nothing to end.
  if (!hasSignIn()) return NextResponse.redirect(new URL("/", request.url), 303);
  await endSession();
  return NextResponse.redirect(new URL("/login", config().appUrl), 303);
}
