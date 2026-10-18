"use client";

import { useActionState, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

import type { UpdateView } from "@/lib/control";

import type { SaveState } from "./actions";

type Action = (state: SaveState, form: FormData) => Promise<SaveState>;

/** A toast for the outcome; refusals also stay by their field. */
function useSaved(action: Action) {
  const [state, formAction, pending] = useActionState(action, null);
  useEffect(() => {
    if (!state) return;
    if (state.ok) toast.success(state.message);
    else toast.error("Not saved", { description: state.message });
  }, [state]);
  return { state, formAction, pending };
}

let answers = 0;
const keys = new WeakMap<object, number>();

/** A key that changes with every answer of the app: the form mounts again
 * with what was typed (after a refusal) or what was saved, not the empty
 * fields React leaves once a form's action has run. */
function formKey(state: SaveState): string {
  if (!state) return "initial";
  if (!keys.has(state)) keys.set(state, ++answers);
  return `answer-${keys.get(state)}`;
}

function FieldError({ state, name }: { state: SaveState; name: string }) {
  const message = state?.errors?.[name];
  return message ? (
    <p id={`${name}-error`} role="status" className="text-xs text-critical">
      {message}
    </p>
  ) : null;
}

function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {children}
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

export function AppForm({ action, accounts, reviewsPerHour }: { action: Action; accounts: string[]; reviewsPerHour: number }) {
  const { state, formAction, pending } = useSaved(action);
  return (
    <form key={formKey(state)} action={formAction} className="flex flex-col gap-4">
      <Field id="allowedAccounts" label="Accounts it works for" hint="GitHub logins, or login:id to pin one to its account. Installations by anyone else are ignored; none at all means nobody.">
        <Textarea
          id="allowedAccounts"
          name="allowedAccounts"
          defaultValue={state?.values?.allowedAccounts ?? accounts.join("\n")}
          rows={3}
          aria-invalid={!!state?.errors?.allowedAccounts}
          aria-describedby="allowedAccounts-error"
        />
        <FieldError state={state} name="allowedAccounts" />
      </Field>
      <Field id="reviewsPerHour" label="Reviews per hour, per account" hint="0 means no limit.">
        <Input
          id="reviewsPerHour"
          name="reviewsPerHour"
          type="number"
          min={0}
          max={1000}
          defaultValue={state?.values?.reviewsPerHour ?? reviewsPerHour}
          className="max-w-32"
          aria-invalid={!!state?.errors?.reviewsPerHour}
          aria-describedby="reviewsPerHour-error"
        />
        <FieldError state={state} name="reviewsPerHour" />
      </Field>
      <Button type="submit" className="self-start" disabled={pending}>
        {pending ? "Saving and restarting…" : "Save"}
      </Button>
    </form>
  );
}

export function EventsForm({ action, pollIntervalS }: { action: Action; pollIntervalS: number }) {
  const { state, formAction, pending } = useSaved(action);
  return (
    <form key={formKey(state)} action={formAction} className="flex flex-col gap-4">
      <Field id="pollIntervalS" label="Look at GitHub every (seconds)" hint="A new push is reviewed within this long. Each look costs almost nothing when nothing changed.">
        <Input
          id="pollIntervalS"
          name="pollIntervalS"
          type="number"
          min={10}
          max={3600}
          defaultValue={state?.values?.pollIntervalS ?? pollIntervalS}
          className="max-w-32"
          aria-invalid={!!state?.errors?.pollIntervalS}
          aria-describedby="pollIntervalS-error"
        />
        <FieldError state={state} name="pollIntervalS" />
      </Field>
      <Button type="submit" className="self-start" disabled={pending}>
        {pending ? "Saving and restarting…" : "Save"}
      </Button>
    </form>
  );
}

/** 8k works with any Ollama; 16k first changes two of the user's Ollama
 * settings and restarts it, so the page asks before it does (D146). */
export function WindowForm({ action, contextWindow, ollamaReady }: { action: Action; contextWindow: number; ollamaReady: boolean }) {
  const { formAction, pending } = useSaved(action);
  const [open, setOpen] = useState(false);
  const long = contextWindow >= 16384;
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm">
        Reviews see <b>{long ? "16k" : "8k"}</b> tokens at a time.{" "}
        <span className="text-muted-foreground">
          {long
            ? "More room for related code; CodeLens's measured results are at 8k."
            : "What CodeLens's results were measured at; 16k gives the model more related code."}
        </span>
      </p>
      {long ? (
        <form action={formAction}>
          <input type="hidden" name="window" value="short" />
          <Button type="submit" variant="outline" size="sm" disabled={pending}>
            {pending ? "Restarting…" : "Back to 8k"}
          </Button>
        </form>
      ) : (
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button variant="outline" size="sm" className="self-start" disabled={pending}>
              {pending ? "Setting up…" : "Use a 16k window…"}
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Use a 16k window?</DialogTitle>
              <DialogDescription>
                {ollamaReady
                  ? "Your Ollama is already set up for it. CodeLens restarts its services with the new window."
                  : "On an 8 GB GPU it fits only with two settings of your Ollama: OLLAMA_FLASH_ATTENTION=1 and OLLAMA_KV_CACHE_TYPE=q8_0. CodeLens sets them for your Windows account and restarts Ollama; they apply to anything else using it too."}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline">Cancel</Button>
              </DialogClose>
              <form action={formAction} onSubmit={() => setOpen(false)}>
                <input type="hidden" name="window" value="long" />
                <Button type="submit">{ollamaReady ? "Use 16k" : "Set up Ollama and use 16k"}</Button>
              </form>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}

const WHAT_THE_LOOK_FOUND: Record<UpdateView["status"], (u: UpdateView) => string> = {
  idle: () => "Not looked yet since CodeLens started.",
  checking: () => "Looking on GitHub…",
  "up-to-date": () => "This is the latest release.",
  available: (u) => `CodeLens ${u.available} is available.`,
  downloading: (u) => `Downloading ${u.available}: ${u.progress ?? 0}%.`,
  ready: (u) => `${u.available} is downloaded. CodeLens asks in its window before it restarts.`,
  error: (u) => `The last look didn't work: ${u.error}.`,
};

/** This version, what the last look found with its one next step, and the
 * switch for looking by itself (also in the tray). Nothing installs without
 * the user's yes, asked again by the app before it restarts. */
export function UpdatesForm({
  update,
  save,
  updates,
  releaseUrl,
}: {
  update: Action;
  save: Action;
  updates: UpdateView;
  releaseUrl: string | null;
}) {
  const now = useSaved(update);
  const saved = useSaved(save);
  const checked = saved.state?.values?.checkForUpdates
    ? saved.state.values.checkForUpdates === "on"
    : updates.checkForUpdates;
  const busy = now.pending || updates.status === "checking" || updates.status === "downloading";
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm" role="status">
        {WHAT_THE_LOOK_FOUND[updates.status](updates)}
        {updates.lastChecked && updates.status === "up-to-date" && (
          <span className="text-muted-foreground"> Looked at {new Date(updates.lastChecked).toLocaleString()}.</span>
        )}
      </p>
      <div className="flex flex-wrap gap-2">
        <form action={now.formAction}>
          <input type="hidden" name="do" value={updates.status === "available" || updates.status === "ready" ? "install" : "check"} />
          <Button type="submit" size="sm" variant={updates.status === "available" ? "default" : "outline"} disabled={busy}>
            {updates.status === "available"
              ? `Update to ${updates.available}`
              : updates.status === "ready"
                ? "Restart to update…"
                : now.pending
                  ? "Looking…"
                  : "Check for updates now"}
          </Button>
        </form>
        {releaseUrl && (
          <Button asChild variant="outline" size="sm">
            <a href={releaseUrl}>What&apos;s new</a>
          </Button>
        )}
      </div>
      <form key={formKey(saved.state)} action={saved.formAction} className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            name="checkForUpdates"
            defaultChecked={checked}
            className="size-4 accent-primary"
          />
          Look for updates by itself, on start and daily
        </label>
        <Button type="submit" variant="outline" size="sm" disabled={saved.pending}>
          {saved.pending ? "Saving…" : "Save"}
        </Button>
      </form>
    </div>
  );
}
