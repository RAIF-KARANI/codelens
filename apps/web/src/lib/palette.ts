import type { Repo, RepoStatus, ReviewStatus, ReviewSummary } from "@/lib/api";

/** What the command palette searches, from GET /api/palette: the caller's
 * repos and their latest reviews. */
export type PaletteData = {
  repos: { id: number; full_name: string; status: RepoStatus }[];
  reviews: PaletteReview[];
};

export type PaletteReview = {
  id: number;
  label: string;
  repo: string;
  pr_number: number | null;
  status: ReviewStatus;
  created_at: string;
};

/** How many repos the palette asks for reviews (one API call each, until the
 * API can list reviews across repos: Phase 10 step 3), and how many it keeps. */
export const PALETTE_REPOS = 8;
export const PALETTE_REVIEWS = 15;

/** A review's name in a list: its PR title or branch, else its number. */
export function reviewLabel(review: Pick<ReviewSummary, "id" | "title" | "pr_number">): string {
  if (review.title) return review.title;
  return review.pr_number !== null ? `PR #${review.pr_number}` : `Review #${review.id}`;
}

/** The palette's data from each repo's first page of reviews: the newest
 * reviews across all of them, at most `limit`. */
export function paletteData(
  repos: readonly Repo[],
  reviewsByRepo: readonly { repo: Repo; reviews: readonly ReviewSummary[] }[],
  limit = PALETTE_REVIEWS,
): PaletteData {
  const reviews = reviewsByRepo
    .flatMap(({ repo, reviews }) =>
      reviews.map((r) => ({
        id: r.id,
        label: reviewLabel(r),
        repo: repo.full_name,
        pr_number: r.pr_number,
        status: r.status,
        created_at: r.created_at,
      })),
    )
    .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id)
    .slice(0, limit);
  return {
    repos: repos.map(({ id, full_name, status }) => ({ id, full_name, status })),
    reviews,
  };
}
