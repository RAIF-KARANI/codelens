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
