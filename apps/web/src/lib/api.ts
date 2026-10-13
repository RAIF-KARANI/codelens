import "server-only";

import { notFound } from "next/navigation";
import { connection } from "next/server";

import { apiConfig } from "@/lib/config";
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
  const result = await apiOrMissing<T>(path, init);
  // Missing and not-yours look the same on purpose (D111).
  if (result === null) notFound();
  return result;
}

/** Like api(), but a 404 is null: for parts of a page that may be absent
 * (an older review has no stored diff). */
export async function apiOrMissing<T>(path: string, init: RequestInit = {}): Promise<T | null> {
  // API data is per request: never part of a prerendered shell. (The user
  // comes from a private cache, so without this Next.js would see the token's
  // timestamp as the first request-time value.)
  await connection();
  try {
    return await callApi<T>(await currentUser(), path, init);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
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
  const { apiUrl, apiJwtSecret } = apiConfig();
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
  /** The PR's title, or a manual review's branch; null for older reviews. */
  title: string | null;
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

export type Severity = "critical" | "warning" | "suggestion";
export type Vote = 1 | -1;
export type Votes = { up: number; down: number };

export type Finding = {
  id: number;
  file_path: string;
  line_start: number;
  line_end: number;
  severity: Severity;
  category: string;
  title: string;
  explanation: string;
  suggested_fix: string | null;
  /** The code nodes the model was shown as evidence for it. */
  evidence_node_ids: number[];
  confidence: number | null;
  status: "posted" | "dropped";
  /** Why validation dropped it (dropped findings only). */
  drop_reason: string | null;
  votes: Votes;
  my_vote: Vote | null;
};

export type ReviewDetail = ReviewSummary & {
  token_usage: Record<string, number> | null;
  pipeline_config: Record<string, unknown>;
  findings: Finding[];
};

export type Seed = {
  path: string;
  qualified_name: string;
  kind: string;
  change: "added" | "modified" | "deleted";
  start_line: number;
  end_line: number;
  signature_changed: boolean;
};

export type ReviewDiff = {
  review_id: number;
  base_sha: string | null;
  head_sha: string;
  diff: string;
  seeds: Seed[];
};

export type TraceItem = {
  label: string;
  node_id: number | null;
  qualified_name: string;
  included: boolean;
  truncated: boolean;
  token_cost: number;
  stage_scores: Record<string, unknown>;
};

export type ReviewTrace = {
  review_id: number;
  budgets: Record<string, number> | null;
  chunks: { index: number; items: TraceItem[] }[];
};

export type CodeNode = {
  id: number;
  repo_id: number;
  path: string;
  kind: string;
  name: string;
  qualified_name: string;
  signature: string | null;
  docstring: string | null;
  start_line: number;
  end_line: number;
  code: string;
};

export type FeedbackOut = { finding_id: number; votes: Votes; my_vote: Vote | null };

export const listRepos = () => api<Repo[]>("/repos");

export const getRepo = (id: number) => api<RepoDetail>(`/repos/${id}`);

export const listIndexRuns = (id: number, cursor?: number) =>
  api<Page<IndexRun>>(withQuery(`/repos/${id}/index-runs`, { cursor }));

export const listReviews = (id: number, opts: { status?: ReviewStatus; cursor?: number }) =>
  api<Page<ReviewSummary>>(withQuery(`/repos/${id}/reviews`, opts));

export const getReview = (id: number, opts: { dropped?: boolean } = {}) =>
  api<ReviewDetail>(withQuery(`/reviews/${id}`, { include_dropped: opts.dropped ? "true" : undefined }));

/** null for reviews from before the diff was stored (D110). */
export const getReviewDiff = (id: number) => apiOrMissing<ReviewDiff>(`/reviews/${id}/diff`);

export const getReviewTrace = (id: number) => api<ReviewTrace>(`/reviews/${id}/trace`);

export type GraphNode = {
  /** "n<node id>", or "seed:<qualified name>" for changed code not in the index yet. */
  id: string;
  node_id: number | null;
  label: string;
  qualified_name: string;
  kind: string;
  path: string | null;
  is_seed: boolean;
  change: Seed["change"] | null;
  /** 0 for changed code, 1 for its callers and callees, null for other context. */
  depth: number | null;
  /** Whether the model was shown it. */
  in_context: boolean;
};

export type GraphEdge = { source: string; target: string; kind: "calls" | "inherits" };

export type ReviewGraph = { review_id: number; nodes: GraphNode[]; edges: GraphEdge[] };

export const getReviewGraph = (id: number) => api<ReviewGraph>(`/reviews/${id}/graph`);
