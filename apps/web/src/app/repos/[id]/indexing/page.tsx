import { DatabaseIcon, FileCode2Icon, HistoryIcon, NetworkIcon, WaypointsIcon } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { DateTime, StatusBadge } from "@/components/badges";
import { Pager } from "@/components/pager";
import { TableSkeleton } from "@/components/skeleton";
import { StateMessage } from "@/components/state-message";
import { StatTile } from "@/components/stat-tile";
import { Button } from "@/components/ui/button";
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
        cursor === undefined ? (
          <StateMessage
            className="mt-3"
            icon={<HistoryIcon />}
            title={repo.status === "pending" ? "Not indexed yet" : "Never indexed"}
          >
            {repo.source !== "github"
              ? "Index it from its checkout with python -m core.index, or with Re-index if the API can read the checkout."
              : repo.status === "pending"
                ? "Its first index is queued: CodeLens indexes a repository when its App is installed on it. This page updates when the worker picks it up."
                : "CodeLens indexes a repository when its App is installed on it, and again on every push to its default branch. Re-index starts one now."}
          </StateMessage>
        ) : (
          <StateMessage
            className="mt-3"
            icon={<HistoryIcon />}
            title="No older runs"
            action={
              <Button asChild variant="outline">
                <Link href={pathname}>Back to the newest</Link>
              </Button>
            }
          >
            This page is past the repository&apos;s first index run.
          </StateMessage>
        )
      ) : (
        <div className="mt-3 overflow-x-auto rounded-lg border border-border bg-card">
          <table className="w-full whitespace-nowrap text-left text-sm">
            <thead className="bg-muted text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Run</th>
                <th className="px-3 py-2 font-medium">Commit</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Files</th>
                <th className="px-3 py-2 font-medium">Definitions</th>
                <th className="px-3 py-2 font-medium">Took</th>
                <th className="px-3 py-2 font-medium">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {page.items.map((run) => (
                <RunRow key={run.id} run={run} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {/* An empty page's state message has its own way back. */}
      {page.items.length > 0 && (
        <Pager
          newest={cursor !== undefined ? pathname : null}
          older={page.next_cursor !== null ? withQuery(pathname, { cursor: page.next_cursor }) : null}
        />
      )}
    </>
  );
}

/** The code graph as of the latest run: what retrieval and the graph view
 * work from. */
function LatestRun({ run }: { run: IndexRun }) {
  const { stats } = run;
  const resolution = stats.calls?.resolution;
  return (
    <section aria-label="The code graph now" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <StatTile
        label="Definitions"
        icon={<DatabaseIcon />}
        value={formatCount(stats.db_totals?.nodes)}
        detail="Modules, classes, functions and methods"
      />
      <StatTile
        label="Edges"
        icon={<WaypointsIcon />}
        value={formatCount(stats.db_totals?.edges)}
        detail="Calls, imports, inheritance and containment"
      />
      <StatTile
        label="Files parsed"
        icon={<FileCode2Icon />}
        value={formatCount(stats.files?.parsed)}
        detail={`In the latest run, at ${shortSha(run.commit_sha)}`}
      />
      <StatTile
        label="Calls resolved"
        icon={<NetworkIcon />}
        value={formatPercent(resolution)}
        detail={
          // How many calls the resolver tied to a definition: the call graph's coverage.
          resolution !== undefined ? (
            <span className="flex items-center gap-2">
              <span
                role="meter"
                aria-label="Calls resolved"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(resolution * 100)}
                className="h-1.5 w-full overflow-hidden rounded-full bg-accent"
              >
                <span
                  className="block h-full rounded-full bg-chart-1"
                  style={{ width: `${resolution * 100}%` }}
                />
              </span>
              <span className="shrink-0">tied to a definition</span>
            </span>
          ) : (
            "Tied to a definition"
          )
        }
      />
    </section>
  );
}

/** "+3 ~1 −0": added, changed, deleted, each in its change's colour and named
 * for screen readers. */
function Changes({ added, changed, deleted }: { added: number; changed: number; deleted: number }) {
  return (
    <span className="inline-flex gap-1.5">
      <span className="text-added" title="added">
        +{added}
        <span className="sr-only"> added</span>
      </span>
      <span className="text-warning" title="changed">
        ~{changed}
        <span className="sr-only"> changed</span>
      </span>
      <span className="text-deleted" title="deleted">
        −{deleted}
        <span className="sr-only"> deleted</span>
      </span>
    </span>
  );
}

function RunRow({ run }: { run: IndexRun }) {
  const files = run.stats.files;
  const nodes = run.stats.nodes;
  return (
    <tr className="align-top transition-colors hover:bg-muted/40">
      <td className="px-3 py-3 font-medium">#{run.id}</td>
      <td className="px-3 py-3 font-mono text-xs text-muted-foreground">
        {shortSha(run.commit_sha)}
      </td>
      <td className="px-3 py-3">
        <StatusBadge status={run.status} />
        {run.error && (
          <p className="mt-1 max-w-xs truncate text-xs text-critical" title={run.error}>
            {run.error}
          </p>
        )}
      </td>
      <td className="px-3 py-3 text-xs tabular-nums text-muted-foreground">
        {files ? (
          <>
            {formatCount(files.parsed)} parsed
            <br />
            <Changes added={files.new ?? 0} changed={files.changed ?? 0} deleted={files.deleted ?? 0} />
          </>
        ) : (
          "—"
        )}
      </td>
      <td className="px-3 py-3 text-xs tabular-nums">
        {nodes ? (
          <Changes added={nodes.inserted ?? 0} changed={nodes.updated ?? 0} deleted={nodes.deleted ?? 0} />
        ) : (
          "—"
        )}
      </td>
      <td className="px-3 py-3 tabular-nums text-muted-foreground">
        {formatDuration(run.started_at, run.finished_at) ?? "—"}
      </td>
      <td className="px-3 py-3 text-muted-foreground">
        <DateTime iso={run.started_at} />
      </td>
    </tr>
  );
}
