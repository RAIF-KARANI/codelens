import Link from "next/link";

import { DateTime, SeverityCountsView, StatusBadge, Tag } from "@/components/badges";
import type { ReviewSummary } from "@/lib/api";
import { formatDuration, shortSha } from "@/lib/format";
import { commitUrl, pullRequestUrl } from "@/lib/github-links";

/** A review as the tables list it: across repos it carries its repo's name. */
export type ReviewRowData = ReviewSummary & { repo_full_name?: string };

/** Reviews, newest first: the change (title, PR, commit), status, findings,
 * and when (with the time it took). `repoName` is the repo when every row shares it (a
 * repo's page); without it, each row names its own repo in a column. */
export function ReviewTable({ reviews, repoName }: { reviews: ReviewRowData[]; repoName?: string }) {
  const showRepo = repoName === undefined;
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card">
      <table className="w-full whitespace-nowrap text-left text-sm">
        <thead className="bg-muted text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Review</th>
            <th className="px-3 py-2 font-medium">Change</th>
            {showRepo && <th className="px-3 py-2 font-medium">Repository</th>}
            <th className="px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium">Findings</th>
            <th className="px-3 py-2 font-medium">When</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {reviews.map((review) => (
            <ReviewRow
              key={review.id}
              review={review}
              repoName={review.repo_full_name ?? repoName ?? ""}
              showRepo={showRepo}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReviewRow({
  review,
  repoName,
  showRepo,
}: {
  review: ReviewRowData;
  repoName: string;
  showRepo: boolean;
}) {
  // A pull request's review is of a GitHub repo, so it links there.
  const onGitHub = review.source === "github" && repoName !== "";
  const took = formatDuration(review.started_at, review.finished_at);
  return (
    <tr className="align-top transition-colors hover:bg-muted/40">
      <td className="px-3 py-3 font-medium">
        <Link href={`/reviews/${review.id}`} className="hover:underline">
          #{review.id}
        </Link>
      </td>
      <td className="px-3 py-3">
        {review.title && (
          <Link
            href={`/reviews/${review.id}`}
            className="block max-w-56 truncate font-medium hover:underline"
            title={review.title}
          >
            {review.title}
          </Link>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {review.pr_number !== null && onGitHub ? (
            <a
              href={pullRequestUrl(repoName, review.pr_number)}
              target="_blank"
              rel="noreferrer"
              className="hover:underline"
            >
              PR #{review.pr_number}
            </a>
          ) : (
            <Tag>{review.source === "eval" ? "eval case" : "branch"}</Tag>
          )}
          <span className="font-mono text-xs text-muted-foreground">
            {onGitHub ? (
              <a
                href={commitUrl(repoName, review.head_sha)}
                target="_blank"
                rel="noreferrer"
                className="hover:underline"
              >
                {shortSha(review.head_sha)}
              </a>
            ) : (
              shortSha(review.head_sha)
            )}
          </span>
        </div>
        {review.error && (
          <p className="mt-1 max-w-md truncate text-xs text-critical" title={review.error}>
            {review.error}
          </p>
        )}
      </td>
      {showRepo && (
        <td className="px-3 py-3">
          <RepoName id={review.repo_id} fullName={repoName} />
        </td>
      )}
      <td className="px-3 py-3">
        <StatusBadge status={review.status} />
      </td>
      <td className="px-3 py-3">
        {review.status === "completed" ? <SeverityCountsView counts={review.counts} /> : "—"}
      </td>
      <td className="px-3 py-3 text-muted-foreground">
        <DateTime iso={review.started_at ?? review.created_at} />
        {/* How long it took, under when: one column, so the table fits beside
            the sidebar without scrolling sideways. */}
        {took !== null && <span className="block text-xs tabular-nums">took {took}</span>}
      </td>
    </tr>
  );
}

/** owner/name in a narrow column: the owner gives way first, so repos that
 * share one (RAIF-KARANI/…) still read apart; the full name is in the title. */
function RepoName({ id, fullName }: { id: number; fullName: string }) {
  const slash = fullName.indexOf("/");
  const owner = slash >= 0 ? fullName.slice(0, slash + 1) : "";
  const name = slash >= 0 ? fullName.slice(slash + 1) : fullName;
  return (
    <Link
      href={`/repos/${id}`}
      title={fullName}
      className="flex max-w-40 text-muted-foreground hover:text-foreground hover:underline"
    >
      {owner && <span className="min-w-[2ch] truncate">{owner}</span>}
      <span className="max-w-32 shrink-0 truncate">{name}</span>
    </Link>
  );
}
