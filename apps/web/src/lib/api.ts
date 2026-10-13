import "server-only";

import { notFound } from "next/navigation";
import { connection } from "next/server";

import { config } from "@/lib/config";
import { currentUser } from "@/lib/dal";
import { withQuery } from "@/lib/params";
import { apiToken, type SessionUser } from "@/lib/tokens";

/** The CodeLens API, called from the dashboard's server only. Each call is
 * signed for the signed-in user with a fresh 5-minute token; the browser never
 * sees one. */

/** The API's error shape: {"error": {"code", "message", "details"}}. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

/** For pages: the signed-in user's view of `path`. A 404 becomes the page's
 * not-found; any other error goes to the nearest error.tsx. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  // API data is per request: never part of a prerendered shell. (The user
  // comes from a private cache, so without this Next.js would see the token's
  // timestamp as the first request-time value.)
  await connection();
  try {
    return await callApi<T>(await currentUser(), path, init);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      // Missing and not-yours look the same on purpose (D111).
      notFound();
    }
    // A 401 here means the API and the dashboard disagree about
    // API_JWT_SECRET: logging in again wouldn't help.
    throw error;
  }
}

/** One call as `user`; throws ApiError on any error status. Server Actions use
 * it directly, with the user they read from the session themselves. */
export async function callApi<T>(
  user: SessionUser,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const { apiUrl, apiJwtSecret } = config();
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${await apiToken(user, apiJwtSecret)}`);
  headers.set("Accept", "application/json");
  // Always fresh: statuses change while a page is open.
  const response = await fetch(`${apiUrl}/api/v1${path}`, { ...init, headers, cache: "no-store" });
  if (!response.ok) {
    throw await errorOf(response);
  }
  return (await response.json()) as T;
}

async function errorOf(response: Response): Promise<ApiError> {
  try {
    const { error } = (await response.json()) as { error: { code: string; message: string } };
    return new ApiError(response.status, error.code, error.message);
  } catch {
    return new ApiError(response.status, "http_error", `HTTP ${response.status}`);
  }
}

// The API's shapes (services/api/src/api/schemas.py), as the pages use them.

export type RepoSource = "github" | "local";
export type RepoStatus = "pending" | "indexing" | "ready" | "failed";
export type IndexRunStatus = "running" | "succeeded" | "failed";
export type ReviewSource = "github" | "cli" | "eval";
export type ReviewStatus = "queued" | "running" | "completed" | "failed" | "superseded";

export const REVIEW_STATUSES: readonly ReviewStatus[] = [
  "queued",
  "running",
  "completed",
  "failed",
  "superseded",
];

/** Keyset pagination: pass `next_cursor` back as `?cursor=`; null on the last
 * page. */
export type Page<T> = { items: T[]; next_cursor: number | null };

export type Repo = {
  id: number;
  full_name: string;
  source: RepoSource;
  default_branch: string;
  status: RepoStatus;
  last_indexed_sha: string | null;
  created_at: string;
  updated_at: string;
  last_review_at: string | null;
};

/** What the indexer reported (core/indexing: IndexReport.to_json). Only the
 * parts the pages show. */
export type IndexStats = {
  files?: { parsed?: number; new?: number; changed?: number; deleted?: number; unchanged?: number };
  nodes?: { inserted?: number; updated?: number; deleted?: number };
  calls?: { total?: number; resolution?: number };
  db_totals?: { nodes?: number; edges?: number };
  timing_s?: { parse?: number; write?: number };
};

export type IndexRun = {
  id: number;
  commit_sha: string | null;
  status: IndexRunStatus;
  started_at: string;
  finished_at: string | null;
  stats: IndexStats;
  error: string | null;
};

export type RepoDetail = Repo & { latest_index_run: IndexRun | null };

export type SeverityCounts = { critical: number; warning: number; suggestion: number };

export type ReviewSummary = {
  id: number;
  repo_id: number;
  source: ReviewSource;
  pr_number: number | null;
  base_sha: string | null;
  head_sha: string;
  status: ReviewStatus;
  model: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  github_review_id: number | null;
  counts: SeverityCounts;
};

export const listRepos = () => api<Repo[]>("/repos");

export const getRepo = (id: number) => api<RepoDetail>(`/repos/${id}`);

export const listIndexRuns = (id: number, cursor?: number) =>
  api<Page<IndexRun>>(withQuery(`/repos/${id}/index-runs`, { cursor }));

export const listReviews = (id: number, opts: { status?: ReviewStatus; cursor?: number }) =>
  api<Page<ReviewSummary>>(withQuery(`/repos/${id}/reviews`, opts));
