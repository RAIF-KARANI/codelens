"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** While `active`, re-render the page from the server every `everyMs`, for at
 * most `forMs`. For statuses that change in the background (indexing, a
 * queued review); live review events get their own stream in step 4. Polls
 * only while the tab is visible. */
export function AutoRefresh({
  active,
  everyMs = 3_000,
  forMs = 10 * 60_000,
}: {
  active: boolean;
  everyMs?: number;
  forMs?: number;
}) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const until = Date.now() + forMs;
    const timer = setInterval(() => {
      if (Date.now() > until) {
        clearInterval(timer);
      } else if (document.visibilityState === "visible") {
        router.refresh();
      }
    }, everyMs);
    return () => clearInterval(timer);
  }, [active, everyMs, forMs, router]);
  return null;
}
