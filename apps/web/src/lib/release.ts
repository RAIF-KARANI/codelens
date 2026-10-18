import "server-only";

import { cacheLife } from "next/cache";

/** CodeLens Desktop's latest release, for the download page (Phase 11, D179):
 * read from GitHub's API, where the Release workflow publishes it
 * (.github/workflows/release.yml), so the page follows each new release
 * without a deploy. Cached for an hour: the page is the same for everyone, and
 * GitHub allows 60 unauthenticated calls an hour per address. */

export const REPO = "RAIF-KARANI/codelens";
export const RELEASES_URL = `https://github.com/${REPO}/releases/latest`;

export type Release = {
  version: string;
  /** The installer itself, on GitHub. */
  url: string;
  bytes: number;
  publishedAt: string;
  /** The release's page, with its notes. */
  pageUrl: string;
  /** From the release's SHA256SUMS.txt, upper case as Get-FileHash prints it. */
  sha256: string | null;
};

type GitHubRelease = {
  tag_name?: unknown;
  html_url?: unknown;
  published_at?: unknown;
  assets?: { name?: unknown; size?: unknown; browser_download_url?: unknown }[];
};

/** The release as GitHub's API returns it, checked: the installer's asset
 * must be the one the tag names, served from this repository. Null for
 * anything else, and the page falls back to the Releases page. */
export function pickRelease(json: unknown, sums: string | null): Release | null {
  const r = json as GitHubRelease | null;
  const tag = typeof r?.tag_name === "string" ? r.tag_name : "";
  const version = /^v(\d+\.\d+\.\d+)$/.exec(tag)?.[1];
  if (!version || !Array.isArray(r?.assets)) return null;
  const name = `CodeLens-Setup-${version}.exe`;
  const asset = r.assets.find((a) => a.name === name);
  const url = asset?.browser_download_url;
  const prefix = `https://github.com/${REPO}/releases/download/${tag}/`;
  if (typeof url !== "string" || url !== `${prefix}${name}` || typeof asset?.size !== "number") return null;
  const sha = sums?.split(/\r?\n/).map((line) => /^([0-9a-f]{64}) {2}(.+)$/.exec(line.trim())).find((m) => m?.[2] === name)?.[1];
  return {
    version,
    url,
    bytes: asset.size,
    publishedAt: typeof r.published_at === "string" ? r.published_at : "",
    pageUrl: typeof r.html_url === "string" && r.html_url.startsWith(`https://github.com/${REPO}/`) ? r.html_url : RELEASES_URL,
    sha256: sha ? sha.toUpperCase() : null,
  };
}

async function get(url: string, accept: string): Promise<Response | null> {
  try {
    const response = await fetch(url, {
      headers: { Accept: accept, "User-Agent": "codelens-download-page" },
      signal: AbortSignal.timeout(5000),
    });
    return response.ok ? response : null;
  } catch {
    return null;
  }
}

export async function latestRelease(): Promise<Release | null> {
  "use cache";
  cacheLife("hours");
  const response = await get(`https://api.github.com/repos/${REPO}/releases/latest`, "application/vnd.github+json");
  if (!response) return null;
  const json: unknown = await response.json().catch(() => null);
  const sums = await get(`https://github.com/${REPO}/releases/download/${(json as GitHubRelease | null)?.tag_name}/SHA256SUMS.txt`, "text/plain");
  return pickRelease(json, sums ? await sums.text() : null);
}
