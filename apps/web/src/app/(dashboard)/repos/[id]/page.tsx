import { GitPullRequestIcon } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { AutoRefresh } from "@/components/auto-refresh";
import { Pager } from "@/components/pager";
import { ReviewTable } from "@/components/review-table";
import { TableSkeleton } from "@/components/skeleton";
import { StateMessage } from "@/components/state-message";
import { StatusFilter } from "@/components/status-filter";
import { Button } from "@/components/ui/button";
import { getRepo, listReviews, REVIEW_STATUSES } from "@/lib/api";
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
        <StateMessage
          className="mt-6"
          icon={<GitPullRequestIcon />}
          title={status ? `No ${status} reviews` : "No reviews yet"}
          action={
            status ? (
              <Button asChild variant="outline">
                <Link href={pathname}>Show all reviews</Link>
              </Button>
            ) : undefined
          }
        >
          {status
            ? "None of this repository's reviews has this status."
            : repo.source === "github"
              ? "CodeLens reviews every push to an open pull request on this repository."
              : "Review a branch of this checkout with python -m core.review."}
        </StateMessage>
      ) : (
        <div className="mt-4">
          <ReviewTable reviews={page.items} repoName={repo.full_name} />
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
