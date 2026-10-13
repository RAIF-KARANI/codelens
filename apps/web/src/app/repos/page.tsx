import type { Metadata } from "next";
import { Suspense } from "react";

import { listRepos, type Repo, type RepoStatus } from "@/lib/api";

export const metadata: Metadata = { title: "Repositories" };

export default function ReposPage() {
  return (
    <>
      <h1 className="text-xl font-semibold tracking-tight">Repositories</h1>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        Repositories CodeLens has indexed that you can access.
      </p>
      <Suspense fallback={<TableSkeleton />}>
        <RepoTable />
      </Suspense>
    </>
  );
}

async function RepoTable() {
  const repos = await listRepos();
  if (repos.length === 0) {
    return (
      <div className="mt-8 rounded-lg border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
        No repositories yet. Install the CodeLens GitHub App on a repository, then sign in again.
      </div>
    );
  }
  return (
    <div className="mt-6 overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
      <table className="w-full text-left text-sm">
        <thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
          <tr>
            <th className="px-4 py-2 font-medium">Repository</th>
            <th className="px-4 py-2 font-medium">Status</th>
            <th className="px-4 py-2 font-medium">Indexed at</th>
            <th className="px-4 py-2 font-medium">Last review</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
          {repos.map((repo) => (
            <RepoRow key={repo.id} repo={repo} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RepoRow({ repo }: { repo: Repo }) {
  return (
    <tr>
      <td className="px-4 py-3">
        <span className="font-medium">{repo.full_name}</span>
        {repo.source === "local" && (
          <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
            local
          </span>
        )}
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={repo.status} />
      </td>
      <td className="px-4 py-3 font-mono text-xs text-zinc-600 dark:text-zinc-400">
        {repo.last_indexed_sha ? `${repo.default_branch} @ ${repo.last_indexed_sha.slice(0, 7)}` : "—"}
      </td>
      <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
        {repo.last_review_at ? <DateTime iso={repo.last_review_at} /> : "—"}
      </td>
    </tr>
  );
}

const STATUS_STYLES: Record<RepoStatus, string> = {
  pending: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  indexing: "bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  ready: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  failed: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
};

function StatusBadge({ status }: { status: RepoStatus }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status]}`}>
      {status}
    </span>
  );
}

const DATE_FORMAT = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" });

function DateTime({ iso }: { iso: string }) {
  return <time dateTime={iso}>{DATE_FORMAT.format(new Date(iso))}</time>;
}

function TableSkeleton() {
  return (
    <div className="mt-6 space-y-2" aria-busy="true" aria-label="Loading repositories">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-11 animate-pulse rounded-md bg-zinc-100 dark:bg-zinc-900" />
      ))}
    </div>
  );
}
