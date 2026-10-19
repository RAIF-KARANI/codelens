"use server";

import { redirect } from "next/navigation";

import { ApiError, callApi, type FeedbackOut, type Vote } from "@/lib/api";
import { requestUser } from "@/lib/session";

export type VoteResult = { ok: true; feedback: FeedbackOut } | { ok: false; message: string };

/** 👍 (1), 👎 (-1), or take the vote back (null). Like any Server Action, it
 * checks the session itself, and the API checks the finding's access. */
export async function vote(findingId: number, value: Vote | null): Promise<VoteResult> {
  const user = await requestUser();
  if (!user) redirect("/login");
  if (!Number.isSafeInteger(findingId) || findingId < 1 || ![1, -1, null].includes(value)) {
    return { ok: false, message: "Invalid vote." };
  }
  try {
    const feedback = await callApi<FeedbackOut>(
      user,
      `/findings/${findingId}/feedback`,
      value === null
        ? { method: "DELETE" }
        : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ vote: value }),
          },
    );
    return { ok: true, feedback };
  } catch (error) {
    if (error instanceof ApiError) {
      return {
        ok: false,
        message: error.code === "demo_mode" ? "Voting is off in the read-only demo." : error.message,
      };
    }
    throw error;
  }
}

export type ReviewAgainState = { ok: false; message: string } | null;

const AGAIN_MESSAGES: Record<string, string> = {
  demo_mode: "This is a read-only demo.",
  not_found: "This review doesn't exist, or you don't have access to it.",
  queue_unavailable: "The job queue is down; try again in a moment.",
};

/** Review a failed pull request review's commit again (D181), then open the
 * new review. The API checks the review's access, that it failed, and that
 * nothing newer reviews the pull request already. */
export async function reviewAgain(reviewId: number): Promise<ReviewAgainState> {
  const user = await requestUser();
  if (!user) redirect("/login");
  if (!Number.isSafeInteger(reviewId) || reviewId < 1) return { ok: false, message: AGAIN_MESSAGES.not_found };
  let queued: { review_id: number };
  try {
    queued = await callApi<{ review_id: number }>(user, `/reviews/${reviewId}/retry`, { method: "POST" });
  } catch (error) {
    if (error instanceof ApiError) return { ok: false, message: AGAIN_MESSAGES[error.code] ?? error.message };
    throw error;
  }
  redirect(`/reviews/${queued.review_id}`);
}
