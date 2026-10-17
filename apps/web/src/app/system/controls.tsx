"use client";

import { FolderOpenIcon, RotateCwIcon, Trash2Icon } from "lucide-react";
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

import type { ActionState } from "./actions";

type Action = (state: ActionState, form: FormData) => Promise<ActionState>;

function useAnswer(action: Action, failed: string) {
  const [state, formAction, pending] = useActionState(action, null);
  useEffect(() => {
    if (!state) return;
    if (state.ok) toast.success(state.message);
    else toast.error(failed, { description: state.message });
  }, [state, failed]);
  return { formAction, pending };
}

export function RestartButton({ action, service }: { action: Action; service: string }) {
  const { formAction, pending } = useAnswer(action, `${service} wasn't restarted`);
  return (
    <form action={formAction}>
      <input type="hidden" name="service" value={service} />
      <Button type="submit" variant="ghost" size="sm" disabled={pending} aria-label={`Restart ${service}`}>
        <RotateCwIcon className={pending ? "animate-spin motion-reduce:animate-none" : ""} />
        {pending ? "Restarting…" : "Restart"}
      </Button>
    </form>
  );
}

export function OpenFolderButton({ action }: { action: Action }) {
  const { formAction, pending } = useAnswer(action, "The folder didn't open");
  return (
    <form action={formAction}>
      <Button type="submit" variant="outline" size="sm" disabled={pending}>
        <FolderOpenIcon />
        Open the data folder
      </Button>
    </form>
  );
}

/** Deleting the mirrors is safe but costs a full fetch of each repository
 * next time, so the page asks first. */
export function ClearMirrorsButton({ action, size }: { action: Action; size: string }) {
  const { formAction, pending } = useAnswer(action, "The mirrors weren't cleared");
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" disabled={pending}>
          <Trash2Icon />
          {pending ? "Clearing…" : "Clear the mirrors"}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Clear the repositories&apos; mirrors?</DialogTitle>
          <DialogDescription>
            {`This frees ${size}. Reviews, findings and the code graph stay. Each repository is copied from GitHub again, in full, the next time it's indexed or a pull request on it is reviewed. The workers pause while it's cleared.`}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancel</Button>
          </DialogClose>
          <form action={formAction} onSubmit={() => setOpen(false)}>
            <Button type="submit">Clear the mirrors</Button>
          </form>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
