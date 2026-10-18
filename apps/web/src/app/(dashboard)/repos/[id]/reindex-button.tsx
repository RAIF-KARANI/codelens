"use client";

import { RefreshCwIcon } from "lucide-react";
import { useActionState, useEffect } from "react";
import { toast } from "sonner";

import { AutoRefresh } from "@/components/auto-refresh";
import { Button } from "@/components/ui/button";

import type { ReindexState } from "./actions";

/** Re-index the repo (then embed what changed). The outcome is a toast; a
 * refusal also stays under the button, so it can still be read once the toast
 * has gone. */
export function ReindexButton({
  action,
  busy,
}: {
  action: (state: ReindexState, form: FormData) => Promise<ReindexState>;
  /** What the repo is busy with ("Queued…", "Indexing…"), or null when it can
   * be re-indexed. */
  busy: string | null;
}) {
  const [state, formAction, pending] = useActionState(action, null);

  useEffect(() => {
    if (!state) return;
    if (state.ok) toast.success(state.message, { description: "The status changes when the worker picks it up." });
    else toast.error("Couldn't re-index", { description: state.message });
  }, [state]);

  return (
    <form action={formAction} className="flex flex-col items-end gap-1">
      <Button type="submit" variant="outline" disabled={busy !== null || pending}>
        <RefreshCwIcon
          className={pending || busy ? "animate-spin motion-reduce:animate-none" : ""}
        />
        {pending ? "Queuing…" : (busy ?? "Re-index")}
      </Button>
      {state && !state.ok && (
        <p role="status" className="max-w-xs text-right text-xs text-critical">
          {state.message}
        </p>
      )}
      {/* The worker picks the job up a moment later: watch for it for a while,
          until the header itself says it's indexing. */}
      <AutoRefresh active={!!state?.ok} forMs={60_000} />
    </form>
  );
}
