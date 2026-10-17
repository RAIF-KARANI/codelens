import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { APP_KEY_COOKIE, checkLocalRequest, isLoopbackHost, keyMatches } from "@/lib/app-key";
import { config, proxy } from "@/proxy";

const KEY = "k".repeat(43);

describe("isLoopbackHost", () => {
  it.each(["127.0.0.1:49152", "localhost:3000", "[::1]:3000", "127.0.0.1", "LOCALHOST:80"])(
    "accepts %s",
    (host) => expect(isLoopbackHost(host)).toBe(true),
  );

  it.each([
    "evil.example",
    // DNS rebinding: a name the attacker controls, made to resolve to 127.0.0.1.
    "evil.example:3000",
    "127.0.0.1.evil.example",
    "localhost.evil.example:3000",
    "127.0.0.2:3000",
    "192.168.1.5:3000",
    "[::2]:3000",
    "127.0.0.1:abc",
    "127.0.0.1:3000:1",
    "",
    null,
    undefined,
  ])("refuses %s", (host) => expect(isLoopbackHost(host)).toBe(false));
});

describe("keyMatches", () => {
  it("matches only the same key", () => {
    expect(keyMatches(KEY, KEY)).toBe(true);
    expect(keyMatches(`${KEY}x`, KEY)).toBe(false);
    expect(keyMatches(KEY.slice(1), KEY)).toBe(false);
    expect(keyMatches("", KEY)).toBe(false);
    expect(keyMatches(undefined, KEY)).toBe(false);
  });
});

describe("checkLocalRequest", () => {
  it("lets the app's window in", () => {
    expect(checkLocalRequest("127.0.0.1:49152", KEY, KEY)).toBeNull();
  });

  it("refuses another host, or no key", () => {
    expect(checkLocalRequest("evil.example", KEY, KEY)).toBe("bad_host");
    expect(checkLocalRequest("127.0.0.1:49152", undefined, KEY)).toBe("bad_key");
    expect(checkLocalRequest("127.0.0.1:49152", "guess", KEY)).toBe("bad_key");
  });

  it("fails closed without a key of its own", () => {
    expect(checkLocalRequest("127.0.0.1", KEY, undefined)).toBe("not_configured");
    expect(checkLocalRequest("127.0.0.1", "", "")).toBe("not_configured");
    expect(checkLocalRequest("127.0.0.1", "short", "short")).toBe("not_configured");
  });
});

describe("proxy in local mode", () => {
  beforeEach(() => {
    vi.stubEnv("LOCAL_MODE", "true");
    vi.stubEnv("LOCAL_APP_KEY", KEY);
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  function request(path: string, { host = "127.0.0.1:49152", key = KEY as string | null } = {}) {
    const headers = new Headers({ host });
    if (key !== null) headers.set("cookie", `${APP_KEY_COOKIE}=${key}`);
    return new NextRequest(`http://${host}${path}`, { headers, method: "GET" });
  }

  it("passes a request with the key and a loopback host", async () => {
    const response = await proxy(request("/repos"));
    // NextResponse.next() says so in a header.
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it.each(["/", "/repos/1", "/api/nodes/7", "/api/nodes/7.svg", "/api/palette", "/login"])(
    "refuses %s without the key",
    async (path) => {
      const response = await proxy(request(path, { key: null }));
      expect(response.status).toBe(403);
      expect(response.headers.get("x-middleware-next")).toBeNull();
    },
  );

  it("sends the login page to the repos: nobody signs in here", async () => {
    const response = await proxy(request("/login?next=%2Freviews"));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("http://127.0.0.1:49152/repos");
  });

  it("refuses the key from another host (DNS rebinding)", async () => {
    const response = await proxy(request("/repos", { host: "evil.example:49152" }));
    expect(response.status).toBe(403);
  });

  it("refuses everything when the app's key isn't set", async () => {
    vi.stubEnv("LOCAL_APP_KEY", "");
    const response = await proxy(request("/repos"));
    expect(response.status).toBe(503);
  });
});

describe("the proxy's matcher", () => {
  // Next.js anchors the pattern to the whole path.
  const matches = (path: string) =>
    config.matcher.some((pattern) => new RegExp(`^${pattern}$`).test(path));

  it("runs on pages, route handlers and anything ending in .svg", () => {
    for (const path of ["/", "/repos/1", "/api/nodes/7", "/api/nodes/7.svg", "/icon.svg", "/_next/image"]) {
      expect(matches(path), path).toBe(true);
    }
  });

  it("skips only the build's static files", () => {
    expect(matches("/_next/static/chunks/app.js")).toBe(false);
  });
});

describe("proxy with GitHub sign-in", () => {
  afterEach(() => vi.unstubAllEnvs());

  const request = (path: string) => new NextRequest(`http://localhost:3000${path}`);

  it("sends a signed-out visitor to the login page", async () => {
    vi.stubEnv("SESSION_SECRET", "s".repeat(32));
    const response = await proxy(request("/repos/1?status=failed"));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(
      "http://localhost:3000/login?next=%2Frepos%2F1%3Fstatus%3Dfailed",
    );
  });

  it.each(["/login", "/api/nodes/7", "/icon.svg", "/_next/image", "/favicon.ico"])(
    "leaves %s to its own checks",
    async (path) => {
      const response = await proxy(request(path));
      expect(response.headers.get("x-middleware-next")).toBe("1");
    },
  );
});
