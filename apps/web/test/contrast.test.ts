import { describe, expect, it } from "vitest";

import { contrast, over, parseHex, readable } from "@/lib/contrast";

describe("contrast", () => {
  it("is WCAG's ratio: 21 for black on white, 1 for a colour on itself", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#6e56cf", "#6e56cf")).toBeCloseTo(1, 5);
    expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
  });

  it("reads short and long hex", () => {
    expect(parseHex("#fff")).toEqual([255, 255, 255]);
    expect(parseHex("#0a0B0c")).toEqual([10, 11, 12]);
  });

  it("paints a translucent colour over another", () => {
    expect(over("#000000", 0.5, "#ffffff")).toBe("#808080");
    expect(over("#10b981", 0, "#ffffff")).toBe("#ffffff");
  });
});

describe("readable", () => {
  const tinted = ["#ffffff", over("#ef4444", 0.11, "#ffffff")];

  it("keeps a colour that already passes", () => {
    expect(readable("#1f2328", tinted)).toBe("#1f2328");
  });

  it("darkens a colour on light backgrounds until every one passes", () => {
    const fixed = readable("#6e7781", tinted);
    expect(fixed).not.toBe("#6e7781");
    for (const bg of tinted) expect(contrast(fixed, bg)).toBeGreaterThanOrEqual(4.5);
  });

  it("lightens a colour on dark backgrounds", () => {
    const dark = ["#17141f", over("#ef4444", 0.15, "#17141f")];
    const fixed = readable("#6a737d", dark);
    for (const bg of dark) expect(contrast(fixed, bg)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(fixed, "#ffffff")).toBeLessThan(contrast("#6a737d", "#ffffff"));
  });
});
