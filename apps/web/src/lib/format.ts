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
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
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

function pad(n: number): string {
  return String(n).padStart(2, "0");
}
