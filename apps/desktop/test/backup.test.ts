import { describe, expect, it } from "vitest";

import { backupName, oldBackups, parseCurrent, staleImages } from "../src/backup.js";

describe("parseCurrent", () => {
  // As `alembic current` printed it on this machine (step 7b), logging included.
  const LOGS = "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\nINFO  [alembic.runtime.migration] Will assume transactional DDL.\n";

  it("reads the revision and whether it is the image's head", () => {
    expect(parseCurrent(`${LOGS}a8d3c41f0b27 (head)\n`)).toEqual({ revision: "a8d3c41f0b27", atHead: true });
    expect(parseCurrent(`${LOGS}cd53acf29082\n`)).toEqual({ revision: "cd53acf29082", atHead: false });
  });

  it("finds nothing in an empty database", () => {
    expect(parseCurrent(LOGS)).toBeNull();
  });

  it("isn't fooled by Docker pulling the image first (found live)", () => {
    const pull = " 4f4fb700ef54 Pull complete \n 2d35ebdb57d9 Downloading [===>   ]  12MB/100MB\n migrate Pulled \n";
    expect(parseCurrent(`${pull}${LOGS}`)).toBeNull();
    expect(parseCurrent(`${pull}${LOGS}cd53acf29082\n`)).toEqual({ revision: "cd53acf29082", atHead: false });
  });
});

describe("backups", () => {
  it("names a backup by the version it came from and when", () => {
    expect(backupName("0.1.0", new Date("2026-10-18T12:34:56.789Z"))).toBe("codelens-0.1.0-20261018T123456Z.dump");
  });

  it("keeps the newest three, and leaves other files alone", () => {
    const names = [
      "codelens-0.1.0-20261001T000000Z.dump",
      "codelens-0.2.0-20261018T000000Z.dump",
      "codelens-0.1.1-20261005T000000Z.dump",
      "codelens-0.1.2-20261010T000000Z.dump",
      "notes.txt",
    ];
    expect(oldBackups(names)).toEqual(["codelens-0.1.0-20261001T000000Z.dump"]);
    expect(oldBackups(names, 1)).toHaveLength(3);
    expect(oldBackups(names.slice(0, 2))).toEqual([]);
  });
});

describe("staleImages", () => {
  const listed = [
    "sha256:aaa ghcr.io/raif-karani/codelens-api",
    "sha256:bbb ghcr.io/raif-karani/codelens-api",
    "sha256:ccc ghcr.io/raif-karani/codelens-web",
    "sha256:ddd redis",
    "sha256:eee ghcr.io/someone-else/codelens-api",
    "sha256:fff codelens-desktop/api",
  ].join("\n");

  it("is CodeLens's published images that this version doesn't pin, and nothing else", () => {
    expect(staleImages(listed, ["sha256:aaa", "sha256:ccc", ""])).toEqual(["sha256:bbb"]);
  });
});
