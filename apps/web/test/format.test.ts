import { describe, expect, it } from "vitest";

import {
  formatBytes,
  dayTitle,
  formatCount,
  formatDuration,
  formatMs,
  formatPercent,
  shortSha,
  weekday,
} from "@/lib/format";
import { parseChoice, parseCursor, parseId, withQuery } from "@/lib/params";

describe("formatDuration", () => {
  const at = (s: number) => new Date(Date.UTC(2026, 9, 13, 12, 0, 0) + s * 1000).toISOString();

  it.each([
    [0.85, "850 ms"],
    [12.4, "12 s"],
    [59.6, "1 min 00 s"],
    [185, "3 min 05 s"],
    [3725, "1 h 02 min"],
  ])("%s s → %s", (seconds, text) => {
    expect(formatDuration(at(0), at(seconds))).toBe(text);
  });

  it("is null until finished, or for a clock that went backwards", () => {
    expect(formatDuration(at(0), null)).toBeNull();
    expect(formatDuration(null, at(1))).toBeNull();
    expect(formatDuration(at(5), at(1))).toBeNull();
  });
});

describe("formatMs", () => {
  it("is formatDuration's rule for a length of time", () => {
    expect(formatMs(26_180)).toBe("26 s");
    expect(formatMs(195_000)).toBe("3 min 15 s");
    expect(formatMs(-1)).toBeNull();
    expect(formatMs(Number.NaN)).toBeNull();
  });
});

describe("UTC days", () => {
  it("names the API's days in UTC, whatever the reader's zone", () => {
    expect(weekday("2026-10-12")).toBe("Mon");
    expect(dayTitle("2026-10-12")).toBe("Mon, Oct 12");
    expect(weekday("2026-10-16")).toBe("Fri");
  });
});

describe("small formats", () => {
  it("shortens a SHA", () => {
    expect(shortSha("0c1050e9a1b2c3d4")).toBe("0c1050e");
    expect(shortSha(null)).toBe("—");
  });

  it("formats counts and fractions, with a dash when the indexer didn't say", () => {
    expect(formatCount(6077)).toBe("6,077");
    expect(formatCount(undefined)).toBe("—");
    expect(formatPercent(0.2184)).toBe("22%");
    expect(formatPercent(undefined)).toBe("—");
  });
});

describe("URL parameters", () => {
  it("accepts only positive integer ids", () => {
    expect(parseId("42")).toBe(42);
    for (const bad of ["0", "-1", "01", "1.5", "1e3", "abc", "", "99999999999999999"]) {
      expect(parseId(bad)).toBeNull();
    }
  });

  it("reads a cursor, ignoring junk", () => {
    expect(parseCursor("17")).toBe(17);
    expect(parseCursor(["17", "18"])).toBe(17);
    expect(parseCursor("x")).toBeUndefined();
    expect(parseCursor(undefined)).toBeUndefined();
  });

  it("accepts only the listed choices", () => {
    const statuses = ["completed", "failed"] as const;
    expect(parseChoice("failed", statuses)).toBe("failed");
    expect(parseChoice("FAILED", statuses)).toBeUndefined();
    expect(parseChoice("x&cursor=1", statuses)).toBeUndefined();
  });

  it("builds links without unset parameters", () => {
    expect(withQuery("/repos/3", {})).toBe("/repos/3");
    expect(withQuery("/repos/3", { status: undefined, cursor: 9 })).toBe("/repos/3?cursor=9");
    expect(withQuery("/repos/3", { status: "failed", cursor: 9 })).toBe(
      "/repos/3?status=failed&cursor=9",
    );
  });
});

describe("formatBytes", () => {
  it("reads like a file manager", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512 * 1024)).toBe("512 KB");
    expect(formatBytes(37.2 * 1024 ** 2)).toBe("37.2 MB");
    expect(formatBytes(1_584_629_376)).toBe("1.48 GB");
    expect(formatBytes(174.1 * 1024 ** 3)).toBe("174 GB");
    expect(formatBytes(null)).toBe("—");
  });
});
