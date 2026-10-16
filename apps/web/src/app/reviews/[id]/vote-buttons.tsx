"use client";

import { useState, useTransition } from "react";

import type { Vote, Votes } from "@/lib/api";

import { vote } from "./actions";

/** 👍/👎 on a finding. The counts change at once (optimistically); the API's
 * answer then replaces them, or the old ones come back with a message. */
export function VoteButtons({
  findingId,
  votes,
  myVote,
}: {
  findingId: number;
  votes: Votes;
  myVote: Vote | null;
}) {
  const [state, setState] = useState({ votes, myVote });
  const [message, setMessage] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const press = (value: Vote) => {
    const next = state.myVote === value ? null : value; // pressing again takes it back
    const before = state;
    setState({ votes: tally(state.votes, state.myVote, next), myVote: next });
    setMessage(null);
    startTransition(async () => {
      const result = await vote(findingId, next);
      if (result.ok) {
        setState({ votes: result.feedback.votes, myVote: result.feedback.my_vote });
      } else {
        setState(before);
        setMessage(result.message);
      }
    });
  };

  const style = (on: boolean) =>
    `inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs tabular-nums ${
      on
        ? "border-primary bg-primary text-primary-foreground"
        : "border-input text-muted-foreground hover:border-primary/60 hover:text-foreground"
    }`;
  return (
    <div className="flex items-center gap-1.5" aria-busy={pending}>
      <button
        type="button"
        onClick={() => press(1)}
        aria-pressed={state.myVote === 1}
        aria-label="Helpful"
        className={style(state.myVote === 1)}
      >
        👍 {state.votes.up}
      </button>
      <button
        type="button"
        onClick={() => press(-1)}
        aria-pressed={state.myVote === -1}
        aria-label="Not helpful"
        className={style(state.myVote === -1)}
      >
        👎 {state.votes.down}
      </button>
      {message && <span className="text-xs text-critical">{message}</span>}
    </div>
  );
}

/** The counts after changing one person's vote from `from` to `to`. */
function tally(votes: Votes, from: Vote | null, to: Vote | null): Votes {
  const next = { ...votes };
  if (from === 1) next.up--;
  if (from === -1) next.down--;
  if (to === 1) next.up++;
  if (to === -1) next.down++;
  return next;
}
