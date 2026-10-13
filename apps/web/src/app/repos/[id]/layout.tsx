import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { AutoRefresh } from "@/components/auto-refresh";
import { StatusBadge, Tag } from "@/components/badges";
import { getRepo } from "@/lib/api";
import { isDemo } from "@/lib/config";
import { shortSha } from "@/lib/format";
import { githubUrl } from "@/lib/github-links";
import { parseId } from "@/lib/params";

import { reindex } from "./actions";
import { ReindexButton } from "./reindex-button";
import { RepoTabs } from "./tabs";

/** A repo's header and tabs (reviews, indexing), around each tab's page. */
export default function RepoLayout({ params, children }: LayoutProps<"/repos/[id]">) {
  return (
    <>
      <Link
        href="/repos"
        className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
      >
        ← Repositories
      </Link>
      {/* The id is only known per request, so the header streams in. */}
      <Suspense fallback={<div className="mt-3 h-24 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />}>
        <RepoHeader params={params} />
      </Suspense>
      <div className="mt-6">{children}</div>
    </>
  );
}

async function RepoHeader({ params }: Pick<LayoutProps<"/repos/[id]">, "params">) {
  const id = parseId((await params).id);
  if (id === null) notFound();
  const repo = await getRepo(id);
  const busy = repo.status === "pending" || repo.status === "indexing";
  const link = repo.source === "github" ? githubUrl(repo.full_name) : null;

  return (
    <header className="mt-3">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
        <div className="min-w-0 flex-1">
          <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold tracking-tight">
            {link ? (
              <a href={link} className="hover:underline" target="_blank" rel="noreferrer">
                {repo.full_name}
              </a>
            ) : (
              repo.full_name
            )}
            <StatusBadge status={repo.status} />
            {repo.source === "local" && <Tag>local</Tag>}
          </h1>
          <p className="mt-1 font-mono text-xs text-zinc-600 dark:text-zinc-400">
            {repo.default_branch} @ {shortSha(repo.last_indexed_sha)}
          </p>
        </div>
        {/* A demo can't index: nothing runs there but the pages. */}
        {!isDemo() && <ReindexButton action={reindex.bind(null, repo.id)} disabled={busy} />}
      </div>
      <RepoTabs repoId={repo.id} />
      {/* While indexing, re-render every few seconds to show progress. */}
      <AutoRefresh active={busy} />
    </header>
  );
}
