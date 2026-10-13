"use client";

/** Shown when a page fails, e.g. the API is down or refused the dashboard's
 * token. In production the message is replaced by a digest; the details are
 * in the server's log. */
export default function Error({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <div className="mx-auto mt-16 max-w-md text-center">
      <h1 className="text-xl font-semibold tracking-tight">Something went wrong</h1>
      <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">
        {error.message}
      </p>
      {error.digest && <p className="mt-1 font-mono text-xs text-zinc-500">{error.digest}</p>}
      <button
        type="button"
        onClick={() => retry()}
        className="mt-6 rounded-md border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
      >
        Try again
      </button>
    </div>
  );
}
