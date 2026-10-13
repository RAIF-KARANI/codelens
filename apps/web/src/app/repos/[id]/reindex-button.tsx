"use client";

import { useActionState } from "react";

import { AutoRefresh } from "@/components/auto-refresh";

import type { ReindexState } from "./actions";

export function ReindexButton({
  action,
  disabled,
}: {
  action: (state: ReindexState, form: FormData) => Promise<ReindexState>;
  disabled: boolean;
}) {
  const [state, formAction, pending] = useActionState(action, null);
  return (
    <form action={formAction} className="flex flex-col items-end gap-1">
      <button
        type="submit"
        disabled={disabled || pending}
        className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
      >
        {pending ? "Queuing…" : disabled ? "Indexing…" : "Re-index"}
      </button>
      {state && (
        <p
          role="status"
          className={`text-xs ${state.ok ? "text-zinc-500" : "text-red-700 dark:text-red-400"}`}
        >
          {state.message}
        </p>
      )}
      {/* The worker picks the job up a moment later: watch for it for a while,
          until the header itself says it's indexing. */}
      <AutoRefresh active={!!state?.ok} forMs={60_000} />
    </form>
  );
}
