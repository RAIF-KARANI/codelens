import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  APP_DIR,
  codelensHome,
  composeArgs,
  composeEnv,
  ensureHome,
  freePort,
  LOCAL_IMAGES,
  parseEnv,
  pinnedImages,
  PROJECT,
} from "../src/stack.js";

const DIGEST = "a".repeat(64);
const IMAGES = {
  api: `ghcr.io/raif-karani/codelens-api@sha256:${DIGEST}`,
  worker: `ghcr.io/raif-karani/codelens-worker:sha-1234567@sha256:${DIGEST}`,
  web: `ghcr.io/raif-karani/codelens-web@sha256:${DIGEST}`,
};

describe("codelensHome", () => {
  it("prefers CODELENS_HOME, then %LOCALAPPDATA%\\CodeLens", () => {
    expect(codelensHome({ CODELENS_HOME: "/x", LOCALAPPDATA: "/y" })).toBe("/x");
    expect(codelensHome({ LOCALAPPDATA: "/y" })).toBe(join("/y", "CodeLens"));
    expect(codelensHome({})).toMatch(/\.codelens$/);
  });
});

describe("parseEnv", () => {
  it("reads KEY=value lines, with CRLF and comments", () => {
    const values = parseEnv("# note\r\nA=1\r\n\r\nB = two=2\nnot a line\n");
    expect([...values]).toEqual([
      ["A", "1"],
      ["B", "two=2"],
    ]);
  });
});

describe("ensureHome", () => {
  let home: string;
  beforeEach(async () => {
    home = join(await mkdtemp(join(tmpdir(), "codelens-home-")), "CodeLens");
  });
  afterEach(async () => rm(join(home, ".."), { recursive: true, force: true }));

  it("creates the folders and two strong secrets", async () => {
    const { envFile, created } = await ensureHome(home);
    expect(created).toEqual(["POSTGRES_PASSWORD", "API_JWT_SECRET"]);
    const values = parseEnv(await readFile(envFile, "utf8"));
    for (const name of created) {
      // 32 random bytes as base64url: 43 characters, at least 32 bytes.
      expect(values.get(name)).toMatch(/^[\w-]{43}$/);
    }
    expect(values.get("POSTGRES_PASSWORD")).not.toBe(values.get("API_JWT_SECRET"));
  });

  it("keeps what is there on a second run", async () => {
    const { envFile } = await ensureHome(home);
    const before = await readFile(envFile, "utf8");
    const again = await ensureHome(home);
    expect(again.created).toEqual([]);
    expect(await readFile(envFile, "utf8")).toBe(before);
  });

  it("adds only a missing secret, keeping the user's other lines", async () => {
    const { envFile } = await ensureHome(home);
    const kept = parseEnv(await readFile(envFile, "utf8")).get("POSTGRES_PASSWORD");
    await writeFile(envFile, `POSTGRES_PASSWORD=${kept}\r\nGITHUB_APP_ID=42`);
    const again = await ensureHome(home);
    expect(again.created).toEqual(["API_JWT_SECRET"]);
    const values = parseEnv(await readFile(envFile, "utf8"));
    expect(values.get("POSTGRES_PASSWORD")).toBe(kept);
    expect(values.get("GITHUB_APP_ID")).toBe("42");
    expect(values.get("API_JWT_SECRET")).toMatch(/^[\w-]{43}$/);
  });
});

describe("pinnedImages", () => {
  it("accepts images pinned by digest", () => {
    expect(pinnedImages(IMAGES)).toEqual(IMAGES);
  });

  it.each([
    ["a tag alone", { ...IMAGES, web: "ghcr.io/raif-karani/codelens-web:latest" }],
    ["a short digest", { ...IMAGES, api: "ghcr.io/raif-karani/codelens-api@sha256:abc" }],
    ["an empty entry", { ...IMAGES, worker: "" }],
    ["a missing entry", { api: IMAGES.api, worker: IMAGES.worker }],
    ["no object", null],
  ])("refuses %s", (_, value) => {
    expect(() => pinnedImages(value)).toThrow(/images\.json/);
  });

  it("refuses the committed placeholder with a way forward", async () => {
    const placeholder = JSON.parse(await readFile(join(APP_DIR, "images.json"), "utf8"));
    expect(() => pinnedImages(placeholder)).toThrow(/--local/);
  });
});

describe("compose", () => {
  const stack = { home: join("C:", "Users", "me", "AppData", "Local", "CodeLens"), images: IMAGES };

  it("names the project, its files and the install's env file", () => {
    const args = composeArgs(stack, "up", "-d");
    expect(args.slice(0, 3)).toEqual(["compose", "-p", PROJECT]);
    expect(args).toContain(join(APP_DIR, "compose.yaml"));
    expect(args).not.toContain(join(APP_DIR, "compose.build.yaml"));
    expect(args.slice(-4)).toEqual(["--env-file", join(stack.home, "codelens.env"), "up", "-d"]);
    expect(composeArgs({ ...stack, build: true }, "build")).toContain(
      join(APP_DIR, "compose.build.yaml"),
    );
  });

  it("passes the port and key only for a launch", () => {
    const launch = { port: 49152, appKey: "k".repeat(43) };
    expect(composeEnv(stack, launch)).toMatchObject({
      CODELENS_HOME: stack.home,
      CODELENS_WEB_IMAGE: IMAGES.web,
      CODELENS_WEB_PORT: "49152",
      CODELENS_APP_KEY: launch.appKey,
    });
    const stopping = composeEnv(stack);
    expect(stopping).not.toHaveProperty("CODELENS_APP_KEY");
    expect(stopping).not.toHaveProperty("CODELENS_WEB_PORT");
  });

  it("has local names for images built from the checkout", () => {
    expect(Object.values(LOCAL_IMAGES).every((ref) => ref.endsWith(":local"))).toBe(true);
  });
});

describe("freePort", () => {
  it("returns a loopback port that can be bound", async () => {
    const port = await freePort();
    expect(port).toBeGreaterThan(0);
    const server = createServer();
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(port, "127.0.0.1", resolve);
    });
    await new Promise((resolve) => server.close(resolve));
  });
});
