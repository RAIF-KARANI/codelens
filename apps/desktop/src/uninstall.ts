/** What uninstalling CodeLens does besides removing its program files
 * (planning-v2.md Phase 11 step 7). The NSIS uninstaller runs the installed
 * app once more as `CodeLens.exe --uninstall` (build/installer.nsh), after it
 * has closed the running app and before it deletes the app's files:
 *
 * - Windows forgets "start at login" and the wizard's resume entry, so
 *   nothing tries to start a program that's gone.
 * - The stack's containers are removed (`compose down`). They restart with
 *   Docker (`restart: unless-stopped`), and the uninstaller kills the app
 *   rather than letting it stop them, so without this they would run on with
 *   no app.
 * - Asked first: delete the data too. That is the data folder AND the
 *   database volume, both or neither: Postgres keeps the password it was
 *   first started with, so a volume left behind locks out the next install's
 *   new one, and a folder left behind with no volume is a database lost with
 *   its key still on disk. The volume goes first; the folder only once it's
 *   gone. Then a link to the user's GitHub App, which stays on GitHub.
 *
 * Silent (`/S`), nothing is asked: the data stays.
 * Everything with a side effect comes in `UninstallDeps`, so tests play each
 * machine: Docker running, stopped, or gone. */

import type { Run, DockerState } from "./stack.js";

export type Question = {
  message: string;
  detail: string;
  buttons: string[];
  /** The button Escape or closing the dialog stands for. */
  cancel: number;
};

export type UninstallDeps = {
  silent: boolean;
  /** The index of the button the user picked. */
  ask(question: Question): Promise<number>;
  dockerState(): Promise<DockerState>;
  /** `docker compose down`, with `-v` to remove the database volume too. */
  down(removeVolume: boolean): Promise<Run>;
  /** CodeLens's own images (best effort: one in use elsewhere stays). */
  removeImages(): Promise<void>;
  /** The data folder, %LOCALAPPDATA%\CodeLens. */
  removeDataFolder(): Promise<void>;
  /** Start at login and the wizard's RunOnce entry, both off. */
  forgetStartup(): Promise<void>;
  startDocker(): void;
  openExternal(url: string): void;
  /** The user's GitHub App's settings page, if the wizard made one. */
  appSettingsUrl: string | null;
  dataFolder: string;
  log(message: string): Promise<void>;
};

/** "deleted": the data folder and the database are gone (the uninstaller
 * then removes the app's own Electron folder too); "kept": both stay. */
export type UninstallResult = "kept" | "deleted";

const KEEP = 0;
const DELETE = 1;

/** Docker running, or the user's word that it can't be: true if it answers. */
async function dockerAnswers(d: UninstallDeps, why: string): Promise<boolean> {
  let started = false;
  for (;;) {
    const state = await d.dockerState();
    if (state === "running") return true;
    if (state === "missing" || d.silent) return false;
    if (!started) {
      d.startDocker();
      started = true;
    }
    const picked = await d.ask({
      message: "Docker Desktop isn't running",
      detail: `${why}\n\nCodeLens has asked it to start. Once Docker Desktop says it's running, choose Try again.`,
      buttons: ["Try again", "Skip"],
      cancel: 1,
    });
    if (picked !== 0) return false;
  }
}

export async function uninstall(d: UninstallDeps): Promise<UninstallResult> {
  await d.forgetStartup();

  const choice = d.silent
    ? KEEP
    : await d.ask({
        message: "Keep CodeLens's data?",
        detail:
          `Your reviews, settings and your GitHub App's key are in ${d.dataFolder} and in Docker. ` +
          "Kept, installing CodeLens again picks up where you left off.\n\n" +
          "Deleted, they are gone for good.",
        buttons: ["Keep my data", "Delete everything"],
        cancel: KEEP,
      });

  if (choice === DELETE) {
    const state = await d.dockerState();
    // Without Docker there is no volume left to lock a new install out:
    // uninstalling Docker Desktop removed its disk with it.
    if (state === "missing") {
      await d.log("uninstall: Docker isn't installed; deleting the data folder");
      await d.removeDataFolder();
      return await deleted(d);
    }
    if (await dockerAnswers(d, "CodeLens needs it to delete its database. Without it, CodeLens keeps all of its data (both or neither).")) {
      const run = await d.down(true);
      if (run.code === 0) {
        await d.log("uninstall: containers and the database volume removed; deleting the data folder");
        await d.removeDataFolder();
        await d.removeImages();
        return await deleted(d);
      }
      await d.log(`uninstall: compose down -v failed (exit ${run.code}): ${run.output.trim()}`);
      await d.ask({
        message: "CodeLens couldn't delete its database",
        detail: `So it kept all of its data, in ${d.dataFolder} and in Docker. Docker said:\n${lastLine(run.output)}`,
        buttons: ["OK"],
        cancel: 0,
      });
      return "kept";
    }
    await tellKept(d, true);
    return "kept";
  }

  // Keeping the data: only the containers go, so they don't run on alone.
  if (await dockerAnswers(d, "CodeLens needs it to remove its containers, which otherwise start again with Docker.")) {
    const run = await d.down(false);
    await d.log(`uninstall: containers removed (exit ${run.code}); the data stays`);
  } else {
    await d.log("uninstall: Docker not answering; containers left as they are, the data stays");
    if (!d.silent && (await d.dockerState()) !== "missing") await tellKept(d, false);
  }
  return "kept";
}

async function deleted(d: UninstallDeps): Promise<UninstallResult> {
  if (d.appSettingsUrl && !d.silent) {
    const picked = await d.ask({
      message: "Your GitHub App is still on GitHub",
      detail:
        "CodeLens's copy of its key is deleted, so it can't review anything any more. " +
        "To remove the App itself, open its settings and choose Delete GitHub App (under Advanced).",
      buttons: ["Open its settings", "Not now"],
      cancel: 1,
    });
    if (picked === 0) d.openExternal(`${d.appSettingsUrl}/advanced`);
  }
  return "deleted";
}

/** Docker didn't answer, so its part is left for the user, in Docker
 * Desktop's own words. */
async function tellKept(d: UninstallDeps, wantedDeleted: boolean): Promise<void> {
  const containers = "In Docker Desktop, delete the codelens-desktop group under Containers";
  await d.ask({
    message: wantedDeleted ? "CodeLens kept its data" : "CodeLens's containers are still in Docker",
    detail: wantedDeleted
      ? `It's in ${d.dataFolder} and in Docker. To delete it later: ${containers}, and the ` +
        "codelens-desktop_pgdata volume under Volumes; then delete the folder."
      : `They start again with Docker Desktop. ${containers}. Your data stays in ${d.dataFolder}.`,
    buttons: ["OK"],
    cancel: 0,
  });
}

function lastLine(output: string): string {
  return output.trim().split(/\r?\n/).at(-1) ?? "";
}
