import type { Repo, RepoStatus, ReviewListItem, ReviewStatus, ReviewSummary } from "@/lib/api";

/** What the command palette searches, from GET /api/palette: the caller's
 * repos and the latest reviews across them. */
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

/** How many of the latest reviews the palette offers. */
export const PALETTE_REVIEWS = 15;

/** A review's name in a list: its PR title or branch, else its number. */
export function reviewLabel(review: Pick<ReviewSummary, "id" | "title" | "pr_number">): string {
  if (review.title) return review.title;
  return review.pr_number !== null ? `PR #${review.pr_number}` : `Review #${review.id}`;
}

/** Only what the palette shows, from the repos and the newest reviews
 * (GET /reviews: across repos, newest first, eval runs left out). */
export function paletteData(
  repos: readonly Repo[],
  reviews: readonly ReviewListItem[],
): PaletteData {
  return {
    repos: repos.map(({ id, full_name, status }) => ({ id, full_name, status })),
    reviews: reviews.map((r) => ({
      id: r.id,
      label: reviewLabel(r),
      repo: r.repo_full_name,
      pr_number: r.pr_number,
      status: r.status,
      created_at: r.created_at,
    })),
  };
}
