import type { Repo, RepoStatus } from "@/lib/api";

/** A sidebar destination. `match` lists the path prefixes it owns ("/" owns
 * only itself: no other path starts with "//"). */
export type NavItem = { href: string; label: string; match: readonly string[] };

export const NAV_ITEMS: readonly NavItem[] = [
  { href: "/overview", label: "Overview", match: ["/overview"] },
  { href: "/reviews", label: "Reviews", match: ["/reviews"] },
  { href: "/repos", label: "Repositories", match: ["/repos"] },
];

/** Whether `pathname` is inside one of the item's prefixes (a whole segment:
 * "/repos" owns "/repos/3" but not "/reposx"). */
export function isActive(pathname: string, item: Pick<NavItem, "match">): boolean {
  return item.match.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

/** The repos the sidebar lists: most recently reviewed first, then by name;
 * at most `limit`, the rest behind "All repositories". */
export function sidebarRepos(repos: readonly Repo[], limit = 8): Repo[] {
  return [...repos]
    .sort((a, b) => {
      const byReview = (b.last_review_at ?? "").localeCompare(a.last_review_at ?? "");
      return byReview !== 0 ? byReview : a.full_name.localeCompare(b.full_name);
    })
    .slice(0, limit);
}

/** The dot beside a repo: what its index is doing. */
export const REPO_DOT: Record<RepoStatus, string> = {
  pending: "bg-muted-foreground/50",
  indexing: "animate-pulse bg-primary",
  ready: "bg-added",
  failed: "bg-critical",
};
