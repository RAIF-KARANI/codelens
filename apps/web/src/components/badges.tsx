import type {
  IndexRunStatus,
  RepoStatus,
  ReviewStatus,
  SeverityCounts,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";

type Status = RepoStatus | IndexRunStatus | ReviewStatus;

const NEUTRAL = "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300";
const BUSY = "bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300";
const GOOD = "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300";
const BAD = "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300";

const STATUS_STYLES: Record<Status, string> = {
  pending: NEUTRAL,
  queued: NEUTRAL,
  superseded: NEUTRAL,
  indexing: BUSY,
  running: BUSY,
  ready: GOOD,
  succeeded: GOOD,
  completed: GOOD,
  failed: BAD,
};

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status]}`}
    >
      {(status === "running" || status === "indexing") && (
        <span className="size-1.5 animate-pulse rounded-full bg-current" aria-hidden="true" />
      )}
      {status}
    </span>
  );
}

export function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
      {children}
    </span>
  );
}

export function DateTime({ iso }: { iso: string }) {
  return <time dateTime={iso}>{formatDateTime(iso)}</time>;
}

const SEVERITIES = [
  { key: "critical", label: "critical", style: "text-red-700 dark:text-red-400" },
  { key: "warning", label: "warning", style: "text-amber-700 dark:text-amber-400" },
  { key: "suggestion", label: "suggestion", style: "text-sky-700 dark:text-sky-400" },
] as const;

/** Posted findings by severity, e.g. "1 critical · 2 warning"; "none" when 0. */
export function SeverityCountsView({ counts }: { counts: SeverityCounts }) {
  const shown = SEVERITIES.filter(({ key }) => counts[key] > 0);
  if (shown.length === 0) {
    return <span className="text-zinc-500">none</span>;
  }
  return (
    <span className="flex gap-x-2">
      {shown.map(({ key, label, style }) => (
        <span key={key} className={style}>
          {counts[key]} {label}
        </span>
      ))}
    </span>
  );
}
