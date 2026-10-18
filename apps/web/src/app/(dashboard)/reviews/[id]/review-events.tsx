"use client";

import { useRouter } from "next/navigation";
import { createContext, use, useEffect, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { EVENT_TYPES, type ReviewEvent } from "@/lib/events";

export type StreamState = "connecting" | "open" | "ended" | "lost";

type ReviewEvents = { events: ReviewEvent[]; state: StreamState; wasLive: boolean };

const EventsContext = createContext<ReviewEvents>({ events: [], state: "connecting", wasLive: false });

/** One event stream per review page (the API's, through
 * /api/reviews/[id]/events), shared by the timeline and the progress stepper.
 * A finished review replays its events and ends at once; a running one
 * streams them live, and at `end` a toast says how it went and the page
 * re-renders from the server to show the findings. */
export function ReviewEventsProvider({
  reviewId,
  live,
  children,
}: {
  reviewId: number;
  live: boolean;
  children: ReactNode;
}) {
  const router = useRouter();
  // Whether it was running when the page loaded. The refresh at `end` turns
  // the prop false; reconnecting then would replay every event again.
  const [wasLive] = useState(live);
  const [events, setEvents] = useState<ReviewEvent[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  // Across subscriptions: what's been seen, and whether the end was announced.
  // A stream opened again (a reconnect, a remount) replays from the start, and
  // must neither repeat events (seen live on a real PR: the first three twice)
  // nor toast and refresh twice.
  const seen = useRef(new Set<string>());
  const posted = useRef<number | null>(null);
  const announced = useRef(false);

  useEffect(() => {
    const source = new EventSource(`/api/reviews/${reviewId}/events`);
    const onEvent = (type: string) => (message: MessageEvent<string>) => {
      const id = message.lastEventId || null;
      if (id !== null) {
        if (seen.current.has(id)) return;
        seen.current.add(id);
      }
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(message.data);
      } catch {
        // A malformed payload still shows as its event name.
      }
      if (type === "done" && typeof data.posted === "number") posted.current = data.posted;
      setEvents((all) => [...all, { id, type, data }]);
      if (type === "end") {
        // The server closes the stream after `end`; without close() the
        // EventSource would reconnect and replay everything.
        source.close();
        setState("ended");
        if (wasLive && !announced.current) {
          announced.current = true;
          announce(reviewId, String(data.status ?? ""), posted.current);
          router.refresh();
        }
      }
    };
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, onEvent(type) as EventListener);
    }
    source.onopen = () => setState("open");
    source.onerror = () => {
      // The browser retries on its own while the state is CONNECTING.
      if (source.readyState === EventSource.CLOSED) setState("lost");
    };
    return () => source.close();
  }, [reviewId, wasLive, router]);

  return <EventsContext value={{ events, state, wasLive }}>{children}</EventsContext>;
}

export function useReviewEvents(): ReviewEvents {
  return use(EventsContext);
}

/** The toast when a review you watched finishes. */
function announce(reviewId: number, status: string, posted: number | null) {
  const found = posted === null ? "" : `: ${posted} ${posted === 1 ? "finding" : "findings"} posted`;
  if (status === "completed") toast.success(`Review #${reviewId} finished${found}`);
  else if (status === "superseded") toast.info(`Review #${reviewId} was superseded by a newer push`);
  else toast.error(`Review #${reviewId} failed`, { description: "The timeline says where." });
}
