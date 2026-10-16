import { ApiError, callApi, type Page, type Repo, type ReviewListItem } from "@/lib/api";
import { PALETTE_REVIEWS, paletteData } from "@/lib/palette";
import { requestUser } from "@/lib/session";

/** What the command palette searches: the caller's repos and the latest
 * reviews across them. The browser asks here when the palette first opens;
 * this server asks the API as the signed-in user, so the palette shows
 * exactly what the pages would. */
export async function GET() {
  const user = await requestUser();
  if (!user) {
    return Response.json({ error: { code: "unauthenticated" } }, { status: 401 });
  }
  try {
    const [repos, reviews] = await Promise.all([
      callApi<Repo[]>(user, "/repos"),
      callApi<Page<ReviewListItem>>(user, `/reviews?limit=${PALETTE_REVIEWS}`),
    ]);
    return Response.json(paletteData(repos, reviews.items), {
      headers: { "Cache-Control": "private, no-store" },
    });
  } catch (error) {
    if (error instanceof ApiError) {
      return Response.json({ error: { code: error.code, message: error.message } }, { status: 502 });
    }
    throw error;
  }
}
