import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { AutoRefresh } from "@/components/auto-refresh";
import { DateTime, SeverityCountsView, StatusBadge, Tag } from "@/components/badges";
import { Pager } from "@/components/pager";
import { TableSkeleton } from "@/components/skeleton";
import {
  getRepo,
  listReviews,
  REVIEW_STATUSES,
  type RepoDetail,
  type ReviewStatus,
  type ReviewSummary,
} from "@/lib/api";
import { formatDuration, shortSha } from "@/lib/format";
import { commitUrl, pullRequestUrl } from "@/lib/github-links";
import { parseChoice, parseCursor, parseId, withQuery } from "@/lib/params";

export const metadata: Metadata = { title: "Reviews" };

/** A repo's reviews, newest first, filterable by status. */
export default function RepoReviewsPage({ params, searchParams }: PageProps<"/repos/[id]">) {
  return (
    <Suspense fallback={<TableSkeleton label="Loading reviews" />}>
      <Reviews params={params} searchParams={searchParams} />
    </Suspense>
  );
}

async function Reviews({ params, searchParams }: PageProps<"/repos/[id]">) {
  const id = parseId((await params).id);
  if (id === null) notFound();
  const query = await searchParams;
  const status = parseChoice(query.status, REVIEW_STATUSES);
  const cursor = parseCursor(query.cursor);
  // Both at once; getRepo is also what 404s a repo the user can't see.
  const [repo, page] = await Promise.all([getRepo(id), listReviews(id, { status, cursor })]);
  const pathname = `/repos/${id}`;
  const busy = page.items.some((r) => r.status === "queued" || r.status === "running");

  return (
    <>
      <StatusFilter pathname={pathname} current={status} />
      {page.items.length === 0 ? (
        <p className="mt-6 rounded-lg border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
          {status
            ? `No ${status} reviews.`
            : repo.source === "github"
              ? "No reviews yet. CodeLens reviews every push to an open pull request."
              : "No reviews yet."}
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table className="w-full whitespace-nowrap text-left text-sm">
            <thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
              <tr>
                <th className="px-4 py-2 font-medium">Review</th>
                <th className="px-4 py-2 font-medium">Change</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Findings</th>
                <th className="px-4 py-2 font-medium">Took</th>
                <th className="px-4 py-2 font-medium">When</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
              {page.items.map((review) => (
                <ReviewRow key={review.id} review={review} repo={repo} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pager
        newest={cursor !== undefined ? withQuery(pathname, { status }) : null}
        older={
          page.next_cursor !== null
            ? withQuery(pathname, { status, cursor: page.next_cursor })
            : null
        }
      />
      <AutoRefresh active={busy} />
    </>
  );
}

function StatusFilter({ pathname, current }: { pathname: string; current?: ReviewStatus }) {
  const choices: (ReviewStatus | undefined)[] = [undefined, ...REVIEW_STATUSES];
  return (
    <nav aria-label="Filter by status" className="flex flex-wrap gap-1.5 text-xs">
      {choices.map((status) => {
        const selected = status === current;
        return (
          <Link
            key={status ?? "all"}
            href={withQuery(pathname, { status })}
            aria-current={selected ? "true" : undefined}
            className={`rounded-full border px-2.5 py-1 ${
              selected
                ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                : "border-zinc-300 text-zinc-600 hover:border-zinc-500 dark:border-zinc-700 dark:text-zinc-400"
            }`}
          >
            {status ?? "all"}
          </Link>
        );
      })}
    </nav>
  );
}

function ReviewRow({ review, repo }: { review: ReviewSummary; repo: RepoDetail }) {
  const onGitHub = repo.source === "github";
  return (
    <tr className="align-top">
      <td className="px-4 py-3 font-medium">#{review.id}</td>
      <td className="px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          {review.pr_number !== null && onGitHub ? (
            <a
              href={pullRequestUrl(repo.full_name, review.pr_number)}
              target="_blank"
              rel="noreferrer"
              className="hover:underline"
            >
              PR #{review.pr_number}
            </a>
          ) : (
            <Tag>{review.source === "eval" ? "eval case" : "branch"}</Tag>
          )}
          <span className="font-mono text-xs text-zinc-500">
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
        </div>
        {review.error && (
          <p className="mt-1 max-w-md truncate text-xs text-red-700 dark:text-red-400" title={review.error}>
            {review.error}
          </p>
        )}
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={review.status} />
      </td>
      <td className="px-4 py-3">
        {review.status === "completed" ? <SeverityCountsView counts={review.counts} /> : "—"}
      </td>
      <td className="px-4 py-3 tabular-nums text-zinc-600 dark:text-zinc-400">
        {formatDuration(review.started_at, review.finished_at) ?? "—"}
      </td>
      <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
        <DateTime iso={review.started_at ?? review.created_at} />
      </td>
    </tr>
  );
}
