"use server";

import { refresh } from "next/cache";

import { isLocal } from "@/lib/config";
import { control, ControlError, LOG_SERVICES } from "@/lib/control";

export type ActionState = { ok: boolean; message: string } | null;

/** In local mode only; the app checks every name and does the work (D176). */
async function ask(path: string, body: unknown, done: string): Promise<ActionState> {
  if (!isLocal()) return { ok: false, message: "The System page exists only in the CodeLens app." };
  try {
    await control(path, { method: "POST", body: JSON.stringify(body ?? {}) });
  } catch (error) {
    if (error instanceof ControlError) return { ok: false, message: error.message };
    throw error;
  }
  refresh();
  return { ok: true, message: done };
}

export async function restartService(_: ActionState, form: FormData): Promise<ActionState> {
  const service = String(form.get("service") ?? "");
  if (!(LOG_SERVICES as readonly string[]).includes(service)) return { ok: false, message: "No such service." };
  return ask("/system/restart", { service }, `${service} restarted.`);
}

export async function clearMirrors(): Promise<ActionState> {
  return ask("/system/clear-mirrors", {}, "Mirrors cleared. Each repository is fetched again next time it's indexed or reviewed.");
}

export async function openDataFolder(): Promise<ActionState> {
  return ask("/system/open-data-folder", {}, "Opened in File Explorer.");
}
