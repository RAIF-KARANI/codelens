"use client";

import { TriangleAlertIcon } from "lucide-react";
import Link from "next/link";

import { StateMessage } from "@/components/state-message";
import { Button } from "@/components/ui/button";

/** Shown when a page fails: most often the API is down, or refused the
 * dashboard's token. In production React replaces the message with a digest
 * (the details stay in the server's log), so the page says what usually
 * causes it rather than relying on the message. */
export default function Error({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <StateMessage
      className="mt-10"
      tone="error"
      icon={<TriangleAlertIcon />}
      title="Something went wrong"
      action={
        <div className="flex flex-wrap justify-center gap-2">
          <Button type="button" onClick={() => retry()}>
            Try again
          </Button>
          <Button asChild variant="outline">
            <Link href="/overview">Back to the overview</Link>
          </Button>
        </div>
      }
    >
      <span className="block">
        This page couldn&apos;t load. Usually the CodeLens API isn&apos;t running or can&apos;t be
        reached; <code className="font-mono text-xs">codelens doctor</code> says which.
      </span>
      {error.message && !error.digest && (
        <span className="mt-2 block font-mono text-xs">{error.message}</span>
      )}
      {error.digest && (
        <span className="mt-2 block font-mono text-xs">Error reference: {error.digest}</span>
      )}
    </StateMessage>
  );
}
