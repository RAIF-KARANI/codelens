"use client";

import { describe, type Tone } from "@/lib/events";

import { useReviewEvents } from "./review-events";

const TONES: Record<Tone, string> = {
  info: "bg-muted-foreground/60",
  good: "bg-added",
  bad: "bg-critical",
};

/** The review's timeline in words, from the page's event stream
 * (review-events.tsx). */
export function LiveTimeline() {
  const { events, state, wasLive } = useReviewEvents();
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
