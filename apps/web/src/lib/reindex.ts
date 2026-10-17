import type { Repo } from "@/lib/api";

/** How long a re-index's claim on a repo holds: after this the API lets it
 * be taken again (services/api routes/repos.py, STALE_CLAIM). */
export const STALE_CLAIM_MS = 60 * 60 * 1000;

/** What the repo is busy with, for the Re-index button ("Queued…",
 * "Indexing…"), or null when it can be re-indexed. A claim older than
 * STALE_CLAIM_MS is a job that was lost (a worker killed mid-run): the API
 * would take it again, so the button works again too (D163). */
export function reindexBusy(
  repo: Pick<Repo, "status" | "updated_at">,
  now: number = Date.now(),
): string | null {
  if (repo.status !== "pending" && repo.status !== "indexing") return null;
  if (now - Date.parse(repo.updated_at) > STALE_CLAIM_MS) return null;
  return repo.status === "pending" ? "Queued…" : "Indexing…";
}
