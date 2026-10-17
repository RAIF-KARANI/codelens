import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  appName,
  handOverArgs,
  hasApp,
  isInstallLink,
  isRegistrationLink,
  markedLines,
  mergeEnv,
  parseDoctor,
  PLACEHOLDER_WEBHOOK_URL,
  registerArgs,
} from "../src/githubapp.js";

describe("the App's name", () => {
  it("is suggested from the Windows user, within GitHub's 34 characters", () => {
    expect(appName("raif")).toBe("CodeLens (raif)");
    expect(appName("a".repeat(40))).toHaveLength(34);
    expect(appName("a".repeat(40)).endsWith(")")).toBe(true);
    expect(appName("<script>")).toBe("CodeLens (script)");
    expect(appName("")).toBe("CodeLens (desktop)");
  });
});

describe("codelens.env", () => {
  it("has an App once its id and webhook secret are there", () => {
    expect(hasApp("API_JWT_SECRET=x\n")).toBe(false);
    expect(hasApp("GITHUB_APP_ID=5\nGITHUB_WEBHOOK_SECRET=w\n")).toBe(true);
    expect(hasApp("GITHUB_APP_ID=5\nGITHUB_WEBHOOK_SECRET=\n")).toBe(false);
  });

  it("gets the App's settings merged in: replaced in place, new ones appended, the rest kept", () => {
    const before = "# CodeLens Desktop's settings\r\nPOSTGRES_PASSWORD=p\r\nGITHUB_APP_ID=1\r\n";
    const after = mergeEnv(before, new Map([["GITHUB_APP_ID", "5"], ["GITHUB_WEBHOOK_SECRET", "w"]]));
    expect(after).toBe("# CodeLens Desktop's settings\nPOSTGRES_PASSWORD=p\nGITHUB_APP_ID=5\nGITHUB_WEBHOOK_SECRET=w\n");
  });
});

describe("the manifest flow's output", () => {
  it("finds the marked lines", () => {
    const out = "Pulling tools\r\nCODELENS_START_URL=http://127.0.0.1:5555/?t=abc\nCreated x\nCODELENS_INSTALL_URL=https://github.com/apps/codelens-me/installations/new\n";
    expect(markedLines(out)).toEqual({
      startUrl: "http://127.0.0.1:5555/?t=abc",
      installUrl: "https://github.com/apps/codelens-me/installations/new",
    });
    expect(markedLines("nothing here")).toEqual({ startUrl: null, installUrl: null });
  });

  it("opens the browser only at the flow's own loopback page", () => {
    expect(isRegistrationLink("http://127.0.0.1:5555/?t=abc", 5555)).toBe(true);
    for (const url of ["http://127.0.0.1:5556/?t=abc", "http://localhost:5555/?t=abc", "https://127.0.0.1:5555/", "http://127.0.0.1:5555/callback", "file:///x"]) {
      expect(isRegistrationLink(url, 5555), url).toBe(false);
    }
  });

  it("sends the user only to GitHub's own install page", () => {
    expect(isInstallLink("https://github.com/apps/codelens-me/installations/new")).toBe(true);
    expect(isInstallLink("https://github.com.evil.example/apps/x/installations/new")).toBe(false);
    expect(isInstallLink("http://github.com/apps/x/installations/new")).toBe(false);
  });
});

describe("registerArgs", () => {
  it("runs the flow in the tools container, on one loopback port, with the webhook off", () => {
    const stack = { home: join("C:", "CodeLens"), images: { api: "a", worker: "w", web: "x" } };
    const args = registerArgs(stack, "CodeLens (raif)", 5555);
    const at = args.indexOf("run");
    expect(args.slice(at, at + 6)).toEqual(["run", "--rm", "--no-deps", "-p", "127.0.0.1:5555:5555", "tools"]);
    expect(args).toContain("--webhook-inactive");
    expect(args).toContain("--no-browser");
    expect(args[args.indexOf("--webhook-url") + 1]).toBe(PLACEHOLDER_WEBHOOK_URL);
    expect(args[args.indexOf("--bind") + 1]).toBe("0.0.0.0");
    expect(args[args.indexOf("--name") + 1]).toBe("CodeLens (raif)");
  });
});

describe("parseDoctor", () => {
  it("reads codelens doctor --desktop --json", () => {
    const json =
      '[{"name": "Ollama", "status": "ok", "detail": "running", "fix": null}, ' +
      '{"name": "GitHub App", "status": "warn", "detail": "not installed", "fix": "install it: https://github.com/apps/x/installations/new"}, ' +
      '{"name": "odd", "status": "??", "detail": "", "fix": null}]';
    expect(parseDoctor(json)).toEqual([
      { name: "Ollama", status: "ok", detail: "running" },
      { name: "GitHub App", status: "warn", detail: "not installed", fix: "install it: https://github.com/apps/x/installations/new" },
      { name: "odd", status: "fail", detail: "" },
    ]);
  });
});

describe("handOverArgs", () => {
  it("gives secrets/ to the workers' user, in the tools container", () => {
    const stack = { home: join("C:", "CodeLens"), images: { api: "a", worker: "w", web: "x" } };
    const args = handOverArgs(stack);
    expect(args.slice(args.indexOf("run"))).toEqual([
      "run", "--rm", "--no-deps", "tools", "chown", "-R", "1000:1000", "/app/secrets",
    ]);
  });
});
