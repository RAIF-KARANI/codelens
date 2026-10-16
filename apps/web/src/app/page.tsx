import {
  ActivityIcon,
  FolderGit2Icon,
  GitPullRequestIcon,
  ShieldAlertIcon,
  TimerIcon,
} from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { AutoRefresh } from "@/components/auto-refresh";
import { DailyChart } from "@/components/daily-chart";
import { ReviewTable } from "@/components/review-table";
import { TableSkeleton } from "@/components/skeleton";
import { StatTile } from "@/components/stat-tile";
import { Skeleton } from "@/components/ui/skeleton";
import { getStats, listAllReviews, type SeverityCounts, type Stats } from "@/lib/api";
import { isDemo } from "@/lib/config";
import { dayTitle, formatCount, formatMs } from "@/lib/format";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Overview" };

const DAYS = 7;

/** The home page: the last week across the repos you can see, what is running
 * now, and the latest reviews. Eval runs aren't counted (D155). */
export default function OverviewPage() {
  return (
    <>
      <h1 className="text-xl font-semibold tracking-tight">Overview</h1>
      <Suspense fallback={<SummarySkeleton />}>
        <Summary />
      </Suspense>
      <Suspense fallback={<TableSkeleton label="Loading recent reviews" rows={4} />}>
        <RecentReviews />
      </Suspense>
    </>
  );
}

async function Summary() {
  // The demo's reviews were made once and copied (D157): its week ends on the
  // newest review, or the overview would be empty a week after publishing.
  const stats = await getStats(DAYS, isDemo() ? "latest" : "today");
  if (stats.repos.total === 0) return <NoRepos />;
  const findings = stats.findings.critical + stats.findings.warning + stats.findings.suggestion;
  return (
    <>
      <p className="mt-1 text-sm text-muted-foreground">
        {stats.ends_today
          ? `The last ${DAYS} days across the repositories you can see.`
          : `The ${DAYS} days up to the latest review, ${dayTitle(stats.ends_on)}.`}
      </p>
      {stats.in_progress > 0 && <InProgress count={stats.in_progress} />}
      <section aria-label={`The last ${DAYS} days`} className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Reviews"
          icon={<GitPullRequestIcon />}
          value={formatCount(stats.reviews.total)}
          detail={reviewDetail(stats)}
        />
        <StatTile
          label="Findings posted"
          icon={<ShieldAlertIcon />}
          value={formatCount(findings)}
          detail={<SeverityBreakdown counts={stats.findings} />}
        />
        <StatTile
          label="Median time to finish"
          icon={<TimerIcon />}
          value={stats.median_seconds !== null ? formatMs(stats.median_seconds * 1000) : "—"}
          detail="From queued to finished, for completed reviews"
        />
        <StatTile
          label="Repositories ready"
          icon={<FolderGit2Icon />}
          value={`${stats.repos.ready} of ${stats.repos.total}`}
          detail={repoDetail(stats)}
        />
      </section>
      <div className="mt-4">
        <DailyChart daily={stats.daily} endsToday={stats.ends_today} />
      </div>
      {/* While something runs, the numbers change: re-render every few seconds. */}
      <AutoRefresh active={stats.in_progress > 0 || stats.repos.indexing + stats.repos.pending > 0} />
    </>
  );
}

function reviewDetail(stats: Stats): string {
  const { completed, failed, superseded } = stats.reviews;
  const parts = [`${completed} completed`];
  if (failed) parts.push(`${failed} failed`);
  if (superseded) parts.push(`${superseded} superseded`);
  return parts.join(" · ");
}

function repoDetail(stats: Stats): string {
  const { indexing, pending, failed } = stats.repos;
  const parts = [];
  if (indexing + pending) parts.push(`${indexing + pending} indexing`);
  if (failed) parts.push(`${failed} failed to index`);
  return parts.length ? parts.join(" · ") : "Every repository is indexed";
}

const SEVERITIES = [
  { key: "critical", dot: "bg-critical" },
  { key: "warning", dot: "bg-warning" },
  { key: "suggestion", dot: "bg-suggestion" },
] as const;

/** The posted findings by severity: a coloured dot beside each, the words in
 * text colours, so identity is never colour alone. */
function SeverityBreakdown({ counts }: { counts: SeverityCounts }) {
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-0.5">
      {SEVERITIES.map(({ key, dot }) => (
        <li key={key} className="flex items-center gap-1.5">
          <span className={cn("size-1.5 rounded-full", dot)} aria-hidden="true" />
          <span className="tabular-nums text-foreground/85">{counts[key]}</span> {key}
        </li>
      ))}
    </ul>
  );
}

function InProgress({ count }: { count: number }) {
  return (
    <p
      role="status"
      className="mt-6 flex items-center gap-2 rounded-lg border border-primary/20 bg-accent px-3.5 py-2.5 text-sm text-accent-foreground"
    >
      <ActivityIcon className="size-4 animate-pulse" aria-hidden="true" />
      {count === 1 ? "1 review is" : `${count} reviews are`} queued or running now.
    </p>
  );
}

function NoRepos() {
  return (
    <div className="mt-8 flex flex-col items-center gap-3 rounded-xl border border-dashed border-input px-6 py-12 text-center">
      <span className="flex size-10 items-center justify-center rounded-lg bg-accent text-accent-foreground">
        <FolderGit2Icon className="size-5" aria-hidden="true" />
      </span>
      <h2 className="text-base font-semibold">No repositories yet</h2>
      <p className="max-w-md text-sm text-muted-foreground">
        {isDemo()
          ? "The demo has no repositories loaded yet."
          : "Install the CodeLens GitHub App on a repository. CodeLens indexes it, then reviews every push to its pull requests. Sign in again afterwards to see it here."}
      </p>
    </div>
  );
}

async function RecentReviews() {
  const page = await listAllReviews({ limit: 8 });
  return (
    <section aria-labelledby="recent-reviews" className="mt-8">
      <div className="flex items-baseline justify-between gap-4">
        <h2 id="recent-reviews" className="text-base font-semibold">
          Recent reviews
        </h2>
        {page.items.length > 0 && (
          <Link href="/reviews" className="text-sm text-muted-foreground hover:text-foreground">
            All reviews →
          </Link>
        )}
      </div>
      {page.items.length === 0 ? (
        <p className="mt-3 rounded-lg border border-dashed border-input p-6 text-center text-sm text-muted-foreground">
          No reviews yet. CodeLens reviews every push to an open pull request in an installed
          repository.
        </p>
      ) : (
        <div className="mt-3">
          <ReviewTable reviews={page.items} />
        </div>
      )}
    </section>
  );
}

function SummarySkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading the overview">
      <Skeleton className="mt-2 h-4 w-72" />
      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-28 rounded-xl" />
        ))}
      </div>
      <Skeleton className="mt-4 h-56 rounded-xl" />
    </div>
  );
}
