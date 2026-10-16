"use client";

import "@xyflow/react/dist/style.css";

import {
  Background,
  Controls,
  type Edge,
  Handle,
  MarkerType,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
} from "@xyflow/react";
import { useMemo } from "react";

import type { GraphEdge, GraphNode } from "@/lib/api";
import { COLUMNS, layout } from "@/lib/graph-layout";
import { useTheme } from "@/lib/use-theme";

import { useEvidence } from "../evidence";

/** `focus`: highlighted (the finding the graph was opened from is about it);
 * `dimmed`: another node, while something is highlighted. */
type CodeNodeData = GraphNode & { cited: boolean; focus: boolean; dimmed: boolean };
type CodeFlowNode = Node<CodeNodeData, "code">;

const CHANGE_STYLES = {
  added: "border-added bg-added-soft",
  modified: "border-warning bg-warning-soft",
  deleted: "border-deleted bg-deleted-soft",
} as const;

/** One code node: changed code is coloured by its change, nodes the model was
 * shown are solid, the rest dashed; ★ marks evidence a finding cited. */
function CodeNodeView({ data }: NodeProps<CodeFlowNode>) {
  const style = data.is_seed
    ? CHANGE_STYLES[data.change ?? "modified"]
    : data.in_context
      ? "border-muted-foreground/70 bg-card"
      : "border-dashed border-input bg-card text-muted-foreground";
  return (
    <div
      className={`w-60 rounded-md border-2 px-2.5 py-1.5 text-left text-xs shadow-sm transition-opacity ${style} ${
        data.node_id !== null ? "cursor-pointer" : ""
      } ${data.focus ? "ring-4 ring-primary/60" : ""} ${data.dimmed ? "opacity-30" : ""}`}
      title={`${data.qualified_name}${data.path ? `\n${data.path}` : ""}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground" />
      <div className="flex items-center gap-1">
        {data.cited && (
          <span className="text-warning" aria-label="cited as evidence">
            ★
          </span>
        )}
        <span className="truncate font-mono font-semibold">{data.label}</span>
        <span className="ml-auto shrink-0 text-[10px] uppercase text-muted-foreground">{data.kind}</span>
      </div>
      <div className="truncate text-[10px] text-muted-foreground">{data.path ?? "not indexed yet"}</div>
      <Handle type="source" position={Position.Right} className="!bg-muted-foreground" />
    </div>
  );
}

const NODE_TYPES = { code: CodeNodeView };

/** The call graph around the review's changed code (api/graph.py), in four
 * columns (lib/graph-layout.ts). Click a node to read its code. */
export function GraphView({
  nodes,
  edges,
  cited,
  focus = [],
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Node ids that findings cite as evidence. */
  cited: number[];
  /** Graph node ids to highlight and zoom to (lib/focus.ts). */
  focus?: string[];
}) {
  const open = useEvidence();
  // The theme on screen (lib/use-theme.ts): the toggle's choice, not just the system's.
  const theme = useTheme();
  const { flowNodes, flowEdges } = useMemo(() => {
    const citedSet = new Set(cited);
    const focusSet = new Set(focus);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const flowNodes: CodeFlowNode[] = layout(nodes, edges).map((p) => {
      const node = byId.get(p.id)!;
      return {
        id: p.id,
        type: "code",
        position: { x: p.x, y: p.y },
        data: {
          ...node,
          cited: node.node_id !== null && citedSet.has(node.node_id),
          focus: focusSet.has(p.id),
          dimmed: focusSet.size > 0 && !focusSet.has(p.id),
        },
        draggable: true,
        connectable: false,
      };
    });
    const flowEdges: Edge[] = edges.map((e) => ({
      id: `${e.source}->${e.target}:${e.kind}`,
      source: e.source,
      target: e.target,
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
      style: {
        ...(e.kind === "inherits" ? { strokeDasharray: "4 3" } : {}),
        // Edges between highlighted nodes stay bright.
        ...(focus.length && !(focusSet.has(e.source) && focusSet.has(e.target))
          ? { opacity: 0.25 }
          : {}),
      },
      label: e.kind === "inherits" ? "inherits" : undefined,
    }));
    return { flowNodes, flowEdges };
  }, [nodes, edges, cited, focus]);

  return (
    <div>
      <Legend />
      <div className="mt-2 h-[70vh] min-h-96 rounded-lg border border-border">
        <ReactFlow<CodeFlowNode>
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={NODE_TYPES}
          onNodeClick={(_, node) => {
            if (node.data.node_id !== null) {
              open({ nodeId: node.data.node_id, label: node.data.qualified_name });
            }
          }}
          fitView
          // Opened from a finding: start zoomed to its nodes.
          fitViewOptions={focus.length ? { nodes: focus.map((id) => ({ id })), maxZoom: 1.2 } : undefined}
          minZoom={0.2}
          nodesConnectable={false}
          colorMode={theme}
        >
          <Background gap={24} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      <span>
        Left to right: {COLUMNS.map((c) => c.title.toLowerCase()).join(" → ")}
      </span>
      <span className="flex items-center gap-1">
        <span className="size-3 rounded-sm border-2 border-warning bg-warning-soft" /> modified
      </span>
      <span className="flex items-center gap-1">
        <span className="size-3 rounded-sm border-2 border-added bg-added-soft" /> added
      </span>
      <span className="flex items-center gap-1">
        <span className="size-3 rounded-sm border-2 border-muted-foreground/70" /> shown to the model
      </span>
      <span className="flex items-center gap-1">
        <span className="size-3 rounded-sm border-2 border-dashed border-input" /> not shown
      </span>
      <span>
        <span className="text-warning">★</span> cited as evidence
      </span>
      <span>dashed arrow: inherits</span>
    </div>
  );
}
