import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { getReview, getReviewTrace } from "@/lib/api";
import { parseId } from "@/lib/params";
import { tokensUsed } from "@/lib/trace";

import { TraceTable } from "./trace-table";

export const metadata: Metadata = { title: "Trace" };

/** The review's Trace tab: for each chunk, how much of its context budget was
 * spent, and every candidate retrieval found, how it ranked, and whether the
 * model was shown it (and if not, why). The same rows the evals are scored
 * from. */
export default function ReviewTracePage({ params }: PageProps<"/reviews/[id]/trace">) {
  return (
    <Suspense fallback={<div className="h-64 animate-pulse rounded-lg bg-muted" />}>
      <Trace params={params} />
    </Suspense>
  );
}

async function Trace({ params }: Pick<PageProps<"/reviews/[id]/trace">, "params">) {
  const id = parseId((await params).id);
  if (id === null) notFound();
  const [trace, review] = await Promise.all([getReviewTrace(id), getReview(id)]);
  if (trace.chunks.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-input p-8 text-center text-sm text-muted-foreground">
        {review.status === "queued" || review.status === "running"
          ? "The trace appears once the review has retrieved its context."
          : "Nothing was retrieved for this review."}
      </p>
    );
  }
  const cited = [...new Set(review.findings.flatMap((f) => f.evidence_node_ids))];
  const contextBudget = trace.budgets?.context;

  return (
    <div className="space-y-8">
      {trace.budgets && <Budgets budgets={trace.budgets} />}
      {trace.chunks.map((chunk) => {
        const used = tokensUsed(chunk.items);
        const shown = chunk.items.filter((i) => i.included).length;
        return (
          <section key={chunk.index} aria-labelledby={`chunk-${chunk.index}`}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <h2 id={`chunk-${chunk.index}`} className="text-sm font-semibold">
                {trace.chunks.length > 1 ? `Chunk ${chunk.index + 1}: ` : ""}what retrieval found
              </h2>
              <span className="text-xs text-muted-foreground">
                {shown} of {chunk.items.length} candidates shown to the model
              </span>
            </div>
            {contextBudget ? <ContextMeter used={used} budget={contextBudget} /> : null}
            <TraceTable items={chunk.items} cited={cited} />
          </section>
        );
      })}
      <p className="text-xs text-muted-foreground">
        Findings validation dropped, with the reason, are on the{" "}
        <Link href={`/reviews/${id}?dropped=1`} className="underline underline-offset-4">
          Diff tab
        </Link>
        .
      </p>
    </div>
  );
}

/** How much of the chunk's context budget the candidates shown used: a meter
 * whose track is a lighter step of its fill's colour. */
function ContextMeter({ used, budget }: { used: number; budget: number }) {
  const share = Math.min(1, used / budget);
  return (
    <div className="mt-2 flex items-center gap-3">
      <div
        role="meter"
        aria-label="Context tokens used"
        aria-valuemin={0}
        aria-valuemax={budget}
        aria-valuenow={used}
        className="h-2 w-full max-w-sm overflow-hidden rounded-full bg-accent"
      >
        <div className="h-full rounded-full bg-chart-1" style={{ width: `${share * 100}%` }} />
      </div>
      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
        {used.toLocaleString("en")} of {budget.toLocaleString("en")} context tokens
        {used > budget && " (over: the window was fitted)"}
      </span>
    </div>
  );
}

/** The packer's token limits for each chunk's prompt (pipeline config): the
 * diff, the changed code in full, and the related code (context). Segments are
 * separated by a 2px gap in the surface colour, and each is named in the key
 * below, so no part is told apart by colour alone. */
function Budgets({ budgets }: { budgets: Record<string, number> }) {
  const parts = [
    { key: "diff", label: "Diff", style: "bg-muted-foreground/70" },
    { key: "changed", label: "Changed code", style: "bg-warning" },
    { key: "context", label: "Context", style: "bg-chart-1" },
  ].filter((p) => typeof budgets[p.key] === "number");
  const total = parts.reduce((sum, p) => sum + budgets[p.key], 0);
  if (total === 0) return null;
  return (
    <section aria-labelledby="budgets-heading" className="rounded-xl border border-border bg-card p-4">
      <h2 id="budgets-heading" className="text-sm font-semibold">
        Token budgets per chunk
      </h2>
      <p className="mt-0.5 text-xs text-muted-foreground">
        How the prompt is shared out, before the model writes up to its own output limit.
      </p>
      <div className="mt-3 flex h-3 gap-0.5" aria-hidden="true">
        {parts.map((p) => (
          <span
            key={p.key}
            className={`rounded-[3px] ${p.style}`}
            style={{ width: `${(budgets[p.key] / total) * 100}%` }}
          />
        ))}
      </div>
      <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs">
        {parts.map((p) => (
          <div key={p.key} className="flex items-center gap-1.5">
            <span className={`size-2 rounded-full ${p.style}`} aria-hidden="true" />
            <dt className="text-muted-foreground">{p.label}</dt>
            <dd className="tabular-nums">up to {budgets[p.key].toLocaleString("en")} tokens</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
