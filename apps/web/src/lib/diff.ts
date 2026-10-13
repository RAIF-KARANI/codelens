/** Reading a unified diff (as `git diff` writes it) into files, hunks and
 * numbered lines, and placing findings on it. Pure, so it's unit-tested. Our
 * own ~120 lines instead of a diff library: the format is small, and this way
 * the placement rule matches the one the GitHub review uses (D109). */

export type LineKind = "add" | "del" | "context";

export type DiffLine = {
  kind: LineKind;
  text: string;
  /** Line number in the old file (null for an added line). */
  oldNo: number | null;
  /** Line number in the new file (null for a deleted line). */
  newNo: number | null;
};

export type Hunk = {
  header: string;
  oldStart: number;
  newStart: number;
  lines: DiffLine[];
};

export type FileStatus = "added" | "deleted" | "modified" | "renamed";

export type DiffFile = {
  oldPath: string | null;
  newPath: string | null;
  status: FileStatus;
  binary: boolean;
  hunks: Hunk[];
};

const HUNK = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/;

export function parseDiff(text: string): DiffFile[] {
  const files: DiffFile[] = [];
  let file: DiffFile | null = null;
  let hunk: Hunk | null = null;
  let oldNo = 0;
  let newNo = 0;
  // Lines the hunk header says are still to come, per side.
  let oldLeft = 0;
  let newLeft = 0;

  for (const line of text.split("\n")) {
    if (line.startsWith("diff --git ")) {
      // a/<old> b/<new>; the ---/+++ lines below give the exact paths.
      const [, a, b] = /^diff --git a\/(.*) b\/(.*)$/.exec(line) ?? [];
      file = { oldPath: a ?? null, newPath: b ?? null, status: "modified", binary: false, hunks: [] };
      files.push(file);
      hunk = null;
      continue;
    }
    if (!file) continue;

    const match = HUNK.exec(line);
    if (match) {
      oldNo = Number(match[1]);
      newNo = Number(match[3]);
      // A missing count means 1 ("@@ -1 +0,0 @@").
      oldLeft = match[2] === undefined ? 1 : Number(match[2]);
      newLeft = match[4] === undefined ? 1 : Number(match[4]);
      hunk = { header: match[5].trim(), oldStart: oldNo, newStart: newNo, lines: [] };
      file.hunks.push(hunk);
      continue;
    }
    if (!hunk || (oldLeft === 0 && newLeft === 0)) {
      // The file's header lines, before its first hunk. (Once a hunk has
      // all its lines, nothing but another hunk or file follows.)
      if (line.startsWith("new file mode")) file.status = "added";
      else if (line.startsWith("deleted file mode")) file.status = "deleted";
      else if (line.startsWith("rename from ")) file.status = "renamed";
      else if (line.startsWith("Binary files ")) file.binary = true;
      else if (line.startsWith("--- ")) file.oldPath = stripPrefix(line.slice(4), "a/");
      else if (line.startsWith("+++ ")) file.newPath = stripPrefix(line.slice(4), "b/");
      continue;
    }
    if (line.startsWith("+")) {
      hunk.lines.push({ kind: "add", text: line.slice(1), oldNo: null, newNo: newNo++ });
      newLeft--;
    } else if (line.startsWith("-")) {
      hunk.lines.push({ kind: "del", text: line.slice(1), oldNo: oldNo++, newNo: null });
      oldLeft--;
    } else if (line.startsWith(" ") || line === "") {
      // An empty line is a blank context line whose leading space was
      // trimmed by an editor or a copy-paste; git apply accepts those too.
      hunk.lines.push({ kind: "context", text: line.slice(1), oldNo: oldNo++, newNo: newNo++ });
      oldLeft--;
      newLeft--;
    }
    // "\ No newline at end of file" isn't a line of either file.
  }
  return files;
}

function stripPrefix(path: string, prefix: string): string | null {
  if (path === "/dev/null") return null;
  return path.startsWith(prefix) ? path.slice(prefix.length) : path;
}

/** The path a finding refers to: findings are about the new version. */
export function filePath(file: DiffFile): string {
  return file.newPath ?? file.oldPath ?? "";
}

export type Range = { lineStart: number; lineEnd: number };

/** Where a finding goes in the diff: after the line `anchor` (a new-file line
 * number), with `lines` the new-file lines it covers there. Like the review
 * posted on GitHub (D109), its range is clipped to the first hunk it
 * overlaps. Null when it overlaps none: it's shown above the file instead. */
export function placeFinding(
  file: DiffFile,
  range: Range,
): { anchor: number; lines: [number, number] } | null {
  for (const hunk of file.hunks) {
    const numbers = hunk.lines.flatMap((l) => (l.newNo === null ? [] : [l.newNo]));
    if (numbers.length === 0) continue;
    const first = numbers[0];
    const last = numbers[numbers.length - 1];
    if (range.lineEnd < first || range.lineStart > last) continue;
    const start = Math.max(range.lineStart, first);
    const end = Math.min(range.lineEnd, last);
    return { anchor: end, lines: [start, end] };
  }
  return null;
}
