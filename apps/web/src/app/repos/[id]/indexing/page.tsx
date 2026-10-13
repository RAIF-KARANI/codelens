import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { DateTime, StatusBadge } from "@/components/badges";
import { Pager } from "@/components/pager";
import { TableSkeleton } from "@/components/skeleton";
import { getRepo, type IndexRun, listIndexRuns } from "@/lib/api";
import { formatCount, formatDuration, formatPercent, shortSha } from "@/lib/format";
import { parseCursor, parseId, withQuery } from "@/lib/params";

export const metadata: Metadata = { title: "Indexing" };

/** What the repo's code graph holds now, and every indexing run. */
export default function RepoIndexingPage({
  params,
  searchParams,
}: PageProps<"/repos/[id]/indexing">) {
  return (
    <Suspense fallback={<TableSkeleton label="Loading index history" />}>
      <Indexing params={params} searchParams={searchParams} />
    </Suspense>
  );
}

async function Indexing({ params, searchParams }: PageProps<"/repos/[id]/indexing">) {
  const id = parseId((await params).id);
  if (id === null) notFound();
  const cursor = parseCursor((await searchParams).cursor);
  const [repo, page] = await Promise.all([getRepo(id), listIndexRuns(id, cursor)]);
  const pathname = `/repos/${id}/indexing`;
  const latest = repo.latest_index_run;

  return (
    <>
      {latest && cursor === undefined && <LatestRun run={latest} />}
      <h2 className="mt-8 text-sm font-semibold">History</h2>
      {page.items.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">Never indexed.</p>
      ) : (
        <div className="mt-3 overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table className="w-full whitespace-nowrap text-left text-sm">
            <thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
              <tr>
                <th className="px-4 py-2 font-medium">Run</th>
                <th className="px-4 py-2 font-medium">Commit</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Files</th>
                <th className="px-4 py-2 font-medium">Nodes</th>
                <th className="px-4 py-2 font-medium">Took</th>
                <th className="px-4 py-2 font-medium">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
              {page.items.map((run) => (
                <RunRow key={run.id} run={run} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pager
        newest={cursor !== undefined ? pathname : null}
        older={page.next_cursor !== null ? withQuery(pathname, { cursor: page.next_cursor }) : null}
      />
    </>
  );
}

/** The code graph as of the latest run: what retrieval and the graph view
 * work from. */
function LatestRun({ run }: { run: IndexRun }) {
  const { stats } = run;
  const items = [
    { label: "Code nodes", value: formatCount(stats.db_totals?.nodes) },
    { label: "Edges", value: formatCount(stats.db_totals?.edges) },
    { label: "Files parsed (last run)", value: formatCount(stats.files?.parsed) },
    // How many calls the resolver could tie to a definition: the call graph's coverage.
    { label: "Calls resolved (last run)", value: formatPercent(stats.calls?.resolution) },
  ];
  return (
    <section aria-label="Latest index">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {items.map(({ label, value }) => (
          <div
            key={label}
            className="rounded-lg border border-zinc-200 px-4 py-3 dark:border-zinc-800"
          >
            <dt className="text-xs text-zinc-500">{label}</dt>
            <dd className="mt-1 text-lg font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function RunRow({ run }: { run: IndexRun }) {
  const files = run.stats.files;
  const nodes = run.stats.nodes;
  return (
    <tr className="align-top">
      <td className="px-4 py-3 font-medium">#{run.id}</td>
      <td className="px-4 py-3 font-mono text-xs text-zinc-600 dark:text-zinc-400">
        {shortSha(run.commit_sha)}
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={run.status} />
        {run.error && (
          <p className="mt-1 max-w-xs truncate text-xs text-red-700 dark:text-red-400" title={run.error}>
            {run.error}
          </p>
        )}
      </td>
      <td className="px-4 py-3 text-xs tabular-nums text-zinc-600 dark:text-zinc-400">
        {files ? (
          <>
            {formatCount(files.parsed)} parsed
            <br />+{files.new ?? 0} ~{files.changed ?? 0} −{files.deleted ?? 0}
          </>
        ) : (
          "—"
        )}
      </td>
      <td className="px-4 py-3 text-xs tabular-nums text-zinc-600 dark:text-zinc-400">
        {nodes ? `+${nodes.inserted ?? 0} ~${nodes.updated ?? 0} −${nodes.deleted ?? 0}` : "—"}
      </td>
      <td className="px-4 py-3 tabular-nums text-zinc-600 dark:text-zinc-400">
        {formatDuration(run.started_at, run.finished_at) ?? "—"}
      </td>
      <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
        <DateTime iso={run.started_at} />
      </td>
    </tr>
  );
}
