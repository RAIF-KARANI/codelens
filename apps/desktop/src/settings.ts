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
};

export const DEFAULTS: DesktopSettings = {
  askedStartAtLogin: false,
  paused: false,
  setupDone: false,
  installUrl: null,
};

/** The settings, or the defaults for anything missing or unreadable: a broken
 * file never stops the app. */
export async function loadSettings(home: string): Promise<DesktopSettings> {
  try {
    const raw = JSON.parse(await readFile(join(home, "desktop.json"), "utf8")) as Partial<
      Record<keyof DesktopSettings, unknown>
    >;
    return {
      askedStartAtLogin:
        typeof raw.askedStartAtLogin === "boolean" ? raw.askedStartAtLogin : DEFAULTS.askedStartAtLogin,
      paused: typeof raw.paused === "boolean" ? raw.paused : DEFAULTS.paused,
      setupDone: typeof raw.setupDone === "boolean" ? raw.setupDone : DEFAULTS.setupDone,
      installUrl: typeof raw.installUrl === "string" ? raw.installUrl : DEFAULTS.installUrl,
    };
  } catch {
    return { ...DEFAULTS };
  }
}

export async function saveSettings(home: string, settings: DesktopSettings): Promise<void> {
  await writeFile(join(home, "desktop.json"), `${JSON.stringify(settings, null, 2)}\n`);
}
