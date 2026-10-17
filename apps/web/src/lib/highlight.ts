import "server-only";

import { createHighlighter, type Highlighter } from "shiki";

import {
  HIGHLIGHT_LANGS,
  hunkTokens,
  type Lang,
  langOf,
  plain,
  sides,
  type Token,
} from "@/lib/code";
import { over, readable } from "@/lib/contrast";
import { type DiffFile, filePath } from "@/lib/diff";

/** Syntax highlighting, on the server only (Shiki with GitHub's themes): the
 * browser gets coloured spans and no highlighter. Both themes' colours ride on
 * every token as CSS variables, so the theme toggle needs no re-render. */

const THEMES = { light: "github-light-default", dark: "github-dark-default" } as const;

/** Where code is shown (app/globals.css): the card, the page and muted rows,
 * each plain or under a diff's added or deleted tint. GitHub's colours are
 * made for a plain background, so each token colour is darkened (or, in the
 * dark theme, lightened) just enough to keep 4.5:1 on all of them (D162). */
function surfaces(bases: string[], added: number, deleted: number): string[] {
  return bases.flatMap((b) => [b, over("#10b981", added, b), over("#ef4444", deleted, b)]);
}
const SURFACES = {
  light: surfaces(["#ffffff", "#f8f7fc", "#f1eff7"], 0.13, 0.11),
  dark: surfaces(["#17141f", "#0e0c14", "#1d1a27"], 0.16, 0.15),
};

const readableCache = new Map<string, string>();

function readableToken(color: string | undefined, theme: keyof typeof SURFACES): string | undefined {
  if (!color || !/^#[0-9a-f]{3,8}$/i.test(color)) return color;
  const key = `${theme}${color}`;
  let fixed = readableCache.get(key);
  if (fixed === undefined) {
    fixed = readable(color, SURFACES[theme]);
    readableCache.set(key, fixed);
  }
  return fixed;
}

/** Bigger than this is shown plain: highlighting is linear, but a 1 MB diff
 * (the cap a review stores, D110) isn't worth the CPU on every page view. */
const MAX_CHARS = 200_000;

let highlighter: Promise<Highlighter> | null = null;

function getHighlighter(): Promise<Highlighter> {
  highlighter ??= createHighlighter({
    themes: Object.values(THEMES),
    langs: [...HIGHLIGHT_LANGS],
  });
  return highlighter;
}

/** `code`'s lines as tokens; plain text for an unknown language or one too long. */
export async function highlight(code: string, lang: Lang | null): Promise<Token[][]> {
  if (lang === null || code.length > MAX_CHARS) return plain(code);
  const shiki = await getHighlighter();
  const { tokens } = shiki.codeToTokens(code, { lang, themes: THEMES, defaultColor: false });
  return tokens.map((line) =>
    line.map((t) => ({
      text: t.content,
      light: readableToken(t.htmlStyle?.["--shiki-light"], "light"),
      dark: readableToken(t.htmlStyle?.["--shiki-dark"], "dark"),
    })),
  );
}

/** Every line of a parsed diff as tokens, [file][hunk][line]: each hunk's old
 * and new sides highlighted separately, then mapped back (lib/code.ts). */
export async function highlightDiff(files: DiffFile[]): Promise<Token[][][][]> {
  // The cap is for the whole diff: past it, every file is shown plain.
  const chars = files.reduce(
    (n, f) => n + f.hunks.reduce((m, h) => m + h.lines.reduce((k, l) => k + l.text.length + 1, 0), 0),
    0,
  );
  return Promise.all(
    files.map((file) => {
      const lang = chars > MAX_CHARS ? null : langOf(filePath(file));
      return Promise.all(
        file.hunks.map(async (hunk) => {
          const { old, new: now } = sides(hunk);
          const [oldSide, newSide] = await Promise.all([highlight(old, lang), highlight(now, lang)]);
          return hunkTokens(hunk, oldSide, newSide);
        }),
      );
    }),
  );
}
