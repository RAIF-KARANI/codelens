"use client";

import { CheckIcon, MinusIcon, XIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { clock, progress, type Step } from "@/lib/progress";
import { cn } from "@/lib/utils";

import { useReviewEvents } from "./review-events";

/** A running review's progress: where it is (changes → context → review chunk
 * i of n → validate → post), driven by the page's event stream, with the time
 * since it started. */
export function ProgressStepper({ startedAt, posts }: { startedAt: string; posts: boolean }) {
  const { events, state } = useReviewEvents();
  const { steps, ended } = progress(events, posts);
  const elapsed = useElapsed(startedAt, ended === null && state !== "lost");
  const current = steps.find((s) => s.state === "current" || s.state === "failed");

  return (
    <section aria-labelledby="progress-heading" className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="progress-heading" className="text-sm font-semibold">
          {ended === null ? "Reviewing…" : ended === "completed" ? "Review finished" : `Review ${ended}`}
        </h2>
        <span className="font-mono text-sm tabular-nums text-muted-foreground" aria-label="Elapsed time">
          {elapsed ?? "–:––"}
        </span>
      </div>
      <ol aria-label="Progress" className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(4rem,1fr))] gap-2">
        {steps.map((step, i) => (
          <StepView key={step.key} step={step} last={i === steps.length - 1} />
        ))}
      </ol>
      {/* Said once per change, not on every tick of the clock. */}
      <p className="sr-only" aria-live="polite">
        {current ? `${current.label}${current.detail ? `: ${current.detail}` : ""}` : ended ? `Review ${ended}` : ""}
      </p>
      {state === "lost" && (
        <p className="mt-3 text-xs text-critical">The connection was lost; reload the page to follow it again.</p>
      )}
    </section>
  );
}

const STATE_WORDS: Record<Step["state"], string> = {
  done: "done",
  current: "in progress",
  todo: "to do",
  failed: "failed",
  skipped: "skipped",
};

function StepView({ step, last }: { step: Step; last: boolean }) {
  return (
    <li aria-current={step.state === "current" ? "step" : undefined} className="relative flex flex-col items-center gap-1.5 text-center">
      {/* The line to the next step: filled once this one is done. */}
      {!last && (
        <span
          aria-hidden="true"
          className={cn(
            "absolute top-3 left-[calc(50%+0.875rem)] h-px w-[calc(100%-1.75rem+0.5rem)]",
            step.state === "done" ? "bg-primary" : "bg-border",
          )}
        />
      )}
      <span
        aria-hidden="true"
        className={cn(
          "relative flex size-6 items-center justify-center rounded-full border text-[11px] [&_svg]:size-3.5",
          step.state === "done" && "border-primary bg-primary text-primary-foreground",
          step.state === "current" && "border-primary bg-accent text-accent-foreground",
          step.state === "failed" && "border-critical bg-critical-soft text-critical",
          step.state === "skipped" && "border-dashed border-input text-muted-foreground",
          step.state === "todo" && "border-input text-muted-foreground",
        )}
      >
        {step.state === "done" && <CheckIcon />}
        {step.state === "failed" && <XIcon />}
        {step.state === "skipped" && <MinusIcon />}
        {step.state === "current" && <span className="size-2 animate-pulse rounded-full bg-primary motion-reduce:animate-none" />}
      </span>
      <span className={cn("text-xs font-medium", step.state === "todo" || step.state === "skipped" ? "text-muted-foreground" : "text-foreground")}>
        {step.label}
        <span className="sr-only">, {STATE_WORDS[step.state]}</span>
      </span>
      {step.detail && <span className="text-[11px] text-muted-foreground">{step.detail}</span>}
    </li>
  );
}

/** "m:ss" since `startIso`, ticking each second while `running`; null until
 * the first tick (the server can't know the time the page is read at). */
function useElapsed(startIso: string, running: boolean): string | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    if (!running) return;
    const tick = () => setNow(Date.now());
    const first = requestAnimationFrame(tick);
    const every = setInterval(tick, 1000);
    return () => {
      cancelAnimationFrame(first);
      clearInterval(every);
    };
  }, [running]);
  return now === null ? null : clock(now - new Date(startIso).getTime());
}
