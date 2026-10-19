"use client";

import { RotateCcwIcon } from "lucide-react";
import { useActionState } from "react";

import { Button } from "@/components/ui/button";

import type { ReviewAgainState } from "./actions";

/** Under a failed pull request review: queue its commit again (D181). On
 * success the action opens the new review; a refusal stays here. */
export function ReviewAgainButton({
  action,
}: {
  action: (state: ReviewAgainState, form: FormData) => Promise<ReviewAgainState>;
}) {
  const [state, formAction, pending] = useActionState(action, null);
  return (
    <form action={formAction} className="flex flex-wrap items-center gap-2">
      <Button type="submit" variant="outline" size="sm" disabled={pending}>
        <RotateCcwIcon className={pending ? "animate-spin motion-reduce:animate-none" : ""} />
        {pending ? "Queuing…" : "Review again"}
      </Button>
      {state && !state.ok && (
        <p role="status" className="text-xs text-critical">
          {state.message}
        </p>
      )}
    </form>
  );
}
