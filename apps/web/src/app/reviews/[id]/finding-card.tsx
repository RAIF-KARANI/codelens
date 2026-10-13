import { Tag } from "@/components/badges";
import type { Finding, Severity } from "@/lib/api";
import { isDemo } from "@/lib/config";

import { EvidenceButton } from "./evidence";
import { GraphLink } from "./graph-link";
import { VoteButtons } from "./vote-buttons";

export const SEVERITY_STYLES: Record<Severity, { badge: string; line: string; border: string }> = {
  critical: {
    badge: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
    line: "shadow-[inset_3px_0_0_var(--color-red-500)]",
    border: "border-red-400 dark:border-red-700",
  },
  warning: {
    badge: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
    line: "shadow-[inset_3px_0_0_var(--color-amber-500)]",
    border: "border-amber-400 dark:border-amber-700",
  },
  suggestion: {
    badge: "bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300",
    line: "shadow-[inset_3px_0_0_var(--color-sky-500)]",
    border: "border-sky-400 dark:border-sky-700",
  },
};

/** One finding: what, why, the fix, the evidence it rests on, and 👍/👎.
 * `names` maps evidence node ids to what the prompt called them. */
export function FindingCard({
  finding,
  names,
}: {
  finding: Finding;
  names: Map<number, string>;
}) {
  const dropped = finding.status === "dropped";
  const style = SEVERITY_STYLES[finding.severity];
  return (
    <article
      id={`finding-${finding.id}`}
      className={`scroll-mt-20 rounded-md border-l-4 bg-white p-3 font-sans shadow-sm ring-1 ring-zinc-200 dark:bg-zinc-950 dark:ring-zinc-800 ${style.border} ${dropped ? "opacity-70" : ""}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${style.badge}`}>
          {finding.severity}
        </span>
        <Tag>{finding.category}</Tag>
        <h3 className="text-sm font-semibold">{finding.title}</h3>
        <span className="ml-auto font-mono text-xs text-zinc-500">
          {finding.file_path}:{finding.line_start}
          {finding.line_end !== finding.line_start && `–${finding.line_end}`}
        </span>
      </header>
      {dropped && (
        <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
          <span className="font-medium">Dropped by validation:</span> {finding.drop_reason}
        </p>
      )}
      <p className="mt-2 whitespace-pre-wrap text-sm text-zinc-700 dark:text-zinc-300">
        {finding.explanation}
      </p>
      {finding.suggested_fix && (
        <div className="mt-2">
          <p className="text-xs font-medium text-zinc-500">Suggested fix</p>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded bg-zinc-50 p-2 font-mono text-xs dark:bg-zinc-900">
            {finding.suggested_fix}
          </pre>
        </div>
      )}
      <footer className="mt-3 flex flex-wrap items-center gap-2">
        {finding.evidence_node_ids.length > 0 ? (
          <div className="flex min-w-0 flex-wrap items-center gap-1.5">
            <span className="text-xs text-zinc-500">Evidence</span>
            {finding.evidence_node_ids.map((nodeId) => (
              <EvidenceButton
                key={nodeId}
                nodeId={nodeId}
                label={names.get(nodeId) ?? `node ${nodeId}`}
              />
            ))}
          </div>
        ) : (
          <span className="text-xs text-zinc-500">No evidence cited</span>
        )}
        {finding.confidence !== null && (
          <span className="text-xs text-zinc-500">
            confidence {Math.round(finding.confidence * 100)}%
          </span>
        )}
        <GraphLink findingId={finding.id} />
        {/* Votes are writes: a read-only demo has none. */}
        {!dropped && !isDemo() && (
          <div className="ml-auto">
            <VoteButtons findingId={finding.id} votes={finding.votes} myVote={finding.my_vote} />
          </div>
        )}
      </footer>
    </article>
  );
}
