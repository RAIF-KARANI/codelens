import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import {
  getReview,
  getReviewDiff,
  getReviewTrace,
  type ReviewDetail,
  type ReviewDiff,
  type Seed,
} from "@/lib/api";
import { parseDiff } from "@/lib/diff";
import { first, parseId, withQuery } from "@/lib/params";

import { DiffView } from "./diff-view";
import { FindingCard } from "./finding-card";
import { LiveTimeline } from "./live-timeline";

export const metadata: Metadata = { title: "Review" };

/** The review's Diff tab: what changed, what CodeLens found and where, and
 * the review's timeline (live while it runs). The header is the layout's. */
export default function ReviewPage({ params, searchParams }: PageProps<"/reviews/[id]">) {
  return (
    <Suspense
      fallback={<div className="h-64 animate-pulse rounded-lg bg-muted" />}
    >
      <Review params={params} searchParams={searchParams} />
    </Suspense>
  );
}

async function Review({ params, searchParams }: PageProps<"/reviews/[id]">) {
  const id = parseId((await params).id);
  if (id === null) notFound();
  const showDropped = first((await searchParams).dropped) === "1";
  const review = await getReview(id, { dropped: showDropped });
  const [diff, trace] = await Promise.all([getReviewDiff(id), getReviewTrace(id)]);
  // What the prompt called each piece of evidence, for the evidence buttons.
  const names = new Map<number, string>();
  for (const item of trace.chunks.flatMap((c) => c.items)) {
    if (item.node_id !== null) names.set(item.node_id, item.qualified_name);
  }
  const live = review.status === "queued" || review.status === "running";

  return (
    <>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <aside className="space-y-4 xl:order-2">
          <LiveTimeline reviewId={review.id} live={live} />
          {diff && diff.seeds.length > 0 && <ChangedCode seeds={diff.seeds} />}
          {review.token_usage && <Usage usage={review.token_usage} />}
        </aside>

        <div className="min-w-0 space-y-4 xl:order-1">
          {review.error && (
            <p className="rounded-md border border-critical/30 bg-critical-soft px-3 py-2 text-sm text-critical">
              {review.error}
            </p>
          )}
          {live ? (
            // The diff is stored when the review starts and the findings when
            // it ends; the timeline re-renders the page then.
            <p className="rounded-lg border border-dashed border-input p-6 text-center text-sm text-muted-foreground">
              The diff and the findings appear here when the review finishes.
            </p>
          ) : (
            <ReviewBody review={review} diff={diff} names={names} showDropped={showDropped} />
          )}
        </div>
      </div>
    </>
  );
}

function ReviewBody({
  review,
  diff,
  names,
  showDropped,
}: {
  review: ReviewDetail;
  diff: ReviewDiff | null;
  names: Map<number, string>;
  showDropped: boolean;
}) {
  const posted = review.findings.filter((f) => f.status === "posted");
  const dropped = review.findings.filter((f) => f.status === "dropped");
  return (
    <>
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-sm font-semibold">
          {posted.length} finding{posted.length === 1 ? "" : "s"}
        </h2>
        <Link
          href={withQuery(`/reviews/${review.id}`, { dropped: showDropped ? undefined : "1" })}
          className="text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
        >
          {showDropped ? "Hide dropped findings" : "Show findings validation dropped"}
        </Link>
      </div>
      {diff ? (
        <DiffView files={parseDiff(diff.diff)} findings={review.findings} names={names} />
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            This review is from before diffs were stored with reviews, so its findings are
            listed without the diff.
          </p>
          {review.findings.map((f) => (
            <FindingCard key={f.id} finding={f} names={names} />
          ))}
        </>
      )}
      {showDropped && dropped.length === 0 && (
        <p className="text-sm text-muted-foreground">Validation dropped nothing in this review.</p>
      )}
    </>
  );
}

const CHANGE_STYLES: Record<Seed["change"], string> = {
  added: "text-added",
  modified: "text-warning",
  deleted: "text-deleted",
};

/** The changed definitions the review started from (its seeds). */
function ChangedCode({ seeds }: { seeds: Seed[] }) {
  return (
    <section className="rounded-lg border border-border p-4">
      <h2 className="text-sm font-semibold">Changed code</h2>
      <ul className="mt-2 space-y-1.5 text-xs">
        {seeds.map((seed) => (
          <li key={`${seed.path}:${seed.qualified_name}`} className="min-w-0">
            <span className={`mr-1.5 ${CHANGE_STYLES[seed.change]}`}>{seed.change}</span>
            <span className="break-all font-mono">{seed.qualified_name}</span>
            {seed.signature_changed && <span className="ml-1.5 text-muted-foreground">(signature)</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Usage({ usage }: { usage: Record<string, number> }) {
  const rows = [
    ["Prompt tokens", usage.input_tokens],
    ["Tokens written", usage.output_tokens],
  ] as const;
  return (
    <section className="rounded-lg border border-border p-4 text-xs">
      <h2 className="text-sm font-semibold">Model usage</h2>
      <dl className="mt-2 grid grid-cols-2 gap-1">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="text-right tabular-nums">{value?.toLocaleString("en") ?? "—"}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
