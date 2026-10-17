import { FolderGit2Icon } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { DateTime, StatusBadge, Tag } from "@/components/badges";
import { TableSkeleton } from "@/components/skeleton";
import { StateMessage } from "@/components/state-message";
import { Button } from "@/components/ui/button";
import { listRepos, type Repo } from "@/lib/api";
import { isDemo } from "@/lib/config";
import { shortSha } from "@/lib/format";

export const metadata: Metadata = { title: "Repositories" };

export default function ReposPage() {
  return (
    <>
      <h1 className="text-xl font-semibold tracking-tight">Repositories</h1>
      <p className="mt-1 text-sm text-muted-foreground">
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
      <StateMessage
        className="mt-8"
        icon={<FolderGit2Icon />}
        title={isDemo() ? "No repositories loaded" : "No repositories yet"}
        action={
          isDemo() ? undefined : (
            <Button asChild variant="outline">
              <a href="https://github.com/RAIF-KARANI/codelens/blob/main/docs/run-your-own.md">How to install it</a>
            </Button>
          )
        }
      >
        {isDemo()
          ? "The demo has no repositories loaded yet."
          : "Install your CodeLens GitHub App on a repository, then sign in again: CodeLens indexes it and reviews every push to its pull requests."}
      </StateMessage>
    );
  }
  return (
    <div className="mt-6 overflow-x-auto rounded-lg border border-border">
      <table className="w-full whitespace-nowrap text-left text-sm">
        <thead className="bg-muted text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-4 py-2 font-medium">Repository</th>
            <th className="px-4 py-2 font-medium">Status</th>
            <th className="px-4 py-2 font-medium">Indexed at</th>
            <th className="px-4 py-2 font-medium">Last review</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
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
    <tr className="hover:bg-muted/60">
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
      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
        {repo.last_indexed_sha
          ? `${repo.default_branch} @ ${shortSha(repo.last_indexed_sha)}`
          : "—"}
      </td>
      <td className="px-4 py-3 text-muted-foreground">
        {repo.last_review_at ? <DateTime iso={repo.last_review_at} /> : "—"}
      </td>
    </tr>
  );
}
