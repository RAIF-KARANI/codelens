/** Updates (planning-v2.md Phase 11 step 7b, D151): a new release is looked
 * for on start and every day, unless the user turned that off (in the tray or
 * on the Settings page); nothing is downloaded or installed without a yes.
 *
 * electron-updater does the mechanics against the project's GitHub Releases:
 * it reads the release's latest.yml (published by .github/workflows/
 * release.yml), downloads the installer and checks its SHA-512 against it,
 * then runs it silently. This file is the flow around it: what to ask, when,
 * and the one thing electron-updater can't know, that CodeLens's stack has to
 * stop before the installer replaces the app (the installer kills whatever
 * still runs from the install folder; the containers would run on with no app
 * to stop them later).
 *
 * The updater and the dialogs come in `UpdateDeps`, so tests play GitHub. */

import { REPO } from "./releasenotes.js";

/** The part of electron-updater's AppUpdater this uses. */
export type UpdaterLike = {
  checkForUpdates(): Promise<unknown>;
  downloadUpdate(): Promise<unknown>;
  quitAndInstall(isSilent: boolean, isForceRunAfter: boolean): void;
  on(event: "update-available" | "update-not-available", listener: (info: { version: string }) => void): unknown;
  on(event: "download-progress", listener: (progress: { percent: number }) => void): unknown;
  on(event: "update-downloaded", listener: (info: { version: string }) => void): unknown;
  on(event: "error", listener: (error: Error) => void): unknown;
};

export type UpdateStatus = "idle" | "checking" | "up-to-date" | "available" | "downloading" | "ready" | "error";

/** What the tray and the Settings page show. */
export type UpdateView = {
  current: string;
  checkForUpdates: boolean;
  status: UpdateStatus;
  /** The newer version found, while there is one. */
  available: string | null;
  /** Of the download, 0-100. */
  progress: number | null;
  lastChecked: string | null;
  error: string | null;
};

export type UpdateDeps = {
  updater: UpdaterLike;
  current: string;
  /** The index of the button picked. */
  ask(message: string, detail: string, buttons: string[]): Promise<number>;
  openExternal(url: string): void;
  /** Stops the stack, so the installer finds nothing of CodeLens running. */
  beforeInstall(): Promise<void>;
  onChange(view: UpdateView): void;
  log(message: string): void;
  now(): Date;
};

/** A day between automatic looks, after the one on start. */
export const CHECK_EVERY_MS = 24 * 60 * 60 * 1000;

export function releasePage(version: string): string {
  return `https://github.com/${REPO}/releases/tag/v${version}`;
}

/** electron-updater's errors can be long (a whole HTTP response); the first
 * line says what happened. */
export function shortError(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  const line = text.split(/\r?\n/)[0] ?? "";
  if (/ENOTFOUND|EAI_AGAIN|ECONNREFUSED|ETIMEDOUT|net::ERR_/.test(line)) return "GitHub couldn't be reached";
  return line.length > 160 ? `${line.slice(0, 157)}…` : line;
}

export class Updates {
  private view: UpdateView;
  /** Asked about once per version, per launch: the daily look doesn't ask
   * again about a version the user said Later to; the tray still offers it. */
  private asked = new Set<string>();
  /** The user asked (a button), so "you're up to date" and errors are said
   * aloud; the automatic looks stay quiet about them. */
  private manual = false;

  constructor(
    private readonly d: UpdateDeps,
    checkForUpdates: boolean,
  ) {
    this.view = {
      current: d.current,
      checkForUpdates,
      status: "idle",
      available: null,
      progress: null,
      lastChecked: null,
      error: null,
    };
    d.updater.on("update-available", (info) => void this.found(info.version));
    d.updater.on("update-not-available", () => void this.upToDate());
    d.updater.on("download-progress", (p) => this.set({ progress: Math.round(p.percent) }));
    d.updater.on("update-downloaded", (info) => void this.downloaded(info.version));
    d.updater.on("error", (error) => void this.failed(error));
  }

  get state(): UpdateView {
    return { ...this.view };
  }

  setCheckForUpdates(on: boolean): void {
    this.set({ checkForUpdates: on });
  }

  /** The automatic look (start, daily): only if the user hasn't turned it
   * off, and not while one is under way. */
  async checkAutomatically(): Promise<void> {
    if (!this.view.checkForUpdates) return;
    await this.check(false);
  }

  /** "Check for updates now" (tray, Settings): works even when the automatic
   * looks are off. */
  async checkNow(): Promise<void> {
    await this.check(true);
  }

  private async check(manual: boolean): Promise<void> {
    if (["checking", "downloading", "ready"].includes(this.view.status)) {
      if (manual && this.view.status === "ready") await this.offerRestart(this.view.available ?? "");
      return;
    }
    this.manual = manual;
    this.set({ status: "checking", error: null });
    try {
      await this.d.updater.checkForUpdates();
      // No answer at all: electron-updater skipped the look (an unpackaged,
      // development run has no app-update.yml).
      if (this.view.status === "checking") this.set({ status: "idle" });
    } catch (error) {
      await this.failed(error);
    }
  }

  private async found(version: string): Promise<void> {
    this.set({ status: "available", available: version, lastChecked: this.d.now().toISOString() });
    this.d.log(`updates: ${version} is available (this is ${this.d.current})`);
    if (!this.manual && this.asked.has(version)) return;
    this.asked.add(version);
    const picked = await this.d.ask(
      `CodeLens ${version} is available`,
      `You have ${this.d.current}. Updating downloads about 110 MB, then asks before it restarts CodeLens. ` +
        "Your reviews and settings stay; the database is backed up first if the update changes it.",
      ["Update", "Later", "What's new"],
    );
    if (picked === 0) await this.install();
    else if (picked === 2) this.d.openExternal(releasePage(version));
  }

  private async upToDate(): Promise<void> {
    this.set({ status: "up-to-date", available: null, lastChecked: this.d.now().toISOString() });
    if (this.manual) await this.d.ask("CodeLens is up to date", `You have ${this.d.current}, the latest release.`, ["OK"]);
  }

  /** Download (asked for already), then ask before restarting. */
  async install(): Promise<void> {
    if (this.view.status === "ready") return this.offerRestart(this.view.available ?? "");
    if (this.view.status !== "available") return;
    this.set({ status: "downloading", progress: 0 });
    this.d.log(`updates: downloading ${this.view.available}`);
    try {
      await this.d.updater.downloadUpdate();
    } catch (error) {
      await this.failed(error);
    }
  }

  private async downloaded(version: string): Promise<void> {
    this.set({ status: "ready", available: version, progress: 100 });
    this.d.log(`updates: ${version} downloaded and checked`);
    await this.offerRestart(version);
  }

  private async offerRestart(version: string): Promise<void> {
    const picked = await this.d.ask(
      `Restart CodeLens to update to ${version}?`,
      "CodeLens stops its services, installs the update and starts again: about a minute without reviews. " +
        "Pull requests pushed meanwhile are reviewed once it's back.",
      ["Restart now", "Later"],
    );
    if (picked !== 0) return;
    this.d.log(`updates: stopping the stack, then installing ${version}`);
    await this.d.beforeInstall();
    this.d.updater.quitAndInstall(true, true);
  }

  private async failed(error: unknown): Promise<void> {
    const message = shortError(error);
    const wasDownloading = this.view.status === "downloading";
    this.d.log(`updates: ${message}`);
    // A failed download keeps the version on offer, to try again.
    this.set({ status: wasDownloading ? "available" : "error", progress: null, error: message });
    if (this.manual || wasDownloading) {
      await this.d.ask(wasDownloading ? "The update didn't download" : "CodeLens couldn't look for updates", message, ["OK"]);
    }
  }

  private set(change: Partial<UpdateView>): void {
    this.view = { ...this.view, ...change };
    this.d.onChange(this.state);
  }
}
