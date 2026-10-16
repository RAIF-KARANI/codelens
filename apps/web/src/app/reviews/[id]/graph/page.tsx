import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { getReview, getReviewDiff, getReviewGraph } from "@/lib/api";
import { focusOf } from "@/lib/focus";
import { first, parseId } from "@/lib/params";

import { GraphView } from "./graph-view";

export const metadata: Metadata = { title: "Graph" };

/** The review's Graph tab: its changed code, who calls it, what it calls, and
 * the other code the model was shown. Opened from a finding
 * (`?finding=<id>`), it highlights that finding's changed code and evidence. */
export default function ReviewGraphPage({ params, searchParams }: PageProps<"/reviews/[id]/graph">) {
  return (
    <Suspense
      fallback={<div className="h-96 animate-pulse rounded-lg bg-muted" />}
    >
      <Graph params={params} searchParams={searchParams} />
    </Suspense>
  );
}

async function Graph({ params, searchParams }: PageProps<"/reviews/[id]/graph">) {
  const id = parseId((await params).id);
  if (id === null) notFound();
  const findingId = parseId(first((await searchParams).finding) ?? "");
  const [graph, review, diff] = await Promise.all([
    getReviewGraph(id),
    getReview(id),
    getReviewDiff(id),
  ]);
  if (graph.nodes.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-input p-8 text-center text-sm text-muted-foreground">
        {review.status === "queued" || review.status === "running"
          ? "The graph appears once the review has found the changed code."
          : "No graph: this review is from before reviews stored their changed code."}
      </p>
    );
  }
  const cited = [...new Set(review.findings.flatMap((f) => f.evidence_node_ids))];
  const finding = review.findings.find((f) => f.id === findingId);
  const focus = finding ? focusOf(finding, diff?.seeds ?? [], graph.nodes) : [];

  return (
    <>
      {finding && (
        <p className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-muted px-3 py-2 text-sm">
          <span>
            Highlighting <span className="font-medium">{finding.title}</span>: the changed code
            it&apos;s in and the evidence it cites
            {focus.length === 0 && " (neither is in the graph)"}.
          </span>
          <Link
            href={`/reviews/${id}#finding-${finding.id}`}
            className="text-xs underline underline-offset-4"
          >
            Back to the finding
          </Link>
          <Link href={`/reviews/${id}/graph`} className="text-xs underline underline-offset-4">
            Show everything
          </Link>
        </p>
      )}
      <GraphView nodes={graph.nodes} edges={graph.edges} cited={cited} focus={focus} />
    </>
  );
}
