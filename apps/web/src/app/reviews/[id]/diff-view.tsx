import { Tag } from "@/components/badges";
import type { Finding } from "@/lib/api";
import { type DiffFile, filePath, placeFinding } from "@/lib/diff";

import { FindingCard, SEVERITY_STYLES } from "./finding-card";

type Placed = { finding: Finding; anchor: number; lines: [number, number] };

/** The reviewed diff, file by file, with each finding shown under the line
 * it ends on (clipped to its hunk, as on GitHub; D109). Findings that touch no
 * changed line are listed above their file; findings on files outside the
 * diff come last. */
export function DiffView({
  files,
  findings,
  names,
}: {
  files: DiffFile[];
  findings: Finding[];
  names: Map<number, string>;
}) {
  const paths = new Set(files.map(filePath));
  const elsewhere = findings.filter((f) => !paths.has(f.file_path));
  return (
    <div className="space-y-4">
      {files.map((file) => (
        <FileDiff
          key={filePath(file)}
          file={file}
          findings={findings.filter((f) => f.file_path === filePath(file))}
          names={names}
          open={files.length <= 8}
        />
      ))}
      {elsewhere.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">On files outside the diff</h3>
          {elsewhere.map((f) => (
            <FindingCard key={f.id} finding={f} names={names} />
          ))}
        </section>
      )}
    </div>
  );
}

function FileDiff({
  file,
  findings,
  names,
  open,
}: {
  file: DiffFile;
  findings: Finding[];
  names: Map<number, string>;
  open: boolean;
}) {
  const placed: Placed[] = [];
  const unplaced: Finding[] = [];
  for (const finding of findings) {
    const spot = placeFinding(file, { lineStart: finding.line_start, lineEnd: finding.line_end });
    if (spot) placed.push({ finding, ...spot });
    else unplaced.push(finding);
  }
  // The most severe finding marks a line covered by several.
  const lineStyle = new Map<number, string>();
  for (const { finding, lines } of [...placed].reverse()) {
    for (let n = lines[0]; n <= lines[1]; n++) {
      lineStyle.set(n, SEVERITY_STYLES[finding.severity].line);
    }
  }
  const path = filePath(file);

  return (
    <details
      open={open || findings.length > 0}
      className="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800"
    >
      <summary className="flex cursor-pointer flex-wrap items-center gap-2 bg-zinc-50 px-3 py-2 text-sm dark:bg-zinc-900">
        <span className="break-all font-mono font-medium">{path}</span>
        {file.status !== "modified" && <Tag>{file.status}</Tag>}
        {findings.length > 0 && (
          <span className="text-xs text-zinc-500">
            {findings.length} finding{findings.length === 1 ? "" : "s"}
          </span>
        )}
      </summary>
      {unplaced.length > 0 && (
        <div className="space-y-2 border-t border-zinc-200 p-3 dark:border-zinc-800">
          <p className="text-xs text-zinc-500">Not on a changed line:</p>
          {unplaced.map((f) => (
            <FindingCard key={f.id} finding={f} names={names} />
          ))}
        </div>
      )}
      {file.binary ? (
        <p className="border-t border-zinc-200 px-3 py-2 text-sm text-zinc-500 dark:border-zinc-800">
          Binary file
        </p>
      ) : (
        // Long lines wrap rather than scroll, so a finding under a line is
        // always fully in view.
        <div className="border-t border-zinc-200 dark:border-zinc-800">
          <table className="w-full table-fixed border-collapse font-mono text-xs leading-5">
            <colgroup>
              <col className="w-12" />
              <col className="w-12" />
              <col className="w-4" />
              <col />
            </colgroup>
            {file.hunks.map((hunk, h) => (
              <tbody key={h}>
                <tr className="bg-sky-50/60 text-zinc-500 dark:bg-sky-950/30">
                  <td colSpan={4} className="px-3 py-1">
                    @@ −{hunk.oldStart} +{hunk.newStart} @@ {hunk.header}
                  </td>
                </tr>
                {hunk.lines.map((line, i) => {
                  const here = line.newNo === null ? [] : placed.filter((p) => p.anchor === line.newNo);
                  const covered = line.newNo !== null ? lineStyle.get(line.newNo) : undefined;
                  return (
                    <LineRows
                      key={i}
                      kind={line.kind}
                      text={line.text}
                      oldNo={line.oldNo}
                      newNo={line.newNo}
                      covered={covered}
                      findings={here.map((p) => p.finding)}
                      names={names}
                    />
                  );
                })}
              </tbody>
            ))}
          </table>
        </div>
      )}
    </details>
  );
}

const KIND_STYLES = {
  add: "bg-emerald-50 dark:bg-emerald-950/40",
  del: "bg-red-50 dark:bg-red-950/40",
  context: "",
} as const;

const MARKERS = { add: "+", del: "−", context: " " } as const;

function LineRows({
  kind,
  text,
  oldNo,
  newNo,
  covered,
  findings,
  names,
}: {
  kind: keyof typeof KIND_STYLES;
  text: string;
  oldNo: number | null;
  newNo: number | null;
  covered: string | undefined;
  findings: Finding[];
  names: Map<number, string>;
}) {
  return (
    <>
      <tr className={KIND_STYLES[kind]}>
        {/* A line a finding covers gets a bar in its severity's colour. */}
        <td className={`select-none px-2 text-right align-top text-zinc-400 ${covered ?? ""}`}>
          {oldNo ?? ""}
        </td>
        <td className="select-none px-2 text-right align-top text-zinc-400">{newNo ?? ""}</td>
        <td className="select-none align-top text-zinc-500">{MARKERS[kind]}</td>
        <td className="whitespace-pre-wrap pr-3 [overflow-wrap:anywhere]">{text}</td>
      </tr>
      {findings.length > 0 && (
        <tr>
          <td colSpan={4} className="space-y-2 bg-zinc-50 px-3 py-2 dark:bg-zinc-900/60">
            {findings.map((f) => (
              <FindingCard key={f.id} finding={f} names={names} />
            ))}
          </td>
        </tr>
      )}
    </>
  );
}
