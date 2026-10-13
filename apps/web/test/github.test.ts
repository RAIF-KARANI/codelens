import { describe, expect, it } from "vitest";

import {
  authorizeUrl,
  exchangeCode,
  fetchInstallationIds,
  fetchUser,
  GitHubError,
} from "@/lib/github";

type Call = { url: string; init?: RequestInit };

/** A fetch that answers from a list of responses and records the calls. */
function fakeFetch(...responses: Response[]) {
  const calls: Call[] = [];
  const fn = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    const next = responses.shift();
    if (!next) throw new Error(`unexpected request to ${url}`);
    return next;
  }) as typeof fetch;
  return { fn, calls };
}

const json = (body: unknown, status = 200) => Response.json(body, { status });

describe("authorizeUrl", () => {
  it("sends the client id, callback and state", () => {
    const url = new URL(authorizeUrl("Iv1.abc", "http://localhost:3000/api/auth/callback", "xyz"));
    expect(url.origin + url.pathname).toBe("https://github.com/login/oauth/authorize");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      client_id: "Iv1.abc",
      redirect_uri: "http://localhost:3000/api/auth/callback",
      state: "xyz",
      allow_signup: "false",
    });
  });
});

describe("exchangeCode", () => {
  const args = { clientId: "id", clientSecret: "secret", code: "c0de", redirectUri: "http://x/cb" };

  it("returns the user token", async () => {
    const { fn, calls } = fakeFetch(json({ access_token: "ghu_123", token_type: "bearer" }));
    expect(await exchangeCode(args, fn)).toBe("ghu_123");
    expect(calls[0].url).toBe("https://github.com/login/oauth/access_token");
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      client_id: "id",
      client_secret: "secret",
      code: "c0de",
      redirect_uri: "http://x/cb",
    });
  });

  it("fails on GitHub's 200-with-error answer", async () => {
    const { fn } = fakeFetch(json({ error: "bad_verification_code" }));
    await expect(exchangeCode(args, fn)).rejects.toThrow(/bad_verification_code/);
  });

  it("fails on an HTTP error", async () => {
    const { fn } = fakeFetch(json({}, 500));
    await expect(exchangeCode(args, fn)).rejects.toBeInstanceOf(GitHubError);
  });
});

describe("fetchUser", () => {
  it("sends the token and keeps only what the session needs", async () => {
    const { fn, calls } = fakeFetch(
      json({ id: 7, login: "octocat", name: null, avatar_url: "https://a/7", email: "x@y" }),
    );
    expect(await fetchUser("ghu_1", fn)).toEqual({
      id: 7,
      login: "octocat",
      name: null,
      avatar_url: "https://a/7",
    });
    const headers = new Headers(calls[0].init?.headers);
    expect(headers.get("authorization")).toBe("Bearer ghu_1");
    expect(headers.get("user-agent")).toBe("CodeLens");
  });

  it("fails on a revoked token", async () => {
    const { fn } = fakeFetch(json({ message: "Bad credentials" }, 401));
    await expect(fetchUser("ghu_1", fn)).rejects.toThrow(/HTTP 401/);
  });
});

describe("fetchInstallationIds", () => {
  it("reads every page", async () => {
    const page1 = Array.from({ length: 100 }, (_, i) => ({ id: i + 1 }));
    const { fn, calls } = fakeFetch(
      json({ total_count: 101, installations: page1 }),
      json({ total_count: 101, installations: [{ id: 101 }] }),
    );
    const ids = await fetchInstallationIds("ghu_1", fn);
    expect(ids).toHaveLength(101);
    expect(ids.at(-1)).toBe(101);
    expect(calls.map((c) => new URL(c.url).searchParams.get("page"))).toEqual(["1", "2"]);
  });

  it("is empty for a user with no installations", async () => {
    const { fn, calls } = fakeFetch(json({ total_count: 0, installations: [] }));
    expect(await fetchInstallationIds("ghu_1", fn)).toEqual([]);
    expect(calls).toHaveLength(1);
  });
});
