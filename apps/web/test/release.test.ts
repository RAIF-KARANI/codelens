import { describe, expect, it } from "vitest";

import { pickRelease, RELEASES_URL } from "@/lib/release";

// As GitHub's API returned v0.1.0 (2026-10-18), trimmed.
const BASE = "https://github.com/RAIF-KARANI/codelens/releases/download/v0.1.0/";
const RELEASE = {
  tag_name: "v0.1.0",
  html_url: "https://github.com/RAIF-KARANI/codelens/releases/tag/v0.1.0",
  published_at: "2026-10-18T07:49:51Z",
  assets: [
    { name: "CodeLens-Setup-0.1.0.exe", size: 111544913, browser_download_url: `${BASE}CodeLens-Setup-0.1.0.exe` },
    { name: "latest.yml", size: 345, browser_download_url: `${BASE}latest.yml` },
  ],
};
const SUMS = "45918d86298914a19e16fc9bfa1148eb549c8f50eb5bcf2f68f64416b031bd5a  CodeLens-Setup-0.1.0.exe\n";

describe("pickRelease", () => {
  it("finds the installer, its size and its checksum", () => {
    expect(pickRelease(RELEASE, SUMS)).toEqual({
      version: "0.1.0",
      url: `${BASE}CodeLens-Setup-0.1.0.exe`,
      bytes: 111544913,
      publishedAt: "2026-10-18T07:49:51Z",
      pageUrl: RELEASE.html_url,
      sha256: "45918D86298914A19E16FC9BFA1148EB549C8F50EB5BCF2F68F64416B031BD5A",
    });
  });

  it("does without the checksum file", () => {
    expect(pickRelease(RELEASE, null)?.sha256).toBeNull();
    expect(pickRelease(RELEASE, "garbage")?.sha256).toBeNull();
  });

  it("offers nothing that isn't this repository's installer for this tag", () => {
    const asset = RELEASE.assets[0]!;
    const withAsset = (patch: object) => ({ ...RELEASE, assets: [{ ...asset, ...patch }] });
    expect(pickRelease(withAsset({ browser_download_url: "https://evil.example/CodeLens-Setup-0.1.0.exe" }), SUMS)).toBeNull();
    expect(pickRelease(withAsset({ browser_download_url: `${BASE.replace("v0.1.0", "v0.0.9")}CodeLens-Setup-0.1.0.exe` }), SUMS)).toBeNull();
    expect(pickRelease({ ...RELEASE, tag_name: "nightly" }, SUMS)).toBeNull();
    expect(pickRelease({ ...RELEASE, assets: [RELEASE.assets[1]] }, SUMS)).toBeNull();
    expect(pickRelease(null, SUMS)).toBeNull();
    expect(pickRelease({ message: "API rate limit exceeded" }, null)).toBeNull();
  });

  it("links the Releases page if the release's own page looks wrong", () => {
    expect(pickRelease({ ...RELEASE, html_url: "https://evil.example/" }, SUMS)?.pageUrl).toBe(RELEASES_URL);
  });
});
