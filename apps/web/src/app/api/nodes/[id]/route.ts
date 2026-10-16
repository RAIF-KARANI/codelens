import type { NextRequest } from "next/server";

import { ApiError, callApi, type CodeNode } from "@/lib/api";
import { langOf } from "@/lib/code";
import { highlight } from "@/lib/highlight";
import { parseId } from "@/lib/params";
import { requestUser } from "@/lib/session";

/** A code node, for the evidence sheet: the browser asks here, and this
 * server asks the API as the signed-in user, then highlights the code (so the
 * browser gets coloured lines, not a highlighter). */
export async function GET(_request: NextRequest, ctx: RouteContext<"/api/nodes/[id]">) {
  const user = await requestUser();
  if (!user) {
    return Response.json({ error: { code: "unauthenticated" } }, { status: 401 });
  }
  const id = parseId((await ctx.params).id);
  if (id === null) {
    return Response.json({ error: { code: "not_found" } }, { status: 404 });
  }
  try {
    const node = await callApi<CodeNode>(user, `/nodes/${id}`);
    const tokens = await highlight(node.code, langOf(node.path));
    // A re-index can update a node in place, and access is per user.
    return Response.json(
      { ...node, tokens },
      { headers: { "Cache-Control": "private, no-store" } },
    );
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
