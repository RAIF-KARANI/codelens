import { notFound } from "next/navigation";
import { Suspense } from "react";

import { AutoRefresh } from "@/components/auto-refresh";
import { StatusBadge, Tag } from "@/components/badges";
import { Crumbs } from "@/components/crumbs";
import { getRepo } from "@/lib/api";
import { isDemo } from "@/lib/config";
import { shortSha } from "@/lib/format";
import { githubUrl } from "@/lib/github-links";
import { parseId } from "@/lib/params";
import { reindexBusy } from "@/lib/reindex";

import { reindex } from "./actions";
import { ReindexButton } from "./reindex-button";
import { RepoTabs } from "./tabs";

/** A repo's header and tabs (reviews, indexing), around each tab's page. */
export default function RepoLayout({ params, children }: LayoutProps<"/repos/[id]">) {
  return (
    <>
      {/* The id is only known per request, so the header (with its
          breadcrumbs) streams in. */}
      <Suspense fallback={<div className="h-28 animate-pulse rounded-lg bg-muted" />}>
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
  // A server component, rendered once per request: "now" is that request's.
  const busy = reindexBusy(repo);
  const link = repo.source === "github" ? githubUrl(repo.full_name) : null;

  return (
    <header>
      <Crumbs items={[{ label: "Repositories", href: "/repos" }, { label: repo.full_name }]} />
      <div className="mt-3 flex flex-wrap items-start gap-x-4 gap-y-3">
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
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {repo.default_branch} @ {shortSha(repo.last_indexed_sha)}
          </p>
        </div>
        {/* A demo can't index: nothing runs there but the pages. */}
        {!isDemo() && (
          <ReindexButton
            action={reindex.bind(null, repo.id)}
            busy={busy}
          />
        )}
      </div>
      <RepoTabs repoId={repo.id} />
      {/* While indexing, re-render every few seconds to show progress. */}
      <AutoRefresh active={busy !== null} />
    </header>
  );
}
