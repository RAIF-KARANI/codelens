import type { NextRequest } from "next/server";

import { config } from "@/lib/config";
import { parseId } from "@/lib/params";
import { readSession } from "@/lib/session";
import { apiToken } from "@/lib/tokens";

/** A review's live events, passed through from the API. The browser's
 * EventSource can't send an Authorization header, so it connects here with its
 * session cookie, and this server adds the token (planning.md §7). The stream
 * is relayed as it arrives, never buffered. */
export async function GET(request: NextRequest, ctx: RouteContext<"/api/reviews/[id]/events">) {
  const user = await readSession();
  if (!user) {
    return Response.json({ error: { code: "unauthenticated" } }, { status: 401 });
  }
  const id = parseId((await ctx.params).id);
  if (id === null) {
    return Response.json({ error: { code: "not_found" } }, { status: 404 });
  }

  const { apiUrl, apiJwtSecret } = config();
  const headers = new Headers({
    Accept: "text/event-stream",
    Authorization: `Bearer ${await apiToken(user, apiJwtSecret)}`,
  });
  // An EventSource that reconnects sends the last id it saw; the API then
  // replays only what came after it.
  const lastEventId = request.headers.get("last-event-id");
  if (lastEventId && /^\d+$/.test(lastEventId)) {
    headers.set("Last-Event-ID", lastEventId);
  }

  const upstream = await fetch(`${apiUrl}/api/v1/reviews/${id}/events`, {
    headers,
    cache: "no-store",
    // The browser leaving closes the API's stream too.
    signal: request.signal,
  });
  if (!upstream.ok || !upstream.body) {
    // 404 for a review the user can't see, like the API (D111).
    return new Response(null, { status: upstream.status === 404 ? 404 : 502 });
  }
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
