import { SearchXIcon } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { StateMessage } from "@/components/state-message";
import { Button } from "@/components/ui/button";
import { isDemo } from "@/lib/config";

export const metadata: Metadata = { title: "Not found" };

/** Also what a repo or review you can't access looks like (D111): the same
 * page, so a private repo's existence doesn't leak. */
export default function NotFound() {
  return (
    <StateMessage
      className="mt-10"
      icon={<SearchXIcon />}
      title="Not found"
      action={
        <Button asChild variant="outline">
          <Link href="/">Back to the overview</Link>
        </Button>
      }
    >
      {isDemo()
        ? "It doesn't exist, or it isn't part of the demo."
        : "It doesn't exist, or you don't have access to it. If it's a repository you just installed CodeLens on, sign in again: access is read at sign-in."}
    </StateMessage>
  );
}
