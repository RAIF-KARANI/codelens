import "server-only";

import { notFound } from "next/navigation";
import { connection } from "next/server";

import { config } from "@/lib/config";
import { currentUser } from "@/lib/dal";
import { apiToken } from "@/lib/tokens";

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

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  // API data is per request: never part of a prerendered shell. (The user
  // comes from a private cache, so without this Next.js would see the token's
  // timestamp as the first request-time value.)
  await connection();
  const user = await currentUser();
  const { apiUrl, apiJwtSecret } = config();
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${await apiToken(user, apiJwtSecret)}`);
  headers.set("Accept", "application/json");
  // Always fresh: statuses change while a page is open.
  const response = await fetch(`${apiUrl}/api/v1${path}`, { ...init, headers, cache: "no-store" });
  if (response.ok) {
    return (await response.json()) as T;
  }
  const error = await errorOf(response);
  if (response.status === 404) {
    // Missing and not-yours look the same on purpose (D111).
    notFound();
  }
  // Anything else is shown by the nearest error.tsx. A 401 here means the API
  // and the dashboard disagree about API_JWT_SECRET: logging in again
  // wouldn't help.
  throw error;
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

export const listRepos = () => api<Repo[]>("/repos");
