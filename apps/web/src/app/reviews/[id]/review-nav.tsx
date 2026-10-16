"use client";

import { FileIcon, ListFilterIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  createContext,
  use,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { Kbd } from "@/components/ui/kbd";
import type { Severity } from "@/lib/api";
import { cn } from "@/lib/utils";

import { useEvidence } from "./evidence";

/** What the panel and the keys need of a finding (the server sends these, in
 * reading order: lib/code.ts findingOrder). */
export type NavFinding = {
  id: number;
  severity: Severity;
  category: string;
  title: string;
  file_path: string;
  line_start: number;
  dropped: boolean;
  /** Its first piece of evidence, for `e`. */
  evidence: { nodeId: number; label: string } | null;
};

export type NavFile = { path: string; status: string; findings: number };

type Nav = {
  isShown: (id: number) => boolean;
  selected: number | null;
  select: (id: number) => void;
};

const NavContext = createContext<Nav>({ isShown: () => true, selected: null, select: () => {} });

const SEVERITIES: { key: Severity; dot: string }[] = [
  { key: "critical", dot: "bg-critical" },
  { key: "warning", dot: "bg-warning" },
  { key: "suggestion", dot: "bg-suggestion" },
];

/** Bring a finding's card into view, focused (it has tabIndex -1), and open
 * its file if it was folded. */
function reveal(id: number) {
  const card = document.getElementById(`finding-${id}`);
  if (!card) return;
  const details = card.closest("details");
  if (details && !details.open) details.open = true;
  card.scrollIntoView({ block: "center", behavior: "smooth" });
  card.focus({ preventScroll: true });
}

/** The review's Diff tab around its content: the findings and files panel,
 * the filters, and the keys (j/k next and previous finding, e its evidence,
 * g the graph). */
export function ReviewNav({
  reviewId,
  findings,
  files,
  details,
  children,
}: {
  reviewId: number;
  findings: NavFinding[];
  files: NavFile[];
  /** The run details (timeline, changed code, tokens), under the panel. */
  details: ReactNode;
  children: ReactNode;
}) {
  const [severities, setSeverities] = useState<Set<Severity>>(() => new Set());
  const [categories, setCategories] = useState<Set<string>>(() => new Set());
  const [selected, setSelected] = useState<number | null>(null);
  const openEvidence = useEvidence();
  const router = useRouter();

  const allCategories = useMemo(
    () => [...new Set(findings.map((f) => f.category))].sort(),
    [findings],
  );
  // An empty set means "all": a filter only narrows once something is picked.
  const shown = useMemo(
    () =>
      findings.filter(
        (f) =>
          (severities.size === 0 || severities.has(f.severity)) &&
          (categories.size === 0 || categories.has(f.category)),
      ),
    [findings, severities, categories],
  );
  const shownIds = useMemo(() => new Set(shown.map((f) => f.id)), [shown]);

  const select = useCallback((id: number) => {
    setSelected(id);
    reveal(id);
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable=true], [role=dialog]")) return;
      const at = shown.findIndex((f) => f.id === selected);
      const current = at >= 0 ? shown[at] : undefined;
      if (event.key === "j" || event.key === "k") {
        if (shown.length === 0) return;
        event.preventDefault();
        const step = event.key === "j" ? 1 : -1;
        const next = at < 0 ? (step > 0 ? 0 : shown.length - 1) : (at + step + shown.length) % shown.length;
        select(shown[next].id);
      } else if (event.key === "e" && current?.evidence) {
        event.preventDefault();
        openEvidence(current.evidence);
      } else if (event.key === "g" && current) {
        event.preventDefault();
        router.push(`/reviews/${reviewId}/graph?finding=${current.id}`);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shown, selected, select, openEvidence, router, reviewId]);

  const nav = useMemo<Nav>(
    () => ({ isShown: (id) => shownIds.has(id), selected, select }),
    [shownIds, selected, select],
  );

  function toggle<T>(set: Set<T>, value: T, update: (next: Set<T>) => void) {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    update(next);
  }

  return (
    <NavContext value={nav}>
      <div className="grid gap-6 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <aside className="min-w-0 space-y-4 lg:sticky lg:top-6 lg:max-h-[calc(100svh-3rem)] lg:self-start lg:overflow-y-auto lg:pr-1">
          <section aria-labelledby="findings-heading" className="rounded-xl border border-border bg-card">
            <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2.5">
              <h2 id="findings-heading" className="text-sm font-semibold">
                Findings{" "}
                <span className="font-normal text-muted-foreground">
                  {shown.length === findings.length ? findings.length : `${shown.length} of ${findings.length}`}
                </span>
              </h2>
              <ListFilterIcon className="size-4 text-muted-foreground" aria-hidden="true" />
            </div>
            {findings.length > 0 && (
              <div className="space-y-2 border-b border-border px-3 py-2.5" role="group" aria-label="Filters">
                <div className="flex flex-wrap gap-1.5">
                  {SEVERITIES.map(({ key, dot }) => {
                    const n = findings.filter((f) => f.severity === key).length;
                    if (n === 0) return null;
                    const on = severities.has(key);
                    return (
                      <button
                        key={key}
                        type="button"
                        aria-pressed={on}
                        onClick={() => toggle(severities, key, setSeverities)}
                        className={cn(
                          "flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs transition-colors",
                          on
                            ? "border-primary bg-accent text-accent-foreground"
                            : "border-input text-muted-foreground hover:border-primary/60 hover:text-foreground",
                        )}
                      >
                        <span className={cn("size-1.5 rounded-full", dot)} aria-hidden="true" />
                        {key} <span className="tabular-nums">{n}</span>
                      </button>
                    );
                  })}
                </div>
                {allCategories.length > 1 && (
                  <div className="flex flex-wrap gap-1.5">
                    {allCategories.map((category) => {
                      const on = categories.has(category);
                      return (
                        <button
                          key={category}
                          type="button"
                          aria-pressed={on}
                          onClick={() => toggle(categories, category, setCategories)}
                          className={cn(
                            "rounded-md border px-1.5 py-0.5 text-xs transition-colors",
                            on
                              ? "border-primary bg-accent text-accent-foreground"
                              : "border-input text-muted-foreground hover:border-primary/60 hover:text-foreground",
                          )}
                        >
                          {category}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
            {findings.length === 0 ? (
              <p className="px-3 py-4 text-sm text-muted-foreground">Nothing found in this change.</p>
            ) : shown.length === 0 ? (
              <p className="px-3 py-4 text-sm text-muted-foreground">No finding matches the filters.</p>
            ) : (
              <ol className="max-h-80 overflow-y-auto py-1">
                {shown.map((f) => (
                  <li key={f.id}>
                    <button
                      type="button"
                      onClick={() => select(f.id)}
                      aria-current={f.id === selected ? "true" : undefined}
                      className={cn(
                        "flex w-full gap-2 px-3 py-1.5 text-left transition-colors hover:bg-muted",
                        f.id === selected && "bg-accent hover:bg-accent",
                      )}
                    >
                      <span
                        className={cn(
                          "mt-1.5 size-1.5 shrink-0 rounded-full",
                          SEVERITIES.find((s) => s.key === f.severity)?.dot,
                        )}
                        aria-hidden="true"
                      />
                      <span className="min-w-0">
                        <span className={cn("block text-sm leading-snug", f.dropped && "text-muted-foreground line-through")}>
                          <span className="sr-only">{f.severity}: </span>
                          {f.title}
                        </span>
                        <span className="block truncate font-mono text-[11px] text-muted-foreground">
                          {f.file_path.split("/").at(-1)}:{f.line_start}
                          {f.dropped && " · dropped"}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ol>
            )}
            {findings.length > 0 && (
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
                <span>
                  <Kbd>j</Kbd> <Kbd>k</Kbd> move
                </span>
                <span>
                  <Kbd>e</Kbd> evidence
                </span>
                <span>
                  <Kbd>g</Kbd> graph
                </span>
              </p>
            )}
          </section>

          {files.length > 0 && (
            <section aria-labelledby="files-heading" className="rounded-xl border border-border bg-card">
              <h2 id="files-heading" className="border-b border-border px-3 py-2.5 text-sm font-semibold">
                Files <span className="font-normal text-muted-foreground">{files.length}</span>
              </h2>
              <ul className="max-h-60 overflow-y-auto py-1">
                {files.map((file) => (
                  <li key={file.path}>
                    <a
                      href={`#file:${file.path}`}
                      onClick={(event) => {
                        const target = document.getElementById(`file:${file.path}`);
                        if (target instanceof HTMLDetailsElement) target.open = true;
                        if (target) {
                          event.preventDefault();
                          target.scrollIntoView({ block: "start", behavior: "smooth" });
                        }
                      }}
                      title={file.path}
                      className="flex items-center gap-2 px-3 py-1 text-xs hover:bg-muted"
                    >
                      <FileIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                      <span className="min-w-0 flex-1 truncate font-mono">{file.path}</span>
                      {file.findings > 0 && (
                        <span className="rounded-full bg-accent px-1.5 text-[11px] tabular-nums text-accent-foreground">
                          {file.findings}
                        </span>
                      )}
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {details}
        </aside>
        <div className="min-w-0">{children}</div>
      </div>
    </NavContext>
  );
}

/** Around each finding card in the diff: hidden when the filters leave it out,
 * ringed while it's the selected one. */
export function FindingSlot({ id, children }: { id: number; children: ReactNode }) {
  const { isShown, selected } = use(NavContext);
  if (!isShown(id)) return null;
  return (
    <div className={cn("rounded-md transition-shadow", selected === id && "ring-2 ring-primary/70 ring-offset-2 ring-offset-background")}>
      {children}
    </div>
  );
}
