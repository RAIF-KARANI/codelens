import Link from "next/link";

import { REVIEW_STATUSES, type ReviewStatus } from "@/lib/api";
import { withQuery } from "@/lib/params";
import { cn } from "@/lib/utils";

/** Filter a review list by status: links, so a filter is a URL that can be
 * shared and survives a reload. `query` holds the other parameters to keep. */
export function StatusFilter({
  pathname,
  current,
  query = {},
}: {
  pathname: string;
  current?: ReviewStatus;
  query?: Record<string, string | undefined>;
}) {
  const choices: (ReviewStatus | undefined)[] = [undefined, ...REVIEW_STATUSES];
  return (
    <nav aria-label="Filter by status" className="flex flex-wrap gap-1.5 text-xs">
      {choices.map((status) => {
        const selected = status === current;
        return (
          <Link
            key={status ?? "all"}
            href={withQuery(pathname, { ...query, status })}
            aria-current={selected ? "true" : undefined}
            className={cn(
              "rounded-full border px-2.5 py-1 transition-colors",
              selected
                ? "border-primary bg-primary text-primary-foreground"
                : "border-input text-muted-foreground hover:border-primary/60 hover:text-foreground",
            )}
          >
            {status ?? "all"}
          </Link>
        );
      })}
    </nav>
  );
}
