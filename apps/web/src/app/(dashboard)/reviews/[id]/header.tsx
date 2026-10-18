import { DateTime, SeverityCountsView, StatusBadge, Tag } from "@/components/badges";
import type { RepoDetail, ReviewDetail } from "@/lib/api";
import { formatDuration, shortSha } from "@/lib/format";
import { commitUrl, pullRequestUrl } from "@/lib/github-links";

/** What the review is (title, PR, commits, model, timing) and what it found. */
export function ReviewHeader({ review, repo }: { review: ReviewDetail; repo: RepoDetail }) {
  const onGitHub = repo.source === "github";
  const name =
    review.title ?? (review.pr_number !== null ? `PR #${review.pr_number}` : `Review #${review.id}`);
  const took = formatDuration(review.started_at, review.finished_at);
  return (
    <header className="mt-3">
      <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold tracking-tight">
        <span className="break-words">{name}</span>
        <StatusBadge status={review.status} />
        {review.source !== "github" && <Tag>{review.source === "eval" ? "eval case" : "branch"}</Tag>}
      </h1>
      <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <span>Review #{review.id}</span>
        {review.pr_number !== null && onGitHub && (
          <a
            href={pullRequestUrl(repo.full_name, review.pr_number)}
            target="_blank"
            rel="noreferrer"
            className="hover:underline"
          >
            PR #{review.pr_number} on GitHub ↗
          </a>
        )}
        <span className="font-mono text-xs">
          {shortSha(review.base_sha)}…
          {onGitHub ? (
            <a
              href={commitUrl(repo.full_name, review.head_sha)}
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
        {review.model && <span>{review.model}</span>}
        {took && <span>took {took}</span>}
        <DateTime iso={review.started_at ?? review.created_at} />
      </p>
      {review.status === "completed" && (
        <p className="mt-2 text-sm">
          <SeverityCountsView counts={review.counts} />
        </p>
      )}
    </header>
  );
}
