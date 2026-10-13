import { decodeJwt, decodeProtectedHeader, SignJWT } from "jose";
import { describe, expect, it } from "vitest";

import {
  API_TOKEN_TTL_SECONDS,
  apiToken,
  openSession,
  sealSession,
  SESSION_TTL_SECONDS,
  type SessionUser,
} from "@/lib/tokens";

const SECRET = "s".repeat(32);
const OTHER = "o".repeat(32);
const NOW = new Date("2026-10-13T12:00:00Z");
const user: SessionUser = {
  id: "583231",
  login: "octocat",
  name: "The Octocat",
  avatarUrl: "https://avatars.githubusercontent.com/u/583231",
  installations: [170701175, 42],
};

describe("session cookie", () => {
  it("round-trips the user", async () => {
    const value = await sealSession(user, SECRET, NOW);
    expect(await openSession(value, SECRET, NOW)).toEqual(user);
  });

  it("keeps a null name", async () => {
    const value = await sealSession({ ...user, name: null }, SECRET, NOW);
    expect((await openSession(value, SECRET, NOW))?.name).toBeNull();
  });

  it("is signed out when missing", async () => {
    expect(await openSession(undefined, SECRET)).toBeNull();
    expect(await openSession("", SECRET)).toBeNull();
    expect(await openSession("not a jwt", SECRET)).toBeNull();
  });

  it("rejects another secret's signature", async () => {
    const value = await sealSession(user, OTHER, NOW);
    expect(await openSession(value, SECRET, NOW)).toBeNull();
  });

  it("rejects an edited payload", async () => {
    const [header, , signature] = (await sealSession(user, SECRET, NOW)).split(".");
    const forged = Buffer.from(
      JSON.stringify({ sub: user.id, login: "admin", installations: [1], exp: 9999999999 }),
    ).toString("base64url");
    expect(await openSession(`${header}.${forged}.${signature}`, SECRET, NOW)).toBeNull();
  });

  it("expires", async () => {
    const value = await sealSession(user, SECRET, NOW);
    const later = new Date(NOW.getTime() + (SESSION_TTL_SECONDS + 1) * 1000);
    expect(await openSession(value, SECRET, later)).toBeNull();
  });

  it("rejects alg none", async () => {
    const header = Buffer.from(JSON.stringify({ alg: "none" })).toString("base64url");
    const body = Buffer.from(
      JSON.stringify({ sub: "1", login: "x", avatar_url: "", installations: [], exp: 9999999999 }),
    ).toString("base64url");
    expect(await openSession(`${header}.${body}.`, SECRET, NOW)).toBeNull();
  });

  it("rejects a malformed installations claim", async () => {
    const value = await new SignJWT({ login: "x", avatar_url: "", installations: ["1"] })
      .setProtectedHeader({ alg: "HS256" })
      .setSubject("1")
      .setExpirationTime("1h")
      .sign(new TextEncoder().encode(SECRET));
    expect(await openSession(value, SECRET)).toBeNull();
  });

  it("is signed out without a secret", async () => {
    const value = await sealSession(user, SECRET, NOW);
    expect(await openSession(value, "", NOW)).toBeNull();
  });
});

describe("API token", () => {
  // The claims services/api/src/api/auth.py requires.
  it("carries sub, login and installations for five minutes", async () => {
    const token = await apiToken(user, SECRET, NOW);
    expect(decodeProtectedHeader(token).alg).toBe("HS256");
    const claims = decodeJwt(token);
    expect(claims.sub).toBe("583231");
    expect(claims.login).toBe("octocat");
    expect(claims.installations).toEqual([170701175, 42]);
    expect(claims.exp! - claims.iat!).toBe(API_TOKEN_TTL_SECONDS);
    // Nothing the API doesn't need.
    expect(Object.keys(claims).sort()).toEqual(["exp", "iat", "installations", "login", "sub"]);
  });

  it("can't be read as a session", async () => {
    // Different secrets (lib/config.ts), so neither passes for the other.
    const token = await apiToken(user, SECRET, NOW);
    expect(await openSession(token, OTHER, NOW)).toBeNull();
  });
});
