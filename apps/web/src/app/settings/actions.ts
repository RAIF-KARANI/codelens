"use server";

import { refresh } from "next/cache";

import { isLocal } from "@/lib/config";
import { control, ControlError, parseAccounts, type SettingsView } from "@/lib/control";

export type SaveState = {
  ok: boolean;
  message: string;
  errors?: Record<string, string>;
  /** What was typed, shown again after a refusal: React clears a form once
   * its action has run. */
  values?: Record<string, string>;
} | null;

/** A Server Action is a public endpoint: in local mode src/proxy.ts has
 * already checked the app's key (D165), and the app checks every value
 * (D175). Here: only ever in local mode, and the form's text made into the
 * shapes the app expects. */
async function save(
  changes: Record<string, unknown>,
  done: string,
  values: Record<string, string> = {},
): Promise<SaveState> {
  if (!isLocal()) return { ok: false, message: "Settings exist only in the CodeLens app." };
  try {
    await control<SettingsView>("/settings", { method: "PUT", body: JSON.stringify(changes) });
  } catch (error) {
    if (error instanceof ControlError) {
      return {
        ok: false,
        message: error.code === "invalid" ? "Some values were refused." : error.message,
        errors: error.details,
        values,
      };
    }
    throw error;
  }
  refresh();
  return { ok: true, message: done };
}

const text = (form: FormData, name: string) => String(form.get(name) ?? "");

/** A number field; empty is NaN, which the app refuses with its reason. */
const number = (form: FormData, name: string) => {
  const value = text(form, name).trim();
  return value === "" ? Number.NaN : Number(value);
};

export async function saveApp(_: SaveState, form: FormData): Promise<SaveState> {
  return save(
    {
      allowedAccounts: parseAccounts(text(form, "allowedAccounts")),
      reviewsPerHour: number(form, "reviewsPerHour"),
    },
    "Saved. The services restarted with it.",
    { allowedAccounts: text(form, "allowedAccounts"), reviewsPerHour: text(form, "reviewsPerHour") },
  );
}

export async function saveEvents(_: SaveState, form: FormData): Promise<SaveState> {
  return save({ pollIntervalS: number(form, "pollIntervalS") }, "Saved. The poller restarted with it.", {
    pollIntervalS: text(form, "pollIntervalS"),
  });
}

/** The context window: back to 8k is an ordinary setting; 16k first sets the
 * user's Ollama up for it (D146), which the page asked them about. */
export async function setWindow(_: SaveState, form: FormData): Promise<SaveState> {
  if (!isLocal()) return { ok: false, message: "Settings exist only in the CodeLens app." };
  if (form.get("window") !== "long") return save({ contextWindow: 8192 }, "Back to the 8k window.");
  try {
    await control<SettingsView>("/settings/long-window", { method: "POST" });
  } catch (error) {
    if (error instanceof ControlError) return { ok: false, message: error.message };
    throw error;
  }
  refresh();
  return { ok: true, message: "Reviews now use a 16k window." };
}
