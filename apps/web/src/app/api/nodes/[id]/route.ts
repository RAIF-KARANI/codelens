import type { NextRequest } from "next/server";

import { ApiError, callApi, type CodeNode } from "@/lib/api";
import { parseId } from "@/lib/params";
import { readSession } from "@/lib/session";

/** A code node, for the evidence drawer: the browser asks here, and this
 * server asks the API as the signed-in user. */
export async function GET(_request: NextRequest, ctx: RouteContext<"/api/nodes/[id]">) {
  const user = await readSession();
  if (!user) {
    return Response.json({ error: { code: "unauthenticated" } }, { status: 401 });
  }
  const id = parseId((await ctx.params).id);
  if (id === null) {
    return Response.json({ error: { code: "not_found" } }, { status: 404 });
  }
  try {
    const node = await callApi<CodeNode>(user, `/nodes/${id}`);
    // A re-index can update a node in place, and access is per user.
    return Response.json(node, { headers: { "Cache-Control": "private, no-store" } });
  } catch (error) {
    if (error instanceof ApiError) {
      return Response.json(
        { error: { code: error.code, message: error.message } },
        { status: error.status === 404 ? 404 : 502 },
      );
    }
    throw error;
  }
}
