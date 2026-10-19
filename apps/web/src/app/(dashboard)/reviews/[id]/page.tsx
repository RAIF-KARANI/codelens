import { ChevronDownIcon, FileDiffIcon } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import {
  type Finding,
  getReview,
  getReviewDiff,
  getReviewTrace,
  type ReviewDetail,
  type ReviewDiff,
  type Seed,
} from "@/lib/api";
import { StateMessage } from "@/components/state-message";
import { isDemo } from "@/lib/config";
import { findingOrder, type Token } from "@/lib/code";
import { type DiffFile, filePath, parseDiff } from "@/lib/diff";
import { highlightDiff } from "@/lib/highlight";
import { first, parseId, withQuery } from "@/lib/params";

import { reviewAgain } from "./actions";
import { DiffView } from "./diff-view";
import { FindingCard } from "./finding-card";
import { LiveTimeline } from "./live-timeline";
import { ProgressStepper } from "./progress-stepper";
import { ReviewAgainButton } from "./review-again-button";
import { ReviewEventsProvider } from "./review-events";
import { FindingSlot, ReviewNav } from "./review-nav";

export const metadata: Metadata = { title: "Review" };

/** The review's Diff tab, findings first: a panel with the findings (filters,
 * j/k), the files and the run's details, beside the highlighted diff with each
 * finding on its line. While the review runs, its live timeline instead. The
 * header is the layout's. */
export default function ReviewPage({ params, searchParams }: PageProps<"/reviews/[id]">) {
  return (
    <Suspense fallback={<div className="h-64 animate-pulse rounded-lg bg-muted" />}>
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
  const details = (
    <RunDetails open={live}>
      <LiveTimeline />
      {diff && diff.seeds.length > 0 && <ChangedCode seeds={diff.seeds} />}
      {review.token_usage && <Usage usage={review.token_usage} />}
    </RunDetails>
  );

  if (live) {
    // The diff is stored when the review starts and the findings when it
    // ends; the event stream re-renders the page then.
    return (
      <ReviewEventsProvider reviewId={review.id} live>
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="space-y-3 lg:order-1">
            <ProgressStepper
              startedAt={review.started_at ?? review.created_at}
              posts={review.source === "github"}
            />
            <p className="text-center text-sm text-muted-foreground">
              The diff and the findings appear here when the review finishes.
            </p>
          </div>
          <div className="lg:order-2">{details}</div>
        </div>
      </ReviewEventsProvider>
    );
  }

  const files = diff ? parseDiff(diff.diff) : [];
  const tokens = await highlightDiff(files);
  const ordered = findingOrder(files, review.findings);
  return (
    <ReviewEventsProvider reviewId={review.id} live={false}>
      <ReviewNav
        reviewId={review.id}
        findings={ordered.map((f) => ({
          id: f.id,
          severity: f.severity,
          category: f.category,
          title: f.title,
          file_path: f.file_path,
          line_start: f.line_start,
          dropped: f.status === "dropped",
          evidence:
            f.evidence_node_ids.length > 0
              ? {
                  nodeId: f.evidence_node_ids[0],
                  label: names.get(f.evidence_node_ids[0]) ?? `node ${f.evidence_node_ids[0]}`,
                }
              : null,
        }))}
        files={files.map((file) => ({
          path: filePath(file),
          status: file.status,
          findings: review.findings.filter((f) => f.file_path === filePath(file)).length,
        }))}
        details={details}
      >
        <div className="space-y-4">
          {review.error && (
            <p className="rounded-md border border-critical/30 bg-critical-soft px-3 py-2 text-sm text-critical">
              {review.error}
            </p>
          )}
          {/* A failed pull request review can be queued again (D181); the
              demo is read-only. */}
          {review.status === "failed" && review.source === "github" && review.pr_number !== null && !isDemo() && (
            <ReviewAgainButton action={reviewAgain.bind(null, review.id)} />
          )}
          <ReviewBody
            review={review}
            diff={diff}
            files={files}
            tokens={tokens}
            ordered={ordered}
            names={names}
            showDropped={showDropped}
          />
        </div>
      </ReviewNav>
    </ReviewEventsProvider>
  );
}

function ReviewBody({
  review,
  diff,
  files,
  tokens,
  ordered,
  names,
  showDropped,
}: {
  review: ReviewDetail;
  diff: ReviewDiff | null;
  files: DiffFile[];
  tokens: Token[][][][];
  ordered: Finding[];
  names: Map<number, string>;
  showDropped: boolean;
}) {
  const dropped = review.findings.filter((f) => f.status === "dropped");
  return (
    <>
      <div className="flex flex-wrap items-center justify-end gap-3">
        <Link
          href={withQuery(`/reviews/${review.id}`, { dropped: showDropped ? undefined : "1" })}
          className="text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
        >
          {showDropped ? "Hide dropped findings" : "Show findings validation dropped"}
        </Link>
      </div>
      {diff ? (
        <DiffView files={files} tokens={tokens} findings={review.findings} names={names} />
      ) : (
        <>
          <StateMessage className="py-6" icon={<FileDiffIcon />} title="No diff stored">
            This review is from before reviews stored the diff they read
            {ordered.length > 0 ? ", so its findings are listed on their own." : "."}
          </StateMessage>
          {ordered.map((f) => (
            <FindingSlot key={f.id} id={f.id}>
              <FindingCard finding={f} names={names} />
            </FindingSlot>
          ))}
        </>
      )}
      {showDropped && dropped.length === 0 && (
        <p className="text-sm text-muted-foreground">Validation dropped nothing in this review.</p>
      )}
    </>
  );
}

/** The run's own story (timeline, changed code, tokens), folded away once the
 * review is done: the findings come first (planning-v2.md, Phase 10 step 4). */
function RunDetails({ open, children }: { open: boolean; children: React.ReactNode }) {
  return (
    <details open={open} className="group rounded-xl border border-border bg-card">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2.5 text-sm font-semibold [&::-webkit-details-marker]:hidden">
        Run details
        <ChevronDownIcon
          className="size-4 text-muted-foreground transition-transform group-open:rotate-180"
          aria-hidden="true"
        />
      </summary>
      <div className="space-y-3 border-t border-border p-3 [&>section]:rounded-lg [&>section]:border-0 [&>section]:p-0">
        {children}
      </div>
    </details>
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
            {seed.signature_changed && (
              <span className="ml-1.5 text-muted-foreground">(signature)</span>
            )}
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
