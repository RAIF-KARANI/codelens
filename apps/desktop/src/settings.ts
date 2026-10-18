/** The app's own preferences, in the data folder (`desktop.json`): what the
 * user chose, kept between launches. Secrets live in codelens.env, never here. */

import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

export type DesktopSettings = {
  /** The "start when Windows starts?" question was asked (Q26: once). */
  askedStartAtLogin: boolean;
  /** Pause stays paused across a restart, until Resume. */
  paused: boolean;
  /** The first-run wizard got to the end once (step 5): later starts go
   * straight to the dashboard, and what breaks later gets the status pages. */
  setupDone: boolean;
  /** The App's install page, from the wizard (shown in Settings). */
  installUrl: string | null;
  /** Look for a new release on start and daily (D151: on unless turned off). */
  checkForUpdates: boolean;
  /** The app version whose stack last came up: a different one means this
   * start follows an update (step 7b: back up before new migrations, then
   * remove the older version's images). */
  lastVersion: string | null;
};

export const DEFAULTS: DesktopSettings = {
  askedStartAtLogin: false,
  paused: false,
  setupDone: false,
  installUrl: null,
  checkForUpdates: true,
  lastVersion: null,
};

/** The settings, or the defaults for anything missing or unreadable: a broken
 * file never stops the app. */
export async function loadSettings(home: string): Promise<DesktopSettings> {
  try {
    const raw = JSON.parse(await readFile(join(home, "desktop.json"), "utf8")) as Partial<
      Record<keyof DesktopSettings, unknown>
    >;
    const bool = (key: "askedStartAtLogin" | "paused" | "setupDone" | "checkForUpdates") =>
      typeof raw[key] === "boolean" ? (raw[key] as boolean) : DEFAULTS[key];
    const text = (key: "installUrl" | "lastVersion") => (typeof raw[key] === "string" ? (raw[key] as string) : DEFAULTS[key]);
    return {
      askedStartAtLogin: bool("askedStartAtLogin"),
      paused: bool("paused"),
      setupDone: bool("setupDone"),
      installUrl: text("installUrl"),
      checkForUpdates: bool("checkForUpdates"),
      lastVersion: text("lastVersion"),
    };
  } catch {
    return { ...DEFAULTS };
  }
}

export async function saveSettings(home: string, settings: DesktopSettings): Promise<void> {
  await writeFile(join(home, "desktop.json"), `${JSON.stringify(settings, null, 2)}\n`);
}
