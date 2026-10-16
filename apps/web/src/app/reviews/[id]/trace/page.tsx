import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { getReview, getReviewTrace, type TraceItem } from "@/lib/api";
import { parseId } from "@/lib/params";
import { type Outcome, outcome, retrievedFor, score, tokensUsed } from "@/lib/trace";

import { EvidenceButton } from "../evidence";

export const metadata: Metadata = { title: "Trace" };

/** The review's Trace tab: for each piece of changed code, what retrieval
 * found, how it ranked, and whether the model was shown it (and if not, why).
 * The same rows the evals are scored from. */
export default function ReviewTracePage({ params }: PageProps<"/reviews/[id]/trace">) {
  return (
    <Suspense
      fallback={<div className="h-64 animate-pulse rounded-lg bg-muted" />}
    >
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
  const cited = new Set(review.findings.flatMap((f) => f.evidence_node_ids));
  const contextBudget = trace.budgets?.context;

  return (
    <div className="space-y-8">
      {trace.budgets && <Budgets budgets={trace.budgets} />}
      {trace.chunks.map((chunk) => (
        <section key={chunk.index} aria-label={`Chunk ${chunk.index + 1}`}>
          <div className="flex flex-wrap items-baseline gap-x-3">
            <h2 className="text-sm font-semibold">
              {trace.chunks.length > 1 ? `Chunk ${chunk.index + 1}: ` : ""}what retrieval found
            </h2>
            <span className="text-xs text-muted-foreground">
              {chunk.items.filter((i) => i.included).length} of {chunk.items.length} candidates
              shown, {tokensUsed(chunk.items).toLocaleString("en")}
              {contextBudget ? ` of ${contextBudget.toLocaleString("en")}` : ""} context tokens
            </span>
          </div>
          <div className="mt-3 space-y-4">
            {groupBySeed(chunk.items).map(([seed, items]) => (
              <SeedGroup key={seed} seed={seed} items={items} cited={cited} />
            ))}
          </div>
        </section>
      ))}
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

/** Candidates under the changed code they were retrieved for, best first. A
 * candidate found for several shows under each. */
function groupBySeed(items: TraceItem[]): [string, TraceItem[]][] {
  const groups = new Map<string, TraceItem[]>();
  for (const item of items) {
    const seeds = retrievedFor(item);
    for (const seed of seeds.length ? seeds : ["(other)"]) {
      if (!groups.has(seed)) groups.set(seed, []);
      groups.get(seed)!.push(item);
    }
  }
  for (const group of groups.values()) {
    group.sort((a, b) => (score(a, "vector_rank") ?? 99) - (score(b, "vector_rank") ?? 99));
  }
  return [...groups.entries()];
}

const OUTCOME_STYLES: Record<Outcome["kind"], string> = {
  included: "text-added",
  signature: "text-warning",
  excluded: "text-muted-foreground",
};

function SeedGroup({
  seed,
  items,
  cited,
}: {
  seed: string;
  items: TraceItem[];
  cited: Set<number>;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <div className="bg-muted px-3 py-2 text-xs">
        <span className="text-muted-foreground">For the changed </span>
        <span className="break-all font-mono font-medium">{seed}</span>
      </div>
      <table className="w-full whitespace-nowrap text-left text-sm">
        <thead className="text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-1.5 font-medium">Rank</th>
            <th className="px-3 py-1.5 font-medium">Candidate</th>
            <th className="px-3 py-1.5 font-medium">Similarity</th>
            <th className="px-3 py-1.5 font-medium">Outcome</th>
            <th className="px-3 py-1.5 text-right font-medium">Tokens</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/60">
          {items.map((item) => {
            const result = outcome(item);
            const similarity = score(item, "vector_similarity");
            return (
              <tr key={item.label} className={item.included ? "" : "text-muted-foreground"}>
                <td className="px-3 py-1.5 tabular-nums">{score(item, "vector_rank") ?? "—"}</td>
                <td className="max-w-md px-3 py-1.5">
                  <span className="flex items-center gap-1.5">
                    {item.node_id !== null && cited.has(item.node_id) && (
                      <span className="text-warning" title="Cited as evidence by a finding">
                        ★
                      </span>
                    )}
                    {item.node_id !== null ? (
                      <EvidenceButton nodeId={item.node_id} label={item.qualified_name} />
                    ) : (
                      <span className="font-mono text-xs" title="Deleted by a later re-index">
                        {item.qualified_name}
                      </span>
                    )}
                  </span>
                </td>
                <td className="px-3 py-1.5">
                  {similarity !== null ? <SimilarityBar value={similarity} /> : "—"}
                </td>
                <td className={`px-3 py-1.5 text-xs ${OUTCOME_STYLES[result.kind]}`}>
                  {result.text}
                </td>
                <td className="px-3 py-1.5 text-right tabular-nums">
                  {item.included ? item.token_cost.toLocaleString("en") : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SimilarityBar({ value }: { value: number }) {
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
        <span
          className="block h-full rounded-full bg-primary"
          style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
        />
      </span>
      <span className="text-xs tabular-nums">{value.toFixed(3)}</span>
    </span>
  );
}

/** The packer's token limits for each chunk's prompt (pipeline config): the
 * diff, the changed code in full, and the related code (context). */
function Budgets({ budgets }: { budgets: Record<string, number> }) {
  const parts = [
    { key: "diff", label: "Diff", style: "bg-muted-foreground/70" },
    { key: "changed", label: "Changed code", style: "bg-warning" },
    { key: "context", label: "Context", style: "bg-primary" },
  ].filter((p) => typeof budgets[p.key] === "number");
  const total = parts.reduce((sum, p) => sum + budgets[p.key], 0);
  if (total === 0) return null;
  return (
    <section aria-label="Token budgets">
      <h2 className="text-sm font-semibold">Token budgets per chunk</h2>
      <div className="mt-2 flex h-3 overflow-hidden rounded-full">
        {parts.map((p) => (
          <span
            key={p.key}
            className={p.style}
            style={{ width: `${(budgets[p.key] / total) * 100}%` }}
          />
        ))}
      </div>
      <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
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
