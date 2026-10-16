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
import { type DiffFile, filePath } from "@/lib/diff";

/** Syntax highlighting, on the server only (Shiki with GitHub's themes): the
 * browser gets coloured spans and no highlighter. Both themes' colours ride on
 * every token as CSS variables, so the theme toggle needs no re-render. */

const THEMES = { light: "github-light", dark: "github-dark" } as const;

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
      light: t.htmlStyle?.["--shiki-light"],
      dark: t.htmlStyle?.["--shiki-dark"],
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
