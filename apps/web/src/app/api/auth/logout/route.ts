import { NextResponse } from "next/server";

import { config } from "@/lib/config";
import { endSession } from "@/lib/session";

/** Sign out. POST only: a GET could be triggered by any page linking or
 * embedding it. 303 makes the browser follow with a GET. */
export async function POST() {
  await endSession();
  return NextResponse.redirect(new URL("/login", config().appUrl), 303);
}
