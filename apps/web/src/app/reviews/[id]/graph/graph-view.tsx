"use client";

import "@xyflow/react/dist/style.css";

import {
  Background,
  Controls,
  type Edge,
  Handle,
  MarkerType,
  MiniMap,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
} from "@xyflow/react";
import { BoxIcon, CircleDotIcon, FileCode2Icon, SquareFunctionIcon, StarIcon } from "lucide-react";
import { type KeyboardEvent, type FocusEvent, useMemo, useState } from "react";

import type { GraphEdge, GraphNode } from "@/lib/api";
import { COLUMN_WIDTH, COLUMNS, layout, neighbourhood } from "@/lib/graph-layout";
import { useTheme } from "@/lib/use-theme";
import { cn } from "@/lib/utils";

import { useEvidence } from "../evidence";

/** `lit`: highlighted (the hovered or focused node and its neighbours, or the
 * nodes the finding the graph was opened from is about); `faded`: another
 * node while something is lit. */
type CodeNodeData = GraphNode & { cited: boolean; lit: boolean; faded: boolean };
type CodeFlowNode = Node<CodeNodeData, "code">;
type ColumnFlowNode = Node<{ title: string }, "column">;
type FlowNode = CodeFlowNode | ColumnFlowNode;

const KIND_ICONS: Record<string, typeof BoxIcon> = {
  module: FileCode2Icon,
  class: BoxIcon,
  function: SquareFunctionIcon,
  method: CircleDotIcon,
};

const CHANGE_STYLES = {
  added: { box: "border-added bg-added-soft", tag: "text-added" },
  modified: { box: "border-warning bg-warning-soft", tag: "text-warning" },
  deleted: { box: "border-deleted bg-deleted-soft", tag: "text-deleted" },
} as const;

/** One code node: its kind's icon and name, ★ when a finding cites it, its
 * file; changed code coloured and labelled with its change, code the model
 * was shown solid, the rest dashed and labelled. */
function CodeNodeView({ data }: NodeProps<CodeFlowNode>) {
  const Icon = KIND_ICONS[data.kind] ?? SquareFunctionIcon;
  const change = data.is_seed ? CHANGE_STYLES[data.change ?? "modified"] : null;
  return (
    <div
      className={cn(
        "w-60 rounded-lg border-2 px-2.5 py-1.5 text-left text-xs shadow-sm transition-[opacity,box-shadow] duration-150",
        change
          ? change.box
          : data.in_context
            ? "border-muted-foreground/60 bg-card"
            : "border-dashed border-input bg-card text-muted-foreground",
        data.node_id !== null && "cursor-pointer",
        data.lit && "ring-4 ring-primary/50",
        data.faded && "opacity-25",
      )}
      title={`${data.qualified_name}${data.path ? `\n${data.path}` : ""}`}
    >
      <Handle type="target" position={Position.Left} className="!size-1.5 !border-0 !bg-muted-foreground" />
      <div className="flex items-center gap-1.5">
        <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="truncate font-mono font-semibold">{data.label}</span>
        {data.cited && (
          <StarIcon className="ml-auto size-3.5 shrink-0 fill-warning text-warning" aria-hidden="true" />
        )}
      </div>
      <div className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
        <span className="min-w-0 truncate">{data.path ?? "not indexed yet"}</span>
        {change ? (
          <span className={cn("ml-auto shrink-0 font-semibold uppercase", change.tag)}>{data.change}</span>
        ) : (
          !data.in_context && <span className="ml-auto shrink-0">not shown</span>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="!size-1.5 !border-0 !bg-muted-foreground" />
    </div>
  );
}

/** A column's heading, drawn on the canvas above it. */
function ColumnView({ data }: NodeProps<ColumnFlowNode>) {
  return (
    <div className="w-60 border-b border-border pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
      {data.title}
    </div>
  );
}

const NODE_TYPES = { code: CodeNodeView, column: ColumnView };

/** What a node is, in words, for screen readers. */
function describeNode(node: GraphNode, cited: boolean): string {
  const parts = [`${node.kind} ${node.qualified_name}`];
  if (node.is_seed) parts.push(`${node.change ?? "modified"} in this change`);
  else parts.push(node.in_context ? "shown to the model" : "not shown to the model");
  if (cited) parts.push("cited as evidence");
  if (node.path) parts.push(`in ${node.path}`);
  return parts.join(", ");
}

/** The call graph around the review's changed code (api/graph.py), in four
 * columns (lib/graph-layout.ts). Hovering or focusing a node lights it and its
 * neighbours; clicking it, or Enter, opens its code in the side sheet. */
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
  const [hovered, setHovered] = useState<string | null>(null);

  const placed = useMemo(() => layout(nodes, edges), [nodes, edges]);

  const { flowNodes, flowEdges } = useMemo(() => {
    const citedSet = new Set(cited);
    // Hovering wins over the finding's highlight while it lasts.
    const lit = hovered ? neighbourhood(hovered, edges) : new Set(focus);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const top = Math.min(0, ...placed.map((p) => p.y));
    const columns: ColumnFlowNode[] = COLUMNS.filter(({ key }) =>
      placed.some((p) => p.column === key),
    ).map(({ key, title }) => ({
      id: `column:${key}`,
      type: "column",
      position: { x: COLUMNS.findIndex((c) => c.key === key) * COLUMN_WIDTH, y: top - 44 },
      data: { title },
      draggable: false,
      selectable: false,
      focusable: false,
    }));
    const codeNodes: CodeFlowNode[] = placed.map((p) => {
      const node = byId.get(p.id)!;
      const isCited = node.node_id !== null && citedSet.has(node.node_id);
      return {
        id: p.id,
        type: "code",
        position: { x: p.x, y: p.y },
        // The graph is controlled with no onNodesChange, so React Flow never
        // writes measured sizes back into these objects, and the minimap
        // skips a node without one. A first guess (w-60, about 48px) fixes
        // that; React Flow still measures the real node on the canvas.
        initialWidth: 240,
        initialHeight: 48,
        ariaLabel: describeNode(node, isCited),
        data: {
          ...node,
          cited: isCited,
          lit: lit.has(p.id),
          faded: lit.size > 0 && !lit.has(p.id),
        },
        draggable: true,
        connectable: false,
      };
    });
    const flowEdges: Edge[] = edges.map((e) => {
      const touches = hovered ? e.source === hovered || e.target === hovered : lit.has(e.source) && lit.has(e.target);
      const active = lit.size > 0 && touches;
      return {
        id: `${e.source}->${e.target}:${e.kind}`,
        source: e.source,
        target: e.target,
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: active ? "var(--primary)" : undefined },
        style: {
          ...(e.kind === "inherits" ? { strokeDasharray: "4 3" } : {}),
          ...(active ? { stroke: "var(--primary)", strokeWidth: 2 } : {}),
          ...(lit.size > 0 && !active ? { opacity: 0.2 } : {}),
        },
        label: e.kind === "inherits" ? "inherits" : undefined,
      };
    });
    return { flowNodes: [...columns, ...codeNodes] as FlowNode[], flowEdges };
  }, [nodes, edges, cited, focus, hovered, placed]);

  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const nodeIdOf = (target: EventTarget): string | null =>
    (target as HTMLElement).closest<HTMLElement>(".react-flow__node")?.dataset.id ?? null;

  function openNode(id: string) {
    const node = byId.get(id);
    if (node?.node_id != null) open({ nodeId: node.node_id, label: node.qualified_name });
  }

  return (
    <div>
      <Legend />
      <div
        className="mt-3 h-[70vh] min-h-96 overflow-hidden rounded-xl border border-border bg-card"
        // Keyboard: Tab moves between nodes (React Flow makes them focusable);
        // focus lights the node like hovering, and Enter opens its code.
        onFocus={(event: FocusEvent) => {
          const id = nodeIdOf(event.target);
          setHovered(id !== null && byId.has(id) ? id : null);
        }}
        onBlur={() => setHovered(null)}
        // In the capture phase, and stopped there: React Flow handles Enter on
        // a node itself (it selects it), which pulled focus back out of the
        // sheet that had just opened, so the sheet closed again (seen with a
        // real keypress; a synthetic event didn't show it).
        onKeyDownCapture={(event: KeyboardEvent) => {
          const id = nodeIdOf(event.target);
          if (event.key !== "Enter" || id === null || !byId.has(id)) return;
          event.preventDefault();
          event.stopPropagation();
          openNode(id);
        }}
      >
        <ReactFlow<FlowNode>
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={NODE_TYPES}
          onNodeClick={(_, node) => openNode(node.id)}
          onNodeMouseEnter={(_, node) => node.type === "code" && setHovered(node.id)}
          onNodeMouseLeave={() => setHovered(null)}
          fitView
          // Opened from a finding: start zoomed to its nodes.
          fitViewOptions={focus.length ? { nodes: focus.map((id) => ({ id })), maxZoom: 1.2 } : undefined}
          minZoom={0.2}
          nodesConnectable={false}
          colorMode={theme}
        >
          <Background gap={24} />
          <Controls showInteractive={false} />
          <MiniMap
            pannable
            zoomable
            ariaLabel="Overview of the graph"
            nodeBorderRadius={6}
            nodeClassName={(node) => minimapClass(node as FlowNode)}
            className="!rounded-lg !border !border-border !bg-card"
          />
        </ReactFlow>
      </div>
    </div>
  );
}

/** The minimap's node colours, from the theme's tokens (globals.css). */
function minimapClass(node: FlowNode): string {
  if (node.type === "column") return "mm-hidden";
  const data = node.data as CodeNodeData;
  if (data.is_seed) return `mm-${data.change ?? "modified"}`;
  return data.in_context ? "mm-shown" : "mm-other";
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
      <span className="flex items-center gap-1.5">
        <span className="size-3 rounded-sm border-2 border-warning bg-warning-soft" aria-hidden="true" /> modified
      </span>
      <span className="flex items-center gap-1.5">
        <span className="size-3 rounded-sm border-2 border-added bg-added-soft" aria-hidden="true" /> added
      </span>
      <span className="flex items-center gap-1.5">
        <span className="size-3 rounded-sm border-2 border-muted-foreground/60" aria-hidden="true" /> shown to
        the model
      </span>
      <span className="flex items-center gap-1.5">
        <span className="size-3 rounded-sm border-2 border-dashed border-input" aria-hidden="true" /> not shown
      </span>
      <span className="flex items-center gap-1.5">
        <StarIcon className="size-3.5 fill-warning text-warning" aria-hidden="true" /> cited as evidence
      </span>
      <span>dashed arrow: inherits</span>
      <span className="basis-full text-[11px]">
        Hover or focus a node to light up its neighbours; click it, or press Enter, to read its code.
      </span>
    </div>
  );
}
