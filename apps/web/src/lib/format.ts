/** Display helpers shared by the pages. Pure, so they're unit-tested. */

const DATE_TIME = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" });

export function formatDateTime(iso: string): string {
  return DATE_TIME.format(new Date(iso));
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : "—";
}

/** "850 ms", "12 s", "3 min 05 s", "1 h 02 min"; null while unfinished. */
export function formatDuration(startIso: string | null, endIso: string | null): string | null {
  if (!startIso || !endIso) return null;
  return formatMs(new Date(endIso).getTime() - new Date(startIso).getTime());
}

/** The same, for a length of time in milliseconds; null if it isn't one. */
export function formatMs(ms: number): string | null {
  if (!Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${pad(seconds % 60)} s`;
  return `${Math.floor(minutes / 60)} h ${pad(minutes % 60)} min`;
}

export function formatPercent(fraction: number | undefined): string {
  return fraction === undefined ? "—" : `${Math.round(fraction * 100)}%`;
}

export function formatCount(n: number | undefined): string {
  return n === undefined ? "—" : n.toLocaleString("en");
}

/** A size as people read it: "0 B", "512 KB", "37.2 MB", "1.48 GB" (1024s). */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)} ${units[unit]}`;
}

const WEEKDAY = new Intl.DateTimeFormat("en", { weekday: "short", timeZone: "UTC" });
const DAY = new Intl.DateTimeFormat("en", {
  weekday: "short",
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

/** A UTC day from the API ("2026-10-16") as "Fri", and in full as "Fri, Oct 16".
 * In UTC, like the API's days, so the label never shifts with the reader's zone. */
export function weekday(day: string): string {
  return WEEKDAY.format(new Date(`${day}T00:00:00Z`));
}

export function dayTitle(day: string): string {
  return DAY.format(new Date(`${day}T00:00:00Z`));
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}
