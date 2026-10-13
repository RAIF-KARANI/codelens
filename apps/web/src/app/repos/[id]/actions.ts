"use server";

import { refresh } from "next/cache";
import { redirect } from "next/navigation";

import { ApiError, callApi } from "@/lib/api";
import { readSession } from "@/lib/session";

export type ReindexState = { ok: boolean; message: string } | null;

const MESSAGES: Record<string, string> = {
  demo_mode: "This is a read-only demo.",
  not_indexable: "This repository has no checkout or GitHub installation to index from.",
  queue_unavailable: "The job queue is down; try again in a moment.",
  not_found: "This repository doesn't exist, or you don't have access to it.",
};

/** Queue a re-index (then an embedding of what changed). A Server Action is a
 * public endpoint, so it reads the session itself and the API checks the
 * repo's access, as for any call (D111). useActionState also passes the
 * previous state and the form data; this action needs neither. */
export async function reindex(repoId: number): Promise<ReindexState> {
  const user = await readSession();
  if (!user) redirect("/login");
  if (!Number.isSafeInteger(repoId) || repoId < 1) {
    return { ok: false, message: MESSAGES.not_found };
  }
  try {
    await callApi(user, `/repos/${repoId}/index`, { method: "POST" });
  } catch (error) {
    if (error instanceof ApiError) {
      return { ok: false, message: MESSAGES[error.code] ?? error.message };
    }
    throw error;
  }
  // Re-render the page, so the header picks up the new status.
  refresh();
  return { ok: true, message: "Re-index queued." };
}
