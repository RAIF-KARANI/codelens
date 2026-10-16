import type {
  IndexRunStatus,
  RepoStatus,
  ReviewStatus,
  SeverityCounts,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { formatDateTime } from "@/lib/format";

type Status = RepoStatus | IndexRunStatus | ReviewStatus;

// Waiting is grey, working is the brand's violet, done is green, failed is red.
const NEUTRAL = "bg-muted text-muted-foreground";
const BUSY = "bg-accent text-accent-foreground";
const GOOD = "bg-added-soft text-added";
const BAD = "bg-critical-soft text-critical";

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
    <Badge className={`gap-1.5 ${STATUS_STYLES[status]}`}>
      {(status === "running" || status === "indexing") && (
        <span className="size-1.5 animate-pulse rounded-full bg-current" aria-hidden="true" />
      )}
      {status}
    </Badge>
  );
}

export function Tag({ children }: { children: React.ReactNode }) {
  return (
    <Badge variant="secondary" className="rounded-md font-normal text-muted-foreground">
      {children}
    </Badge>
  );
}

export function DateTime({ iso }: { iso: string }) {
  return <time dateTime={iso}>{formatDateTime(iso)}</time>;
}

const SEVERITIES = [
  { key: "critical", label: "critical", style: "text-critical" },
  { key: "warning", label: "warning", style: "text-warning" },
  { key: "suggestion", label: "suggestion", style: "text-suggestion" },
] as const;

/** Posted findings by severity, e.g. "1 critical · 2 warning"; "none" when 0. */
export function SeverityCountsView({ counts }: { counts: SeverityCounts }) {
  const shown = SEVERITIES.filter(({ key }) => counts[key] > 0);
  if (shown.length === 0) {
    return <span className="text-muted-foreground">none</span>;
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
