import { describe, expect, it } from "vitest";

import type { Repo, ReviewSummary } from "@/lib/api";
import { HEAD_SCRIPT } from "@/lib/head-script";
import { isActive, sidebarRepos } from "@/lib/nav";
import { paletteData, reviewLabel } from "@/lib/palette";
import { SIDEBAR_KEY } from "@/lib/sidebar";
import { THEME_KEY } from "@/lib/theme";

function repo(id: number, full_name: string, last_review_at: string | null): Repo {
  return {
    id,
    full_name,
    last_review_at,
    source: "github",
    default_branch: "main",
    status: "ready",
    last_indexed_sha: "abc",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  };
}

function review(id: number, created_at: string, extra: Partial<ReviewSummary> = {}): ReviewSummary {
  return {
    id,
    created_at,
    repo_id: 1,
    source: "github",
    pr_number: id,
    title: `Change ${id}`,
    base_sha: null,
    head_sha: "def",
    status: "completed",
    model: "qwen3:8b",
    started_at: null,
    finished_at: null,
    error: null,
    github_review_id: null,
    counts: { critical: 0, warning: 0, suggestion: 0 },
    ...extra,
  };
}

describe("isActive", () => {
  const repos = { match: ["/repos"] };
  const overview = { match: ["/overview"] };

  it("owns its prefixes and everything under them", () => {
    expect(isActive("/repos", repos)).toBe(true);
    expect(isActive("/repos/3/indexing", repos)).toBe(true);
    expect(isActive("/reviews/1386/graph", { match: ["/reviews"] })).toBe(true);
  });

  it("lets the overview own only its own page", () => {
    expect(isActive("/overview", overview)).toBe(true);
    expect(isActive("/", overview)).toBe(false);
    expect(isActive("/repos", overview)).toBe(false);
    expect(isActive("/reviews/3", overview)).toBe(false);
  });

  it("matches whole segments only", () => {
    expect(isActive("/reposx", repos)).toBe(false);
    expect(isActive("/", repos)).toBe(false);
    expect(isActive("/repos/3", { match: ["/repos/3"] })).toBe(true);
    expect(isActive("/repos/31", { match: ["/repos/3"] })).toBe(false);
  });
});

describe("sidebarRepos", () => {
  it("lists the most recently reviewed first, then by name, up to the limit", () => {
    const listed = sidebarRepos(
      [
        repo(1, "b/never", null),
        repo(2, "a/old", "2026-10-10T00:00:00Z"),
        repo(3, "a/new", "2026-10-15T00:00:00Z"),
        repo(4, "a/never", null),
      ],
      3,
    );
    expect(listed.map((r) => r.full_name)).toEqual(["a/new", "a/old", "a/never"]);
  });
});

describe("the palette's data", () => {
  it("names a review by its title, else its PR, else its number", () => {
    expect(reviewLabel({ id: 7, title: "Fix it", pr_number: 3 })).toBe("Fix it");
    expect(reviewLabel({ id: 7, title: null, pr_number: 3 })).toBe("PR #3");
    expect(reviewLabel({ id: 7, title: null, pr_number: null })).toBe("Review #7");
  });

  it("keeps only what the palette shows, in the API's order", () => {
    const a = repo(1, "o/a", "2026-10-15T00:00:00Z");
    const data = paletteData(
      [a],
      [
        { ...review(10, "2026-10-15T10:00:00Z"), repo_full_name: "o/a" },
        { ...review(9, "2026-10-12T00:00:00Z", { title: null }), repo_full_name: "o/b" },
      ],
    );
    expect(data.reviews).toEqual([
      {
        id: 10,
        label: "Change 10",
        repo: "o/a",
        pr_number: 10,
        status: "completed",
        created_at: "2026-10-15T10:00:00Z",
      },
      {
        id: 9,
        label: "PR #9",
        repo: "o/b",
        pr_number: 9,
        status: "completed",
        created_at: "2026-10-12T00:00:00Z",
      },
    ]);
    expect(data.repos).toEqual([{ id: 1, full_name: "o/a", status: "ready" }]);
  });
});

describe("the head script", () => {
  /** Run it against a fake browser: what it sets on <html>, or what it threw. */
  function run(stored: Record<string, string>, systemDark = false) {
    const attrs = new Map<string, string>();
    new Function("localStorage", "matchMedia", "document", HEAD_SCRIPT)(
      { getItem: (key: string) => stored[key] ?? null },
      () => ({ matches: systemDark }),
      { documentElement: { setAttribute: (name: string, value: string) => attrs.set(name, value) } },
    );
    return Object.fromEntries(attrs);
  }

  it("applies the theme and the collapsed sidebar together", () => {
    expect(run({ [THEME_KEY]: "dark", [SIDEBAR_KEY]: "collapsed" })).toEqual({
      "data-theme": "dark",
      "data-sidebar": "collapsed",
    });
  });

  it("leaves the sidebar open unless it was collapsed", () => {
    expect(run({}, true)).toEqual({ "data-theme": "dark" });
  });
});
