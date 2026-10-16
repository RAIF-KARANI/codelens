import { describe, expect, it } from "vitest";

import { THEME_KEY, THEME_SCRIPT, parseThemeChoice, resolveTheme } from "@/lib/theme";

describe("theme choice", () => {
  it("keeps light and dark, and reads anything else as system", () => {
    expect(parseThemeChoice("light")).toBe("light");
    expect(parseThemeChoice("dark")).toBe("dark");
    for (const junk of [null, undefined, "", "Dark", "blue", 1]) {
      expect(parseThemeChoice(junk)).toBe("system");
    }
  });

  it("follows the system only when asked to", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });
});

/** Run the inline <head> script against a fake browser and return the theme it
 * set on <html>. `stored` throwing stands for blocked storage. */
function runScript(stored: string | null | Error, systemDark: boolean): string | null {
  const attrs = new Map<string, string>();
  const browser = {
    localStorage: {
      getItem(key: string) {
        if (stored instanceof Error) throw stored;
        return key === THEME_KEY ? stored : null;
      },
    },
    matchMedia: () => ({ matches: systemDark }),
    document: {
      documentElement: { setAttribute: (name: string, value: string) => attrs.set(name, value) },
    },
  };
  new Function("localStorage", "matchMedia", "document", THEME_SCRIPT)(
    browser.localStorage,
    browser.matchMedia,
    browser.document,
  );
  return attrs.get("data-theme") ?? null;
}

describe("the theme script", () => {
  it.each([
    ["light", true, "light"],
    ["dark", false, "dark"],
    [null, true, "dark"],
    [null, false, "light"],
    ["junk", true, "dark"],
  ] as const)("stored %s, system dark %s → %s", (stored, systemDark, theme) => {
    expect(runScript(stored, systemDark)).toBe(theme);
  });

  it("agrees with resolveTheme for every stored value", () => {
    for (const stored of ["light", "dark", null, "junk"]) {
      for (const systemDark of [true, false]) {
        expect(runScript(stored, systemDark)).toBe(
          resolveTheme(parseThemeChoice(stored), systemDark),
        );
      }
    }
  });

  it("falls back to the system when storage is blocked", () => {
    expect(runScript(new Error("SecurityError"), true)).toBe("dark");
    expect(runScript(new Error("SecurityError"), false)).toBe("light");
  });
});
