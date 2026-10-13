import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { getReview, getReviewGraph } from "@/lib/api";
import { parseId } from "@/lib/params";

import { GraphView } from "./graph-view";

export const metadata: Metadata = { title: "Graph" };

/** The review's Graph tab: its changed code, who calls it, what it calls, and
 * the other code the model was shown. */
export default function ReviewGraphPage({ params }: PageProps<"/reviews/[id]/graph">) {
  return (
    <Suspense
      fallback={<div className="h-96 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />}
    >
      <Graph params={params} />
    </Suspense>
  );
}

async function Graph({ params }: Pick<PageProps<"/reviews/[id]/graph">, "params">) {
  const id = parseId((await params).id);
  if (id === null) notFound();
  const [graph, review] = await Promise.all([getReviewGraph(id), getReview(id)]);
  if (graph.nodes.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
        {review.status === "queued" || review.status === "running"
          ? "The graph appears once the review has found the changed code."
          : "No graph: this review is from before reviews stored their changed code."}
      </p>
    );
  }
  const cited = [...new Set(review.findings.flatMap((f) => f.evidence_node_ids))];
  return <GraphView nodes={graph.nodes} edges={graph.edges} cited={cited} />;
}
