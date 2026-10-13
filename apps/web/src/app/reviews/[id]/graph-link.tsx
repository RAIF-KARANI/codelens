"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

/** "Show in graph" on a finding: the Graph tab, highlighting the finding's
 * changed code and evidence. The review id comes from the URL. */
export function GraphLink({ findingId }: { findingId: number }) {
  const { id } = useParams<{ id: string }>();
  return (
    <Link
      href={`/reviews/${id}/graph?finding=${findingId}`}
      className="text-xs text-zinc-500 underline underline-offset-4 hover:text-zinc-900 dark:hover:text-zinc-100"
    >
      Show in graph
    </Link>
  );
}
