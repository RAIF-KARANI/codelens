"use client";

import { createContext, use, useEffect, useState } from "react";

import { CodeLine } from "@/components/code-line";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import type { CodeNode } from "@/lib/api";
import type { Token } from "@/lib/code";

/** The evidence sheet: a finding's evidence buttons open the code the model
 * was shown for it, fetched on demand from /api/nodes/[id]. One drawer for
 * the page, opened through context. */

type Opened = { nodeId: number; label: string } | null;

const EvidenceContext = createContext<(opened: Opened) => void>(() => {});

export function EvidenceProvider({ children }: { children: React.ReactNode }) {
  const [opened, setOpened] = useState<Opened>(null);
  return (
    <EvidenceContext value={setOpened}>
      {children}
      {opened && (
        <EvidenceDrawer key={opened.nodeId} {...opened} onClose={() => setOpened(null)} />
      )}
    </EvidenceContext>
  );
}

/** Opens the drawer on a node, e.g. from a graph node's click. */
export function useEvidence(): (opened: Opened) => void {
  return use(EvidenceContext);
}

export function EvidenceButton({ nodeId, label }: { nodeId: number; label: string }) {
  const open = useEvidence();
  return (
    <button
      type="button"
      onClick={() => open({ nodeId, label })}
      className="max-w-full truncate rounded border border-input px-1.5 py-0.5 font-mono text-xs text-foreground/85 outline-none hover:border-primary/60 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/60"
      title={label}
    >
      {label}
    </button>
  );
}

type Loaded = { node: CodeNode & { tokens: Token[][] } } | { error: string } | null;

/** The code a finding cites, in a sheet from the right (Radix: focus moves in
 * and comes back, Escape closes it), highlighted by the server. */
function EvidenceDrawer({
  nodeId,
  label,
  onClose,
}: {
  nodeId: number;
  label: string;
  onClose: () => void;
}) {
  const [loaded, setLoaded] = useState<Loaded>(null);
  // Where focus was when the sheet opened (an evidence button, or the finding
  // `e` was pressed on): the sheet has no trigger of its own, so Radix can't
  // know where to send focus back on close.
  const [returnTo] = useState(() => document.activeElement as HTMLElement | null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/nodes/${nodeId}`, { signal: controller.signal })
      .then(async (response) => {
        if (response.status === 404) {
          // A re-index since the review can delete a node (D110's caveat).
          setLoaded({ error: "This code is no longer in the index (re-indexed since)." });
        } else if (!response.ok) {
          setLoaded({ error: `Couldn't load it (HTTP ${response.status}).` });
        } else {
          setLoaded({ node: await response.json() });
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setLoaded({ error: String(error) });
      });
    return () => controller.abort();
  }, [nodeId]);

  const node = loaded && "node" in loaded ? loaded.node : null;
  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        side="right"
        className="w-full gap-0 p-0 data-[side=right]:sm:max-w-2xl"
        onCloseAutoFocus={(event) => {
          if (returnTo?.isConnected) {
            event.preventDefault();
            returnTo.focus({ preventScroll: true });
          }
        }}
      >
        <SheetHeader className="border-b border-border p-4 pr-12">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Evidence</p>
          <SheetTitle className="break-all font-mono text-sm">{node?.qualified_name ?? label}</SheetTitle>
          <SheetDescription className="text-xs">
            {node ? `${node.kind} · ${node.path}:${node.start_line}–${node.end_line}` : "The code this finding cites."}
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {loaded === null && (
            <div className="space-y-2" aria-busy="true" aria-label="Loading the code">
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-40" />
            </div>
          )}
          {loaded && "error" in loaded && <p className="text-sm text-critical">{loaded.error}</p>}
          {node?.docstring && (
            <p className="mb-3 whitespace-pre-wrap text-sm text-muted-foreground">{node.docstring}</p>
          )}
          {node && (
            <pre className="overflow-x-auto rounded-lg border border-border bg-muted/50 p-3 font-mono text-xs leading-5">
              {node.tokens.map((line, i) => (
                <div key={i} className="flex">
                  <span className="w-12 shrink-0 select-none pr-3 text-right text-muted-foreground">
                    {node.start_line + i}
                  </span>
                  <span className="whitespace-pre">
                    <CodeLine tokens={line} />
                  </span>
                </div>
              ))}
            </pre>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
