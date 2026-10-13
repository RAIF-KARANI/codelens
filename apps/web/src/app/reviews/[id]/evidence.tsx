"use client";

import { createContext, use, useEffect, useState } from "react";

import type { CodeNode } from "@/lib/api";

/** The evidence drawer: a finding's evidence buttons open the code the model
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

export function EvidenceButton({ nodeId, label }: { nodeId: number; label: string }) {
  const open = use(EvidenceContext);
  return (
    <button
      type="button"
      onClick={() => open({ nodeId, label })}
      className="max-w-full truncate rounded border border-zinc-300 px-1.5 py-0.5 font-mono text-xs text-zinc-700 hover:border-zinc-500 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
      title={label}
    >
      {label}
    </button>
  );
}

type Loaded = { node: CodeNode } | { error: string } | null;

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
          setLoaded({ node: (await response.json()) as CodeNode });
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setLoaded({ error: String(error) });
      });
    return () => controller.abort();
  }, [nodeId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const node = loaded && "node" in loaded ? loaded.node : null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true" aria-label={`Evidence: ${label}`}>
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/30" />
      <div className="relative flex h-full w-full max-w-2xl flex-col border-l border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex items-start gap-3 border-b border-zinc-200 p-4 dark:border-zinc-800">
          <div className="min-w-0 flex-1">
            <p className="text-xs uppercase tracking-wide text-zinc-500">Evidence</p>
            <h2 className="mt-1 break-all font-mono text-sm font-semibold">{node?.qualified_name ?? label}</h2>
            {node && (
              <p className="mt-1 text-xs text-zinc-500">
                {node.kind} · {node.path}:{node.start_line}–{node.end_line}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            autoFocus
            className="rounded-md px-2 py-1 text-sm text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-900"
          >
            Close
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {loaded === null && <p className="text-sm text-zinc-500">Loading…</p>}
          {loaded && "error" in loaded && <p className="text-sm text-red-700 dark:text-red-400">{loaded.error}</p>}
          {node?.docstring && (
            <p className="mb-3 whitespace-pre-wrap text-sm text-zinc-600 dark:text-zinc-400">{node.docstring}</p>
          )}
          {node && (
            <pre className="overflow-x-auto rounded-md bg-zinc-50 p-3 font-mono text-xs leading-5 dark:bg-zinc-900">
              {node.code.split("\n").map((line, i) => (
                <div key={i} className="flex">
                  <span className="w-12 shrink-0 select-none pr-3 text-right text-zinc-400">
                    {node.start_line + i}
                  </span>
                  <span className="whitespace-pre">{line}</span>
                </div>
              ))}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
