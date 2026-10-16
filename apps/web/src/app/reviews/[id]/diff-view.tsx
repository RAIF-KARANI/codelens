import { Tag } from "@/components/badges";
import { CodeLine } from "@/components/code-line";
import type { Finding } from "@/lib/api";
import type { Token } from "@/lib/code";
import { type DiffFile, filePath, placeFinding } from "@/lib/diff";

import { FindingCard, SEVERITY_STYLES } from "./finding-card";
import { FindingSlot } from "./review-nav";

type Placed = { finding: Finding; anchor: number; lines: [number, number] };

/** The reviewed diff, file by file, highlighted (`tokens`, [file][hunk][line],
 * from lib/highlight.ts), with each finding shown under the line it ends on
 * (clipped to its hunk, as on GitHub; D109). Findings that touch no changed
 * line are listed above their file; findings on files outside the diff come
 * last. Every card sits in a FindingSlot, so the panel's filters and keys
 * reach it. */
export function DiffView({
  files,
  tokens,
  findings,
  names,
}: {
  files: DiffFile[];
  tokens: Token[][][][];
  findings: Finding[];
  names: Map<number, string>;
}) {
  const paths = new Set(files.map(filePath));
  const elsewhere = findings.filter((f) => !paths.has(f.file_path));
  return (
    <div className="space-y-4">
      {files.map((file, i) => (
        <FileDiff
          key={filePath(file)}
          file={file}
          tokens={tokens[i]}
          findings={findings.filter((f) => f.file_path === filePath(file))}
          names={names}
          open={files.length <= 8}
        />
      ))}
      {elsewhere.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">On files outside the diff</h3>
          {elsewhere.map((f) => (
            <FindingSlot key={f.id} id={f.id}>
              <FindingCard finding={f} names={names} />
            </FindingSlot>
          ))}
        </section>
      )}
    </div>
  );
}

function FileDiff({
  file,
  tokens,
  findings,
  names,
  open,
}: {
  file: DiffFile;
  tokens: Token[][][];
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
      id={`file:${path}`}
      open={open || findings.length > 0}
      className="scroll-mt-6 overflow-hidden rounded-lg border border-border bg-card"
    >
      <summary className="flex cursor-pointer flex-wrap items-center gap-2 bg-muted px-3 py-2 text-sm">
        <span className="break-all font-mono font-medium">{path}</span>
        {file.status !== "modified" && <Tag>{file.status}</Tag>}
        {findings.length > 0 && (
          <span className="text-xs text-muted-foreground">
            {findings.length} finding{findings.length === 1 ? "" : "s"}
          </span>
        )}
      </summary>
      {unplaced.length > 0 && (
        <div className="space-y-2 border-t border-border p-3">
          <p className="text-xs text-muted-foreground">Not on a changed line:</p>
          {unplaced.map((f) => (
            <FindingSlot key={f.id} id={f.id}>
              <FindingCard finding={f} names={names} />
            </FindingSlot>
          ))}
        </div>
      )}
      {file.binary ? (
        <p className="border-t border-border px-3 py-2 text-sm text-muted-foreground">
          Binary file
        </p>
      ) : (
        // Long lines wrap rather than scroll, so a finding under a line is
        // always fully in view.
        <div className="border-t border-border">
          <table className="w-full table-fixed border-collapse font-mono text-xs leading-5">
            <colgroup>
              <col className="w-12" />
              <col className="w-12" />
              <col className="w-4" />
              <col />
            </colgroup>
            {file.hunks.map((hunk, h) => (
              <tbody key={h}>
                <tr className="bg-accent/50 text-muted-foreground">
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
                      tokens={tokens[h]?.[i] ?? [{ text: line.text }]}
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
  add: "bg-added-soft",
  del: "bg-deleted-soft",
  context: "",
} as const;

const MARKERS = { add: "+", del: "−", context: " " } as const;

function LineRows({
  kind,
  tokens,
  oldNo,
  newNo,
  covered,
  findings,
  names,
}: {
  kind: keyof typeof KIND_STYLES;
  tokens: Token[];
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
        <td className={`select-none px-2 text-right align-top text-muted-foreground ${covered ?? ""}`}>
          {oldNo ?? ""}
        </td>
        <td className="select-none px-2 text-right align-top text-muted-foreground">{newNo ?? ""}</td>
        <td className="select-none align-top text-muted-foreground">{MARKERS[kind]}</td>
        <td className="whitespace-pre-wrap pr-3 [overflow-wrap:anywhere]">
          <CodeLine tokens={tokens} />
        </td>
      </tr>
      {findings.length > 0 && (
        <tr>
          <td colSpan={4} className="space-y-2 bg-muted/60 px-3 py-2">
            {findings.map((f) => (
              <FindingSlot key={f.id} id={f.id}>
                <FindingCard finding={f} names={names} />
              </FindingSlot>
            ))}
          </td>
        </tr>
      )}
    </>
  );
}
