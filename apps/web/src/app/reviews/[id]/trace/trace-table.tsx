"use client";

import { ArrowDownIcon, ArrowUpDownIcon, ArrowUpIcon, SearchIcon, StarIcon } from "lucide-react";
import { useMemo, useState } from "react";

import type { TraceItem } from "@/lib/api";
import {
  FIRST_DIR,
  filterRows,
  type Outcome,
  type SortDir,
  type SortKey,
  sortRows,
  traceRows,
} from "@/lib/trace";
import { cn } from "@/lib/utils";

import { EvidenceButton } from "../evidence";

const OUTCOMES: { kind: Outcome["kind"]; label: string; style: string }[] = [
  { kind: "included", label: "shown in full", style: "text-added" },
  { kind: "signature", label: "signature only", style: "text-warning" },
  { kind: "excluded", label: "not shown", style: "text-muted-foreground" },
];

const OUTCOME_STYLES = Object.fromEntries(OUTCOMES.map((o) => [o.kind, o.style])) as Record<
  Outcome["kind"],
  string
>;

/** One chunk's candidates: what retrieval found, for which changed code, how
 * it ranked, and whether the model was shown it (and if not, why). Sortable by
 * rank, name, similarity and tokens; filterable by outcome, by the changed
 * code it was found for, and by name. */
export function TraceTable({ items, cited }: { items: TraceItem[]; cited: number[] }) {
  const rows = useMemo(() => traceRows(items), [items]);
  const seeds = useMemo(() => [...new Set(rows.flatMap((r) => r.forSeeds))].sort(), [rows]);
  const citedSet = useMemo(() => new Set(cited), [cited]);
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: "rank", dir: "asc" });
  const [outcomes, setOutcomes] = useState<Set<Outcome["kind"]>>(() => new Set());
  const [seed, setSeed] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const shown = useMemo(
    () => sortRows(filterRows(rows, { outcomes, seed, query }), sort.key, sort.dir),
    [rows, outcomes, seed, query, sort],
  );

  function sortBy(key: SortKey) {
    setSort((current) =>
      current.key === key
        ? { key, dir: current.dir === "asc" ? "desc" : "asc" }
        : { key, dir: FIRST_DIR[key] },
    );
  }

  function toggleOutcome(kind: Outcome["kind"]) {
    setOutcomes((current) => {
      const next = new Set(current);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }

  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5" role="group" aria-label="Filters">
        <label className="relative flex items-center">
          <SearchIcon className="pointer-events-none absolute left-2 size-3.5 text-muted-foreground" aria-hidden="true" />
          <span className="sr-only">Filter candidates by name</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter by name"
            className="h-7 w-44 rounded-md border border-input bg-background pr-2 pl-7 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          />
        </label>
        {seeds.length > 1 && (
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            For
            <select
              value={seed ?? ""}
              onChange={(event) => setSeed(event.target.value || null)}
              className="h-7 max-w-56 rounded-md border border-input bg-background px-1.5 font-mono text-xs text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
            >
              <option value="">all changed code</option>
              {seeds.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="flex flex-wrap gap-1.5">
          {OUTCOMES.map(({ kind, label }) => {
            const n = rows.filter((r) => r.outcome.kind === kind).length;
            if (n === 0) return null;
            const on = outcomes.has(kind);
            return (
              <button
                key={kind}
                type="button"
                aria-pressed={on}
                onClick={() => toggleOutcome(kind)}
                className={cn(
                  "rounded-full border px-2 py-0.5 text-xs transition-colors",
                  on
                    ? "border-primary bg-accent text-accent-foreground"
                    : "border-input text-muted-foreground hover:border-primary/60 hover:text-foreground",
                )}
              >
                {label} <span className="tabular-nums">{n}</span>
              </button>
            );
          })}
        </div>
        <span className="ml-auto text-xs text-muted-foreground" aria-live="polite">
          {shown.length === rows.length ? `${rows.length} candidates` : `${shown.length} of ${rows.length}`}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full whitespace-nowrap text-left text-sm">
          <thead className="bg-muted text-xs text-muted-foreground">
            <tr>
              <SortHeader
                label="Rank"
                sortKey="rank"
                sort={sort}
                onSort={sortBy}
                hint="Each candidate's rank among those found for its changed code, so several can be 1"
              />
              <SortHeader label="Candidate" sortKey="name" sort={sort} onSort={sortBy} />
              <th className="px-3 py-2 font-medium">For</th>
              <SortHeader label="Similarity" sortKey="similarity" sort={sort} onSort={sortBy} />
              <th className="px-3 py-2 font-medium">Outcome</th>
              <SortHeader label="Tokens" sortKey="tokens" sort={sort} onSort={sortBy} align="right" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {shown.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-sm text-muted-foreground">
                  No candidate matches the filters.
                </td>
              </tr>
            ) : (
              shown.map((row) => {
                const { item } = row;
                const isCited = item.node_id !== null && citedSet.has(item.node_id);
                return (
                  <tr key={item.label} className={cn("transition-colors hover:bg-muted/40", !item.included && "text-muted-foreground")}>
                    <td className="px-3 py-1.5 tabular-nums">{row.rank ?? "—"}</td>
                    <td className="max-w-md px-3 py-1.5">
                      <span className="flex items-center gap-1.5">
                        {isCited && (
                          <StarIcon className="size-3.5 shrink-0 fill-warning text-warning" aria-label="Cited as evidence by a finding" />
                        )}
                        {item.node_id !== null ? (
                          <EvidenceButton nodeId={item.node_id} label={item.qualified_name} />
                        ) : (
                          <span className="font-mono text-xs" title="Deleted by a later re-index">
                            {item.qualified_name}
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="max-w-56 px-3 py-1.5">
                      <span className="block truncate font-mono text-[11px] text-muted-foreground" title={row.forSeeds.join("\n")}>
                        {row.forSeeds.length === 0
                          ? "—"
                          : row.forSeeds.map((s) => s.split(".").at(-1)).join(", ")}
                      </span>
                    </td>
                    <td className="px-3 py-1.5">
                      {row.similarity !== null ? <SimilarityBar value={row.similarity} /> : "—"}
                    </td>
                    <td className={cn("px-3 py-1.5 text-xs", OUTCOME_STYLES[row.outcome.kind])}>{row.outcome.text}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {item.included ? item.token_cost.toLocaleString("en") : "—"}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SortHeader({
  label,
  sortKey,
  sort,
  onSort,
  align = "left",
  hint,
}: {
  label: string;
  sortKey: SortKey;
  sort: { key: SortKey; dir: SortDir };
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
  /** What the column means, when its name alone doesn't say. */
  hint?: string;
}) {
  const active = sort.key === sortKey;
  const Icon = !active ? ArrowUpDownIcon : sort.dir === "asc" ? ArrowUpIcon : ArrowDownIcon;
  return (
    <th
      className={cn("px-3 py-2 font-medium", align === "right" && "text-right")}
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        title={hint}
        className={cn(
          "inline-flex items-center gap-1 rounded-sm outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60",
          active && "text-foreground",
        )}
      >
        {label}
        <Icon className="size-3" aria-hidden="true" />
      </button>
    </th>
  );
}

function SimilarityBar({ value }: { value: number }) {
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-accent">
        <span
          className="block h-full rounded-full bg-chart-1"
          style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
        />
      </span>
      <span className="text-xs tabular-nums">{value.toFixed(3)}</span>
    </span>
  );
}
