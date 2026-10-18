import { SearchXIcon } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { StateMessage } from "@/components/state-message";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = { title: "Not found" };

/** An address that matches no page at all, outside the dashboard's shell
 * (its own not-found, in `(dashboard)/`, answers for a missing review or
 * repo). "/" is the demo's landing page, or forwards to the dashboard. */
export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-svh max-w-xl items-center px-4">
      <StateMessage
        icon={<SearchXIcon />}
        title="Not found"
        action={
          <Button asChild variant="outline">
            <Link href="/">Back to CodeLens</Link>
          </Button>
        }
      >
        There&apos;s no page at this address.
      </StateMessage>
    </main>
  );
}
