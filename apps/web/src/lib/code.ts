/** Highlighted code, without the highlighter: which language a path is, and
 * how a diff hunk's lines get their colours. Pure, so it's unit-tested; the
 * highlighting itself is lib/highlight.ts (server only). */

import type { Finding } from "@/lib/api";
import { type DiffFile, filePath, type Hunk } from "@/lib/diff";

/** A run of text and its colour in each theme (absent: the text colour). */
export type Token = { text: string; light?: string; dark?: string };

/** The languages CodeLens reviews (the parser's), by extension. */
const LANGS = {
  py: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "jsx",
  ts: "typescript",
  tsx: "tsx",
} as const;

export type Lang = (typeof LANGS)[keyof typeof LANGS];

export const HIGHLIGHT_LANGS: readonly Lang[] = [...new Set(Object.values(LANGS))];

export function langOf(path: string): Lang | null {
  const dot = path.lastIndexOf(".");
  if (dot < 0 || dot < path.lastIndexOf("/")) return null;
  const ext = path.slice(dot + 1).toLowerCase();
  return ext in LANGS ? LANGS[ext as keyof typeof LANGS] : null;
}

/** Plain lines, for code the highlighter isn't given. */
export function plain(code: string): Token[][] {
  return code.split("\n").map((text) => [{ text }]);
}

/** A hunk as the two files it shows: the old side (context and deleted lines)
 * and the new side (context and added lines). Each is highlighted on its own,
 * so the grammar reads real code, not a mix of both versions. */
export function sides(hunk: Hunk): { old: string; new: string } {
  const old: string[] = [];
  const now: string[] = [];
  for (const line of hunk.lines) {
    if (line.kind !== "add") old.push(line.text);
    if (line.kind !== "del") now.push(line.text);
  }
  return { old: old.join("\n"), new: now.join("\n") };
}

/** Each of the hunk's lines with its tokens, taken from the side it belongs to
 * (context lines from the new side). A side with too few lines (it shouldn't
 * happen) falls back to the plain text. */
export function hunkTokens(hunk: Hunk, oldSide: Token[][], newSide: Token[][]): Token[][] {
  let o = 0;
  let n = 0;
  return hunk.lines.map((line) => {
    let tokens: Token[] | undefined;
    if (line.kind === "add") tokens = newSide[n++];
    else if (line.kind === "del") tokens = oldSide[o++];
    else {
      tokens = newSide[n++];
      o++;
    }
    return tokens ?? [{ text: line.text }];
  });
}

/** The findings in reading order: file by file as the diff lists them, then
 * by line; findings on files outside the diff last. This is the order j/k
 * walks. */
export function findingOrder(files: DiffFile[], findings: Finding[]): Finding[] {
  const fileIndex = new Map(files.map((f, i) => [filePath(f), i]));
  const rank = (f: Finding) => fileIndex.get(f.file_path) ?? files.length;
  return [...findings].sort(
    (a, b) => rank(a) - rank(b) || a.line_start - b.line_start || a.id - b.id,
  );
}
