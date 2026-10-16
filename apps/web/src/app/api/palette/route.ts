import { ApiError, callApi, type Page, type Repo, type ReviewSummary } from "@/lib/api";
import { sidebarRepos } from "@/lib/nav";
import { PALETTE_REPOS, paletteData } from "@/lib/palette";
import { requestUser } from "@/lib/session";

/** What the command palette searches: the caller's repos and the newest
 * reviews of the most recently reviewed ones. The browser asks here when the
 * palette first opens; this server asks the API as the signed-in user, so the
 * palette shows exactly what the pages would. */
export async function GET() {
  const user = await requestUser();
  if (!user) {
    return Response.json({ error: { code: "unauthenticated" } }, { status: 401 });
  }
  try {
    const repos = await callApi<Repo[]>(user, "/repos");
    const recent = sidebarRepos(
      repos.filter((repo) => repo.last_review_at !== null),
      PALETTE_REPOS,
    );
    const reviewsByRepo = await Promise.all(
      recent.map(async (repo) => ({
        repo,
        reviews: (await callApi<Page<ReviewSummary>>(user, `/repos/${repo.id}/reviews?limit=5`)).items,
      })),
    );
    return Response.json(paletteData(repos, reviewsByRepo), {
      headers: { "Cache-Control": "private, no-store" },
    });
  } catch (error) {
    if (error instanceof ApiError) {
      return Response.json({ error: { code: error.code, message: error.message } }, { status: 502 });
    }
    throw error;
  }
}
