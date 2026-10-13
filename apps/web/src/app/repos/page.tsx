import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { DateTime, StatusBadge, Tag } from "@/components/badges";
import { TableSkeleton } from "@/components/skeleton";
import { listRepos, type Repo } from "@/lib/api";
import { shortSha } from "@/lib/format";

export const metadata: Metadata = { title: "Repositories" };

export default function ReposPage() {
  return (
    <>
      <h1 className="text-xl font-semibold tracking-tight">Repositories</h1>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        Repositories CodeLens has indexed that you can access.
      </p>
      <Suspense fallback={<TableSkeleton label="Loading repositories" />}>
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
      <table className="w-full whitespace-nowrap text-left text-sm">
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
    <tr className="hover:bg-zinc-50 dark:hover:bg-zinc-900/50">
      <td className="px-4 py-3">
        <Link href={`/repos/${repo.id}`} className="font-medium hover:underline">
          {repo.full_name}
        </Link>
        {repo.source === "local" && (
          <span className="ml-2">
            <Tag>local</Tag>
          </span>
        )}
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={repo.status} />
      </td>
      <td className="px-4 py-3 font-mono text-xs text-zinc-600 dark:text-zinc-400">
        {repo.last_indexed_sha
          ? `${repo.default_branch} @ ${shortSha(repo.last_indexed_sha)}`
          : "—"}
      </td>
      <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
        {repo.last_review_at ? <DateTime iso={repo.last_review_at} /> : "—"}
      </td>
    </tr>
  );
}
