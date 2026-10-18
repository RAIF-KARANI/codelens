import { describe, expect, it } from "vitest";

import { type DockerState, downArgs, oneOffArgs } from "../src/stack.js";
import { type Question, uninstall, type UninstallDeps } from "../src/uninstall.js";

/** A machine for the uninstaller: Docker in the given states, one per look,
 * the user's answers in order, and a record of what was done. */
function machine(opts: {
  docker: DockerState[];
  answers?: number[];
  silent?: boolean;
  downCode?: number;
  appSettingsUrl?: string | null;
}) {
  const did: string[] = [];
  const asked: Question[] = [];
  const answers = [...(opts.answers ?? [])];
  const docker = [...opts.docker];
  const deps: UninstallDeps = {
    silent: opts.silent ?? false,
    ask: async (q) => {
      asked.push(q);
      return answers.shift() ?? q.cancel;
    },
    dockerState: async () => (docker.length > 1 ? docker.shift()! : docker[0]!),
    down: async (removeVolume) => {
      did.push(removeVolume ? "down -v" : "down");
      return { code: opts.downCode ?? 0, output: opts.downCode ? "Error response from daemon: volume is in use\n" : "" };
    },
    removeImages: async () => void did.push("rmi"),
    removeDataFolder: async () => void did.push("rm folder"),
    forgetStartup: async () => void did.push("forget startup"),
    startDocker: () => void did.push("start docker"),
    openExternal: (url) => void did.push(`open ${url}`),
    appSettingsUrl: opts.appSettingsUrl === undefined ? "https://github.com/settings/apps/codelens-me" : opts.appSettingsUrl,
    dataFolder: "C:\\Users\\me\\AppData\\Local\\CodeLens",
    log: async () => {},
  };
  return { deps, did, asked };
}

describe("uninstalling, keeping the data", () => {
  it("removes the containers and keeps the volume and the folder", async () => {
    const m = machine({ docker: ["running"], answers: [0] });
    expect(await uninstall(m.deps)).toBe("kept");
    expect(m.did).toEqual(["forget startup", "down"]);
    expect(m.asked[0]?.buttons).toEqual(["Keep my data", "Delete everything"]);
  });

  it("keeps the data when the question is closed", async () => {
    const m = machine({ docker: ["running"] });
    expect(await uninstall(m.deps)).toBe("kept");
    expect(m.did).not.toContain("down -v");
  });

  it("asks nothing when silent, and never waits for Docker", async () => {
    const running = machine({ docker: ["running"], silent: true });
    expect(await uninstall(running.deps)).toBe("kept");
    expect(running.did).toEqual(["forget startup", "down"]);
    expect(running.asked).toEqual([]);

    const stopped = machine({ docker: ["stopped"], silent: true });
    expect(await uninstall(stopped.deps)).toBe("kept");
    expect(stopped.did).toEqual(["forget startup"]);
    expect(stopped.asked).toEqual([]);
  });

  it("starts Docker and tries again, or says where the containers are left", async () => {
    const retried = machine({ docker: ["stopped", "running"], answers: [0, 0] });
    expect(await uninstall(retried.deps)).toBe("kept");
    expect(retried.did).toEqual(["forget startup", "start docker", "down"]);

    const skipped = machine({ docker: ["stopped"], answers: [0, 1] });
    expect(await uninstall(skipped.deps)).toBe("kept");
    expect(skipped.did).toEqual(["forget startup", "start docker"]);
    expect(skipped.asked.at(-1)?.message).toBe("CodeLens's containers are still in Docker");
  });
});

describe("uninstalling, deleting everything", () => {
  it("removes the volume first, then the folder and the images, then points at the App", async () => {
    const m = machine({ docker: ["running"], answers: [1, 0] });
    expect(await uninstall(m.deps)).toBe("deleted");
    expect(m.did).toEqual([
      "forget startup",
      "down -v",
      "rm folder",
      "rmi",
      "open https://github.com/settings/apps/codelens-me/advanced",
    ]);
  });

  it("keeps both if the volume can't go: both or neither", async () => {
    const m = machine({ docker: ["running"], answers: [1], downCode: 1 });
    expect(await uninstall(m.deps)).toBe("kept");
    expect(m.did).toEqual(["forget startup", "down -v"]);
    expect(m.asked.at(-1)?.detail).toContain("volume is in use");
  });

  it("keeps both when Docker is stopped and the user skips", async () => {
    const m = machine({ docker: ["stopped"], answers: [1, 1] });
    expect(await uninstall(m.deps)).toBe("kept");
    expect(m.did).toEqual(["forget startup", "start docker"]);
    expect(m.asked.at(-1)?.message).toBe("CodeLens kept its data");
  });

  it("deletes after Docker comes back", async () => {
    const m = machine({ docker: ["stopped", "stopped", "running"], answers: [1, 0, 1] });
    expect(await uninstall(m.deps)).toBe("deleted");
    expect(m.did).toEqual(["forget startup", "start docker", "down -v", "rm folder", "rmi"]);
  });

  it("deletes the folder alone when Docker is gone (its disk went with it)", async () => {
    const m = machine({ docker: ["missing"], answers: [1], appSettingsUrl: null });
    expect(await uninstall(m.deps)).toBe("deleted");
    expect(m.did).toEqual(["forget startup", "rm folder"]);
    // No App made: nothing to point at.
    expect(m.asked).toHaveLength(1);
  });
});

describe("downArgs", () => {
  it("names the project alone, so no compose file or codelens.env is needed", () => {
    expect(downArgs(false)).toEqual(["compose", "-p", "codelens-desktop", "down", "--remove-orphans"]);
    expect(downArgs(true)).toEqual(["compose", "-p", "codelens-desktop", "down", "--remove-orphans", "--volumes"]);
  });

  it("finds the one-off containers down leaves behind (the wizard's tools run)", () => {
    expect(oneOffArgs()).toEqual([
      "ps", "-aq",
      "--filter", "label=com.docker.compose.project=codelens-desktop",
      "--filter", "label=com.docker.compose.oneoff=True",
    ]);
  });
});
