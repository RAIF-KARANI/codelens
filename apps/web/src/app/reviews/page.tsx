import { GitPullRequestIcon } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { AutoRefresh } from "@/components/auto-refresh";
import { Pager } from "@/components/pager";
import { ReviewTable } from "@/components/review-table";
import { TableSkeleton } from "@/components/skeleton";
import { StateMessage } from "@/components/state-message";
import { StatusFilter } from "@/components/status-filter";
import { Button } from "@/components/ui/button";
import { listAllReviews, REVIEW_STATUSES } from "@/lib/api";
import { parseChoice, parseCursor, withQuery } from "@/lib/params";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Reviews" };

const PATHNAME = "/reviews";

/** Every review across the repos you can see, newest first, filterable by
 * status. Eval runs are measurements, so they're hidden unless asked for
 * (`?eval=1`, D155). */
export default function ReviewsPage({ searchParams }: PageProps<"/reviews">) {
  return (
    <>
      <h1 className="text-xl font-semibold tracking-tight">Reviews</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Every review across the repositories you can see, newest first.
      </p>
      <Suspense fallback={<TableSkeleton label="Loading reviews" />}>
        <Reviews searchParams={searchParams} />
      </Suspense>
    </>
  );
}

async function Reviews({ searchParams }: Pick<PageProps<"/reviews">, "searchParams">) {
  const query = await searchParams;
  const status = parseChoice(query.status, REVIEW_STATUSES);
  const cursor = parseCursor(query.cursor);
  const includeEval = query.eval === "1";
  const keep = { eval: includeEval ? "1" : undefined };
  const page = await listAllReviews({ status, cursor, includeEval });
  const busy = page.items.some((r) => r.status === "queued" || r.status === "running");

  return (
    <>
      <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
        <StatusFilter pathname={PATHNAME} current={status} query={keep} />
        <Link
          href={withQuery(PATHNAME, { status, eval: includeEval ? undefined : "1" })}
          className={cn(
            "rounded-full border px-2.5 py-1 text-xs transition-colors",
            includeEval
              ? "border-primary bg-accent text-accent-foreground"
              : "border-input text-muted-foreground hover:border-primary/60 hover:text-foreground",
          )}
        >
          {includeEval ? "Showing eval runs" : "Show eval runs"}
        </Link>
      </div>
      {page.items.length === 0 ? (
        <StateMessage
          className="mt-6"
          icon={<GitPullRequestIcon />}
          title={status ? `No ${status} reviews` : "No reviews yet"}
          action={
            status ? (
              <Button asChild variant="outline">
                <Link href={withQuery(PATHNAME, keep)}>Show all reviews</Link>
              </Button>
            ) : undefined
          }
        >
          {status
            ? "None of the reviews you can see has this status."
            : "CodeLens reviews every push to an open pull request in a repository its App is installed on."}
        </StateMessage>
      ) : (
        <div className="mt-4">
          <ReviewTable reviews={page.items} />
        </div>
      )}
      <Pager
        newest={cursor !== undefined ? withQuery(PATHNAME, { status, ...keep }) : null}
        older={
          page.next_cursor !== null
            ? withQuery(PATHNAME, { status, ...keep, cursor: page.next_cursor })
            : null
        }
      />
      <AutoRefresh active={busy} />
    </>
  );
}
