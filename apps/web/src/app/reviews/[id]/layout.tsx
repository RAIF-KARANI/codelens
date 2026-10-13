import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { Tabs } from "@/components/tabs";
import { getRepo, getReview } from "@/lib/api";
import { parseId } from "@/lib/params";

import { EvidenceProvider } from "./evidence";
import { ReviewHeader } from "./header";

/** A review's header and tabs (diff, graph, trace) around each tab's page.
 * The evidence drawer is shared, so every tab can open a node's code. */
export default function ReviewLayout({ params, children }: LayoutProps<"/reviews/[id]">) {
  return (
    <EvidenceProvider>
      {/* The id is only known per request, so the header streams in. */}
      <Suspense fallback={<div className="h-28 animate-pulse rounded-lg bg-zinc-100 dark:bg-zinc-900" />}>
        <Header params={params} />
      </Suspense>
      <div className="mt-6">{children}</div>
    </EvidenceProvider>
  );
}

async function Header({ params }: Pick<LayoutProps<"/reviews/[id]">, "params">) {
  const id = parseId((await params).id);
  if (id === null) notFound();
  const review = await getReview(id);
  const repo = await getRepo(review.repo_id);
  return (
    <>
      <Link
        href={`/repos/${repo.id}`}
        className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
      >
        ← {repo.full_name}
      </Link>
      <ReviewHeader review={review} repo={repo} />
      <Tabs
        tabs={[
          { href: `/reviews/${id}`, label: "Diff" },
          { href: `/reviews/${id}/graph`, label: "Graph" },
          { href: `/reviews/${id}/trace`, label: "Trace" },
        ]}
      />
    </>
  );
}
