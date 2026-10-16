"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { describe, EVENT_TYPES, type ReviewEvent, type Tone } from "@/lib/events";

const TONES: Record<Tone, string> = {
  info: "bg-muted-foreground/60",
  good: "bg-added",
  bad: "bg-critical",
};

/** The review's timeline, from the API's event stream (through
 * /api/reviews/[id]/events). A finished review replays its events and ends at
 * once; a running one streams them live, and when it ends the page re-renders
 * from the server to show the findings. */
export function LiveTimeline({ reviewId, live }: { reviewId: number; live: boolean }) {
  const router = useRouter();
  // Whether it was running when the page loaded. The refresh at `end` turns
  // the prop false; reconnecting then would replay every event again.
  const [wasLive] = useState(live);
  const [events, setEvents] = useState<ReviewEvent[]>([]);
  const [state, setState] = useState<"connecting" | "open" | "ended" | "lost">("connecting");

  useEffect(() => {
    const source = new EventSource(`/api/reviews/${reviewId}/events`);
    const seen = new Set<string>();
    const onEvent = (type: string) => (message: MessageEvent<string>) => {
      // After a reconnect the API replays from Last-Event-ID; skip any repeat.
      if (message.lastEventId) {
        if (seen.has(message.lastEventId)) return;
        seen.add(message.lastEventId);
      }
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(message.data);
      } catch {
        // A malformed payload still shows as its event name.
      }
      setEvents((all) => [...all, { id: message.lastEventId || null, type, data }]);
      if (type === "end") {
        // The server closes the stream after `end`; without close() the
        // EventSource would reconnect and replay everything.
        source.close();
        setState("ended");
        if (wasLive) router.refresh();
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

  return (
    <section aria-label="Timeline" className="rounded-lg border border-border p-4">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        Timeline
        {state !== "ended" && (
          <span className="text-xs font-normal text-muted-foreground">
            {state === "lost" ? "connection lost; reload to retry" : wasLive ? "live" : "loading…"}
          </span>
        )}
      </h2>
      {events.length === 0 && state === "ended" && (
        <p className="mt-2 text-sm text-muted-foreground">No events were recorded for this review.</p>
      )}
      <ol className="mt-3 space-y-2" aria-live="polite">
        {events.map((event, i) => {
          const { text, tone } = describe(event);
          return (
            <li key={event.id ?? `${event.type}-${i}`} className="flex items-start gap-2 text-sm">
              <span className={`mt-1.5 size-2 shrink-0 rounded-full ${TONES[tone]}`} aria-hidden="true" />
              <span className="text-foreground/85">{text}</span>
            </li>
          );
        })}
        {wasLive && state !== "ended" && (
          <li className="flex items-center gap-2 text-sm text-muted-foreground">
            <span className="size-2 animate-pulse rounded-full bg-primary" aria-hidden="true" />
            waiting for the next step…
          </li>
        )}
      </ol>
    </section>
  );
}
