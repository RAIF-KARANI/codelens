import { afterEach, describe, expect, it, vi } from "vitest";

import { control, ControlError, parseAccounts } from "@/lib/control";

describe("parseAccounts", () => {
  it("takes commas, spaces and new lines", () => {
    expect(parseAccounts("RAIF-KARANI:123\r\nfriend, other  third")).toEqual(["RAIF-KARANI:123", "friend", "other", "third"]);
    expect(parseAccounts("  \n ")).toEqual([]);
  });
});

describe("control", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("refuses to run outside the app", async () => {
    vi.stubEnv("CODELENS_CONTROL_URL", "");
    await expect(control("/settings")).rejects.toMatchObject({ code: "not_configured" });
  });

  it("sends the launch's key, and reads the app's refusal per field", async () => {
    vi.stubEnv("CODELENS_CONTROL_URL", "http://host.docker.internal:5000/");
    vi.stubEnv("CODELENS_CONTROL_KEY", "k".repeat(43));
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      expect(new Headers(init.headers).get("authorization")).toBe(`Bearer ${"k".repeat(43)}`);
      return Response.json(
        { error: { code: "invalid", message: "some settings were refused", details: { reviewsPerHour: "a whole number from 0 to 1000" } } },
        { status: 422 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const error = await control("/settings", { method: "PUT", body: "{}" }).catch((e: unknown) => e);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://host.docker.internal:5000/v1/settings");
    expect(error).toBeInstanceOf(ControlError);
    expect(error).toMatchObject({ status: 422, code: "invalid", details: { reviewsPerHour: "a whole number from 0 to 1000" } });
  });

  it("says when the app doesn't answer", async () => {
    vi.stubEnv("CODELENS_CONTROL_URL", "http://host.docker.internal:5000");
    vi.stubEnv("CODELENS_CONTROL_KEY", "k".repeat(43));
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));
    await expect(control("/settings")).rejects.toMatchObject({ code: "unreachable" });
  });
});
