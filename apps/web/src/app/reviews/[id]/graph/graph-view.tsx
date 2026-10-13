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

import { useEvidence } from "../evidence";

type CodeNodeData = GraphNode & { cited: boolean };
type CodeFlowNode = Node<CodeNodeData, "code">;

const CHANGE_STYLES = {
  added: "border-emerald-500 bg-emerald-50 dark:bg-emerald-950",
  modified: "border-amber-500 bg-amber-50 dark:bg-amber-950",
  deleted: "border-red-500 bg-red-50 dark:bg-red-950",
} as const;

/** One code node: changed code is coloured by its change, nodes the model was
 * shown are solid, the rest dashed; ★ marks evidence a finding cited. */
function CodeNodeView({ data }: NodeProps<CodeFlowNode>) {
  const style = data.is_seed
    ? CHANGE_STYLES[data.change ?? "modified"]
    : data.in_context
      ? "border-zinc-500 bg-white dark:bg-zinc-900"
      : "border-dashed border-zinc-300 bg-white text-zinc-500 dark:border-zinc-700 dark:bg-zinc-950";
  return (
    <div
      className={`w-60 rounded-md border-2 px-2.5 py-1.5 text-left text-xs shadow-sm ${style} ${
        data.node_id !== null ? "cursor-pointer" : ""
      }`}
      title={`${data.qualified_name}${data.path ? `\n${data.path}` : ""}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-zinc-400" />
      <div className="flex items-center gap-1">
        {data.cited && (
          <span className="text-amber-500" aria-label="cited as evidence">
            ★
          </span>
        )}
        <span className="truncate font-mono font-semibold">{data.label}</span>
        <span className="ml-auto shrink-0 text-[10px] uppercase text-zinc-500">{data.kind}</span>
      </div>
      <div className="truncate text-[10px] text-zinc-500">{data.path ?? "not indexed yet"}</div>
      <Handle type="source" position={Position.Right} className="!bg-zinc-400" />
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
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Node ids that findings cite as evidence. */
  cited: number[];
}) {
  const open = useEvidence();
  const { flowNodes, flowEdges } = useMemo(() => {
    const citedSet = new Set(cited);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const flowNodes: CodeFlowNode[] = layout(nodes, edges).map((p) => {
      const node = byId.get(p.id)!;
      return {
        id: p.id,
        type: "code",
        position: { x: p.x, y: p.y },
        data: { ...node, cited: node.node_id !== null && citedSet.has(node.node_id) },
        draggable: true,
        connectable: false,
      };
    });
    const flowEdges: Edge[] = edges.map((e) => ({
      id: `${e.source}->${e.target}:${e.kind}`,
      source: e.source,
      target: e.target,
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
      style: e.kind === "inherits" ? { strokeDasharray: "4 3" } : undefined,
      label: e.kind === "inherits" ? "inherits" : undefined,
    }));
    return { flowNodes, flowEdges };
  }, [nodes, edges, cited]);

  return (
    <div>
      <Legend />
      <div className="mt-2 h-[70vh] min-h-96 rounded-lg border border-zinc-200 dark:border-zinc-800">
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
          minZoom={0.2}
          nodesConnectable={false}
          colorMode="system"
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
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-600 dark:text-zinc-400">
      <span>
        Left to right: {COLUMNS.map((c) => c.title.toLowerCase()).join(" → ")}
      </span>
      <span className="flex items-center gap-1">
        <span className="size-3 rounded-sm border-2 border-amber-500 bg-amber-50" /> modified
      </span>
      <span className="flex items-center gap-1">
        <span className="size-3 rounded-sm border-2 border-emerald-500 bg-emerald-50" /> added
      </span>
      <span className="flex items-center gap-1">
        <span className="size-3 rounded-sm border-2 border-zinc-500" /> shown to the model
      </span>
      <span className="flex items-center gap-1">
        <span className="size-3 rounded-sm border-2 border-dashed border-zinc-300" /> not shown
      </span>
      <span>
        <span className="text-amber-500">★</span> cited as evidence
      </span>
      <span>dashed arrow: inherits</span>
    </div>
  );
}
