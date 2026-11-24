import { CheckCircleIcon, Cog6ToothIcon, ExclamationTriangleIcon, PlayIcon, PlusIcon, TrashIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { useQueryClient } from "@tanstack/react-query";
import {
  addEdge,
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type IsValidConnection,
  type Node,
  type NodeProps,
  type XYPosition,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type CSSProperties, type DragEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, errorText, unwrap } from "../../api/client";
import { useRunJob } from "../../api/jobs";
import { useFlow, useFlowRun, useNodeTypes, useScheduleCheck } from "../../api/queries";
import { Button, ButtonLink, buttonClass } from "../../components/Button";
import { useDialogs } from "../../components/Dialogs";
import { Popover } from "../../components/Popover";
import { useToast } from "../../components/Toasts";
import { Badge, Loading, Notice, Spinner } from "../../components/ui";
import { timeUntil } from "../../lib/format";
import { useTitle } from "../../lib/useTitle";
import { ModeBadge, NodeIcon, outcomeLabel, type FlowGraph, type FlowView, type NodeType, type RunNode } from "./common";
import { ConfigField, fieldDefaults, visible } from "./fields";

type StepData = { type: string; config: Record<string, unknown> };
type StepNode = Node<StepData, "step">;
type Check = { problems: { node?: string | null; message: string }[]; summaries: Record<string, string> };
type Decor = { summaries: Record<string, string>; problems: Record<string, string[]>; counts: Record<string, RunNode> | null; dry: boolean };

const TypesContext = createContext<Map<string, NodeType>>(new Map());
const DecorContext = createContext<Decor>({ summaries: {}, problems: {}, counts: null, dry: true });
const DRAG_TYPE = "application/x-programarr-node";
const ACCENT: Record<string, string> = { source: "border-l-indigo-400", filter: "border-l-gray-500", output: "border-l-emerald-500" };

const round = (value: number) => Math.round(value * 10) / 10;

function toFlow(graph: FlowGraph): { nodes: StepNode[]; edges: Edge[] } {
  return {
    nodes: graph.nodes.map((node) => ({
      id: node.id,
      type: "step",
      position: { x: node.position.x, y: node.position.y },
      data: { type: node.type, config: (node.config ?? {}) as Record<string, unknown> },
    })),
    edges: graph.edges.map((edge) => ({ id: `${edge.source}:${edge.port}>${edge.target}`, source: edge.source, sourceHandle: edge.port, target: edge.target })),
  };
}

function toGraph(nodes: StepNode[], edges: Edge[]): FlowGraph {
  return {
    nodes: nodes.map((node) => ({ id: node.id, type: node.data.type, config: node.data.config, position: { x: round(node.position.x), y: round(node.position.y) } })),
    edges: edges.filter((edge) => edge.sourceHandle).map((edge) => ({ source: edge.source, port: edge.sourceHandle as string, target: edge.target })),
  };
}

function settingsLink(message: string): string | null {
  if (message.includes("Settings › Services")) return "/settings/services";
  if (message.includes("Settings › Lists")) return "/settings/lists";
  if (message.includes("Settings › Sources")) return "/settings/sources";
  return null;
}

// -- the node on the canvas -----------------------------------------------------------------------

function portCount(counts: RunNode | undefined, key: string): number | null {
  if (!counts) return null;
  return counts.ports.find((port) => port.key === key)?.count ?? 0;
}

function StepCard({ id, data, selected }: NodeProps<StepNode>) {
  const types = useContext(TypesContext);
  const decor = useContext(DecorContext);
  const info = types.get(data.type);
  const category = info?.category ?? "filter";
  const problems = decor.problems[id] ?? [];
  const counts = decor.counts?.[id];
  const summary = decor.summaries[id];
  const titles = counts ? (category === "source" ? portCount(counts, "out") : counts.count_in) : null;
  return (
    <div
      className={`step w-56 rounded-lg border border-l-4 bg-gray-800 text-left shadow-lg ${ACCENT[category]} ${
        selected ? "border-indigo-400 ring-2 ring-indigo-500/50" : problems.length ? "border-red-500/70" : "border-gray-600"
      }`}
      data-node={id}
      data-type={data.type}
    >
      {info?.inputs && <Handle type="target" position={Position.Left} className="!h-3 !w-3 !border-2 !border-gray-900 !bg-gray-300" />}
      <div className="flex items-center gap-2 px-3 pt-2.5">
        <NodeIcon type={data.type} category={category} />
        <span className="flex-1 truncate text-sm font-semibold text-gray-100">{info?.label ?? data.type}</span>
        {problems.length > 0 && (
          <span title={problems.join("\n")} className="text-red-400">
            <ExclamationTriangleIcon className="h-4 w-4" aria-hidden="true" />
            <span className="sr-only">{problems.join(". ")}</span>
          </span>
        )}
      </div>
      <p className="line-clamp-2 px-3 pt-1 pb-2.5 text-xs leading-4 text-gray-400">{summary || (info ? "Not set up" : "Unknown type")}</p>
      {titles !== null && (
        <p className="border-t border-gray-700 px-3 py-1.5 text-xs text-gray-300" data-count-in={titles}>
          <span className="font-semibold tabular-nums text-gray-100">{titles}</span> {titles === 1 ? "title" : "titles"}
        </p>
      )}
      {category === "output" && counts && counts.ports.length > 0 && (
        <ul className="space-y-0.5 border-t border-gray-700 px-3 py-1.5 text-xs text-gray-300">
          {counts.ports.map((port) => (
            <li key={port.key} className="flex justify-between gap-2" data-outcome={port.key}>
              <span>{outcomeLabel(port.key, decor.dry)}</span>
              <span className="font-semibold tabular-nums text-gray-100">{port.count}</span>
            </li>
          ))}
        </ul>
      )}
      {info && info.ports.length > 0 && (
        <div className="border-t border-gray-700 py-1">
          {info.ports.map((port) => {
            const count = portCount(counts, port.key);
            return (
              <div key={port.key} className="relative flex items-center justify-end gap-2 py-1 pr-4 pl-3 text-xs text-gray-300" data-port={port.key}>
                {info.ports.length > 1 || category !== "source" ? port.label : "Out"}
                {count !== null && <span className="rounded-full bg-gray-700 px-1.5 font-semibold tabular-nums text-gray-100">{count}</span>}
                <Handle type="source" position={Position.Right} id={port.key} className="!h-3 !w-3 !border-2 !border-gray-900 !bg-indigo-400" />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const NODE_TYPES = { step: StepCard };

// -- side panels ----------------------------------------------------------------------------------

function AddMenu({ types, category, onAdd }: { types: NodeType[]; category: string; onAdd: (type: NodeType) => void }) {
  const items = types.filter((type) => type.category === category);
  const label = { source: "Source", filter: "Filter", output: "Output" }[category] ?? category;
  return (
    <Popover
      label={`Add ${label.toLowerCase()}`}
      button={
        <>
          <PlusIcon className="h-4 w-4" aria-hidden="true" />
          {label}
        </>
      }
      buttonClass={buttonClass("default", "sm", "shadow-md")}
      data={{ "data-add-menu": category }}
      width={232}
      align="start"
      padding="p-1.5"
    >
      {(close) => (
        <ul className="add-menu">
          {items.map((type) => (
            <li key={type.type}>
              <button
                type="button"
                onClick={() => {
                  onAdd(type);
                  close();
                }}
                draggable
                onDragStart={(event: DragEvent) => {
                  event.dataTransfer.setData(DRAG_TYPE, type.type);
                  event.dataTransfer.effectAllowed = "move";
                }}
                data-add={type.type}
                title={type.missing ?? undefined}
                className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm text-gray-200 hover:bg-gray-700 hover:text-white"
              >
                <NodeIcon type={type.type} category={type.category} />
                <span className="flex-1 truncate">{type.label}</span>
                {type.missing && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-yellow-400" aria-label="Needs setup" />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Popover>
  );
}

function NodePanel({ node, info, problems, onChange, onRemove, onClose }: {
  node: StepNode;
  info: NodeType | undefined;
  problems: string[];
  onChange: (config: Record<string, unknown>) => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  // Settings a node never touched show their defaults, as the server applies them.
  const config = useMemo(() => ({ ...(info ? fieldDefaults(info.fields) : {}), ...node.data.config }), [info, node.data.config]);
  const set = useCallback((key: string, value: unknown) => onChange({ ...config, [key]: value }), [config, onChange]);
  const link = info?.missing ? settingsLink(info.missing) : null;
  return (
    <div className="node-panel" data-panel={node.id}>
      <div className="flex items-center gap-2">
        {info && <NodeIcon type={info.type} category={info.category} className="h-5 w-5" />}
        <h2 className="flex-1 truncate text-lg font-semibold text-gray-100">{info?.label ?? node.data.type}</h2>
        <button type="button" onClick={onClose} className="rounded-md p-1 text-gray-400 hover:bg-gray-700 hover:text-white" aria-label="Close" data-action="close-panel">
          <XMarkIcon className="h-5 w-5" />
        </button>
      </div>
      {!info && (
        <div className="mt-3">
          <Notice tone="red">Unknown node type: {node.data.type}</Notice>
        </div>
      )}
      {info?.missing && (
        <div className="mt-3">
          <Notice>{link ? <Link to={link} className="underline underline-offset-2">{info.missing}</Link> : info.missing}</Notice>
        </div>
      )}
      {problems.filter((problem) => problem !== info?.missing).length > 0 && (
        <ul className="node-problems mt-3 space-y-1 text-sm text-red-300">
          {problems.filter((problem) => problem !== info?.missing).map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {info && (
        <div className="mt-4 space-y-4">
          {info.fields
            .filter((field) => visible(field, config))
            .map((field) => (
              <ConfigField key={`${node.id}-${field.key}`} field={field} value={config[field.key]} onChange={(value) => set(field.key, value)} />
            ))}
          {!info.fields.length && <p className="text-sm text-gray-400">Nothing to set.</p>}
        </div>
      )}
      <div className="mt-6 border-t border-gray-700 pt-4">
        <Button variant="ghost" size="sm" icon={<TrashIcon className="h-4 w-4" />} onClick={onRemove} data-action="remove-node">
          Remove node
        </Button>
      </div>
    </div>
  );
}

type Settings = { name: string; schedule: string | null; enabled: boolean };

function scheduleMode(value: string | null): "manual" | "interval" | "cron" {
  if (!value) return "manual";
  return value.startsWith("every:") ? "interval" : "cron";
}

function ScheduleField({ value, onChange }: { value: string | null; onChange: (value: string | null) => void }) {
  const mode = scheduleMode(value);
  const match = value ? /^every:(\d+)([mhd])$/.exec(value) : null;
  const [amount, unit] = match ? [match[1], match[2]] : ["6", "h"];
  const check = useScheduleCheck(value ?? "", !!value);
  return (
    <div className="space-y-2">
      <label htmlFor="flow-schedule" className="block text-xs font-semibold text-gray-400">
        Schedule
      </label>
      <select
        id="flow-schedule"
        className="field-input"
        value={mode}
        onChange={(event) => onChange(event.target.value === "manual" ? null : event.target.value === "interval" ? "every:6h" : "0 6 * * *")}
      >
        <option value="manual">Manual</option>
        <option value="interval">Interval</option>
        <option value="cron">Cron</option>
      </select>
      {mode === "interval" && (
        <div className="flex items-center gap-2">
          <span className="text-sm text-gray-300">Every</span>
          <input
            type="number"
            min={1}
            className="field-input max-w-20"
            value={amount}
            aria-label="Interval"
            onChange={(event) => onChange(`every:${event.target.value || "1"}${unit}`)}
          />
          <select className="field-input max-w-28" value={unit} aria-label="Unit" onChange={(event) => onChange(`every:${amount}${event.target.value}`)}>
            <option value="m">minutes</option>
            <option value="h">hours</option>
            <option value="d">days</option>
          </select>
        </div>
      )}
      {mode === "cron" && (
        <input
          className="field-input font-mono"
          value={value ?? ""}
          aria-label="Cron"
          placeholder="0 6 * * *"
          onChange={(event) => onChange(event.target.value)}
          spellCheck={false}
        />
      )}
      {value && check.data && (
        <p className={`text-xs ${check.data.ok ? "text-gray-400" : "text-red-300"}`} data-schedule-check>
          {check.data.ok ? `${check.data.text}, next ${timeUntil(check.data.next_run_at)}` : check.data.message}
        </p>
      )}
    </div>
  );
}

function FlowSettings({ flow, settings, onSettings, onDryRunMode, onDelete }: {
  flow: FlowView;
  settings: Settings;
  onSettings: (settings: Settings) => void;
  onDryRunMode: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flow-settings space-y-5">
      <div>
        <label htmlFor="flow-name" className="mb-1 block text-xs font-semibold text-gray-400">
          Name
        </label>
        <input id="flow-name" className="field-input" value={settings.name} onChange={(event) => onSettings({ ...settings, name: event.target.value })} />
      </div>
      <ScheduleField value={settings.schedule} onChange={(schedule) => onSettings({ ...settings, schedule })} />
      {settings.schedule && (
        <label className="flex items-center gap-2 text-sm text-gray-200">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-gray-500 bg-gray-700 text-indigo-600"
            checked={settings.enabled}
            onChange={(event) => onSettings({ ...settings, enabled: event.target.checked })}
          />
          Run on schedule
        </label>
      )}
      <div className="flex flex-wrap gap-2 border-t border-gray-700 pt-4">
        {!flow.dry_run && (
          <Button size="sm" onClick={onDryRunMode} data-action="dry-run-mode">
            Back to dry run
          </Button>
        )}
        <Button variant="ghost" size="sm" icon={<TrashIcon className="h-4 w-4" />} onClick={onDelete} data-action="delete-flow">
          Delete flow
        </Button>
      </div>
    </div>
  );
}

function Checks({ problems, labels, onSelect }: { problems: Check["problems"]; labels: Record<string, string>; onSelect: (nodeId: string) => void }) {
  if (!problems.length) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md bg-gray-800/90 px-2.5 py-1.5 text-xs font-medium text-green-300 shadow-md ring-1 ring-gray-700" data-checks="ok">
        <CheckCircleIcon className="h-4 w-4" aria-hidden="true" />
        Ready to run
      </span>
    );
  }
  return (
    <Popover
      label="Problems"
      button={
        <>
          <ExclamationTriangleIcon className="h-4 w-4" aria-hidden="true" />
          {problems.length === 1 ? "1 problem" : `${problems.length} problems`}
        </>
      }
      buttonClass="inline-flex items-center gap-1.5 rounded-md bg-red-950/90 px-2.5 py-1.5 text-xs font-medium text-red-200 shadow-md ring-1 ring-red-500/50 hover:bg-red-900"
      data={{ "data-checks": String(problems.length) }}
      width={320}
    >
      {(close) => (
        <ul className="flow-problems space-y-2">
          {problems.map((problem, index) => (
            <li key={`${problem.node}-${index}`} className="text-sm text-red-200">
              {problem.node ? (
                <button
                  type="button"
                  className="text-left hover:underline"
                  onClick={() => {
                    close();
                    onSelect(problem.node as string);
                  }}
                >
                  <span className="font-semibold">{labels[problem.node] ?? problem.node}:</span> {problem.message}
                </button>
              ) : (
                problem.message
              )}
            </li>
          ))}
        </ul>
      )}
    </Popover>
  );
}

// -- the editor -----------------------------------------------------------------------------------

function Editor({ flow, types }: { flow: FlowView; types: NodeType[] }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const dialogs = useDialogs();
  const runJob = useRunJob();
  const flowApi = useReactFlow<StepNode, Edge>();
  const typeMap = useMemo(() => new Map(types.map((type) => [type.type, type])), [types]);
  const initial = useMemo(() => toFlow(flow.graph ?? { nodes: [], edges: [] }), [flow.graph]);
  const [nodes, setNodes, onNodesChange] = useNodesState<StepNode>(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initial.edges);
  const [settings, setSettings] = useState<Settings>({ name: flow.name, schedule: flow.schedule ?? null, enabled: flow.enabled });
  const [saved, setSaved] = useState(() => JSON.stringify({ graph: flow.graph, name: flow.name, schedule: flow.schedule ?? null, enabled: flow.enabled }));
  const [check, setCheck] = useState<Check>({ problems: flow.problems, summaries: flow.summaries });
  const [busy, setBusy] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);

  const graph = useMemo(() => toGraph(nodes, edges), [nodes, edges]);
  const graphKey = JSON.stringify(graph);
  const current = JSON.stringify({ graph, name: settings.name, schedule: settings.schedule, enabled: settings.enabled });
  const dirty = current !== saved;
  const graphDirty = graphKey !== JSON.stringify(JSON.parse(saved).graph);

  // Checks and one-line summaries follow every change, a moment after typing stops.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      unwrap(api.POST("/api/flows/check", { body: { graph } }))
        .then((result) => setCheck(result))
        .catch(() => undefined);
    }, 400);
    return () => window.clearTimeout(timer);
  }, [graphKey]); // graphKey stands for graph

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const lastRun = flow.last_run;
  const runFits = !!lastRun && lastRun.flow_version === flow.version && lastRun.status !== "running";
  const run = useFlowRun(lastRun?.id ?? 0, runFits);
  const decor = useMemo<Decor>(() => {
    const problems: Record<string, string[]> = {};
    for (const problem of check.problems) if (problem.node) (problems[problem.node] ??= []).push(problem.message);
    const counts = runFits && run.data && !graphDirty ? Object.fromEntries(run.data.nodes.map((node) => [node.id, node])) : null;
    return { summaries: check.summaries, problems, counts, dry: run.data?.dry_run ?? true };
  }, [check, run.data, graphDirty, runFits]);

  const selectedNodes = nodes.filter((node) => node.selected);
  const selectedNode = selectedNodes.length === 1 ? selectedNodes[0] : null;
  const selectedEdge = !selectedNodes.length ? edges.find((edge) => edge.selected) ?? null : null;
  const labels = Object.fromEntries(nodes.map((node) => [node.id, typeMap.get(node.data.type)?.label ?? node.data.type]));

  const onConnect = useCallback((connection: Connection) => {
    setEdges((list) => addEdge({ ...connection, id: `${connection.source}:${connection.sourceHandle}>${connection.target}` }, list));
  }, [setEdges]);

  const isValidConnection: IsValidConnection = useCallback((connection) => {
    if (connection.source === connection.target) return false;
    const target = flowApi.getNode(connection.target);
    return !!target && !!typeMap.get((target.data as StepData).type)?.inputs;
  }, [flowApi, typeMap]);

  /** The spot moved down until it clears every node. */
  const clear = (spot: XYPosition): XYPosition => {
    const next = { x: Math.round(spot.x), y: Math.round(spot.y) };
    const overlaps = () =>
      nodes.some((node) => {
        const width = node.measured?.width ?? 224;
        const height = node.measured?.height ?? 120;
        return next.x < node.position.x + width + 16 && next.x + 240 > node.position.x && next.y < node.position.y + height + 16 && next.y + 136 > node.position.y;
      });
    for (let tries = 0; tries < 20 && overlaps(); tries += 1) next.y += 72;
    return next;
  };

  /** Where a new node goes: right of the selected or right-most node, sources in the first column. */
  const placeFor = (type: NodeType): XYPosition => {
    const selected = nodes.find((node) => node.selected);
    if (selected && type.inputs) return clear({ x: selected.position.x + 290, y: selected.position.y });
    if (nodes.length) {
      const xs = nodes.map((node) => node.position.x);
      if (type.inputs) {
        const right = nodes.reduce((best, node) => (node.position.x > best.position.x ? node : best));
        return clear({ x: right.position.x + 290, y: right.position.y });
      }
      return clear({ x: Math.min(...xs), y: Math.min(...nodes.map((node) => node.position.y)) });
    }
    const box = wrapper.current?.getBoundingClientRect();
    if (!box) return { x: 0, y: 0 };
    const point = flowApi.screenToFlowPosition({ x: box.left + box.width * 0.4, y: box.top + box.height * 0.4 });
    return { x: Math.round(point.x - 112), y: Math.round(point.y - 60) };
  };

  const addNode = (type: NodeType, position?: XYPosition) => {
    const taken = new Set(nodes.map((node) => node.id));
    let number = 1;
    while (taken.has(`${type.type}${number}`)) number += 1;
    const id = `${type.type}${number}`;
    const spot = position ?? placeFor(type);
    const node: StepNode = { id, type: "step", position: spot, data: { type: type.type, config: fieldDefaults(type.fields) }, selected: true };
    setNodes((list) => [...list.map((other) => ({ ...other, selected: false })), node]);
    setEdges((list) => list.map((edge) => ({ ...edge, selected: false })));
    // Pan just enough to show it; the settings panel covers the right edge of the canvas.
    const box = wrapper.current?.getBoundingClientRect();
    if (box && !position) {
      const start = flowApi.flowToScreenPosition(spot);
      const end = flowApi.flowToScreenPosition({ x: spot.x + 224, y: spot.y + 120 });
      const area = { left: box.left + 24, top: box.top + 56, right: box.right - 344, bottom: box.bottom - 24 };
      const dx = end.x > area.right ? area.right - end.x : start.x < area.left ? area.left - start.x : 0;
      const dy = end.y > area.bottom ? area.bottom - end.y : start.y < area.top ? area.top - start.y : 0;
      if (dx || dy) {
        const view = flowApi.getViewport();
        void flowApi.setViewport({ x: view.x + dx, y: view.y + dy, zoom: view.zoom }, { duration: 200 });
      }
    }
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    const type = typeMap.get(event.dataTransfer.getData(DRAG_TYPE));
    if (type) addNode(type, flowApi.screenToFlowPosition({ x: event.clientX - 112, y: event.clientY - 30 }));
  };

  const updateConfig = useCallback((id: string, config: Record<string, unknown>) => {
    setNodes((list) => list.map((node) => (node.id === id ? { ...node, data: { ...node.data, config } } : node)));
  }, [setNodes]);

  const deselect = () => {
    setNodes((list) => list.map((node) => ({ ...node, selected: false })));
    setEdges((list) => list.map((edge) => ({ ...edge, selected: false })));
  };

  const select = (id: string) => {
    setNodes((list) => list.map((node) => ({ ...node, selected: node.id === id })));
    setEdges((list) => list.map((edge) => ({ ...edge, selected: false })));
    const node = flowApi.getNode(id);
    if (node) flowApi.setCenter(node.position.x + 112, node.position.y + 60, { zoom: Math.max(flowApi.getZoom(), 0.8), duration: 300 });
  };

  const save = async (): Promise<FlowView | null> => {
    setBusy(true);
    try {
      const updated = await unwrap(api.PUT("/api/flows/{flow_id}", {
        params: { path: { flow_id: flow.id } },
        body: { name: settings.name, graph, schedule: settings.schedule, enabled: settings.enabled },
      }));
      queryClient.setQueryData(["flow", flow.id], updated);
      queryClient.invalidateQueries({ queryKey: ["flows"] });
      setSaved(JSON.stringify({ graph: updated.graph, name: updated.name, schedule: updated.schedule ?? null, enabled: updated.enabled }));
      setSettings({ name: updated.name, schedule: updated.schedule ?? null, enabled: updated.enabled });
      setCheck({ problems: updated.problems, summaries: updated.summaries });
      if (JSON.stringify(updated.graph) !== graphKey) {
        // The server dropped broken connections: show what it kept.
        const kept = toFlow(updated.graph ?? { nodes: [], edges: [] });
        setNodes(kept.nodes);
        setEdges(kept.edges);
      }
      return updated;
    } catch (error) {
      toast.show(errorText(error), { kind: "error", timeout: 8000 });
      return null;
    } finally {
      setBusy(false);
    }
  };

  const saveRef = useRef(save);
  saveRef.current = save;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void saveRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const start = async (dryRun: boolean) => {
    if (dirty && !(await save())) return;
    const job = await runJob(
      () => unwrap(api.POST("/api/flows/{flow_id}/run", { params: { path: { flow_id: flow.id } }, body: { dry_run: dryRun } })),
      dryRun ? "Dry run…" : "Running…",
    );
    const runId = job?.result?.run;
    if (runId) navigate(`/flows/runs/${runId}`);
  };

  const setMode = async (live: boolean) => {
    if (live) {
      const ok = await dialogs.confirm({
        title: "Go live?",
        body: "Runs of this flow add titles to Radarr and Sonarr and add tags there. Each output adds at most its limit of new titles per run.",
        confirm: "Go live",
      });
      if (!ok) return;
      if (dirty && !(await save())) return;
    }
    try {
      const updated = await unwrap(api.PUT("/api/flows/{flow_id}", { params: { path: { flow_id: flow.id } }, body: { dry_run: !live } }));
      queryClient.setQueryData(["flow", flow.id], updated);
      queryClient.invalidateQueries({ queryKey: ["flows"] });
      toast.show(live ? "Flow is live" : "Flow is back in dry run", { kind: "success" });
    } catch (error) {
      toast.show(errorText(error), { kind: "error", timeout: 8000 });
    }
  };

  const remove = async () => {
    const ok = await dialogs.confirm({ title: `Delete ${flow.name}?`, body: "Its history goes with it.", confirm: "Delete", danger: true });
    if (!ok) return;
    try {
      await unwrap(api.DELETE("/api/flows/{flow_id}", { params: { path: { flow_id: flow.id } } }));
      queryClient.invalidateQueries({ queryKey: ["flows"] });
      setSaved(current);
      navigate("/flows");
    } catch (error) {
      toast.show(errorText(error), { kind: "error", timeout: 8000 });
    }
  };

  const running = lastRun?.status === "running";

  return (
    <TypesContext.Provider value={typeMap}>
      <DecorContext.Provider value={decor}>
        <div className="mt-6 mb-4 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div className="min-w-0">
            <p className="text-sm text-gray-400">
              <Link to="/flows" className="hover:text-gray-200">
                Flows
              </Link>
            </p>
            <h1 className="truncate text-2xl font-bold leading-8 text-gray-100 sm:text-3xl sm:leading-10">{settings.name || flow.name}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-gray-400">
              <ModeBadge dryRun={flow.dry_run} />
              {flow.schedule && !flow.enabled && <Badge tone="gray">Off</Badge>}
              <span>{flow.schedule_text}</span>
              {flow.next_run_at && <span>next {timeUntil(flow.next_run_at)}</span>}
              {dirty && (
                <Badge tone="yellow" title="Ctrl+S saves">
                  Unsaved
                </Badge>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <ButtonLink to={`/flows/history?flow=${flow.id}`} variant="ghost">
              History
            </ButtonLink>
            <Popover
              label="Flow settings"
              button={
                <>
                  <Cog6ToothIcon className="h-4 w-4" aria-hidden="true" />
                  Settings
                </>
              }
              buttonClass={buttonClass("ghost", "md")}
              data={{ "data-action": "settings" }}
            >
              {(close) => (
                <FlowSettings
                  flow={flow}
                  settings={settings}
                  onSettings={setSettings}
                  onDryRunMode={() => {
                    close();
                    void setMode(false);
                  }}
                  onDelete={() => {
                    close();
                    void remove();
                  }}
                />
              )}
            </Popover>
            <Button onClick={() => void save()} disabled={!dirty || busy} variant={dirty ? "primary" : "default"} data-action="save">
              {busy && <Spinner className="h-4 w-4" />}
              Save
            </Button>
            <Button onClick={() => void start(true)} disabled={running} icon={<PlayIcon className="h-4 w-4" />} data-action="dry-run">
              Dry run
            </Button>
            {flow.dry_run ? (
              <Button variant="primary" onClick={() => void setMode(true)} data-action="go-live">
                Go live
              </Button>
            ) : (
              <Button variant="primary" onClick={() => void start(false)} disabled={running} icon={<PlayIcon className="h-4 w-4" />} data-action="run">
                Run
              </Button>
            )}
          </div>
        </div>
        <div
          ref={wrapper}
          className="flow-canvas relative h-[70vh] min-h-[28rem] overflow-hidden rounded-lg border border-gray-700 lg:h-[calc(100vh-13.5rem)] lg:min-h-[36rem]"
          style={{ "--xy-background-color": "var(--color-gray-900)" } as CSSProperties}
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
          }}
          onDrop={onDrop}
        >
          <ReactFlow<StepNode, Edge>
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            isValidConnection={isValidConnection}
            colorMode="dark"
            fitView
            fitViewOptions={{ maxZoom: 1, padding: 0.15 }}
            minZoom={0.3}
            deleteKeyCode={["Backspace", "Delete"]}
            defaultEdgeOptions={{ style: { strokeWidth: 2 } }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#374151" />
            <Controls showInteractive={false} />
            <Panel position="top-left" className="pointer-events-none !right-0 flex flex-wrap items-start gap-2">
              {["source", "filter", "output"].map((category) => (
                <span key={category} className="pointer-events-auto">
                  <AddMenu types={types} category={category} onAdd={(type) => addNode(type)} />
                </span>
              ))}
              {!selectedNode && (
                <span className="pointer-events-auto ml-auto">
                  <Checks problems={check.problems} labels={labels} onSelect={select} />
                </span>
              )}
            </Panel>
            {!nodes.length && (
              <Panel position="top-center" className="!top-1/3 text-sm text-gray-400">
                Add a source to start
              </Panel>
            )}
            {selectedEdge && (
              <Panel position="bottom-center" className="flex items-center gap-3 rounded-md bg-gray-800/95 px-3 py-2 text-sm text-gray-300 shadow-md ring-1 ring-gray-700">
                <span>
                  {labels[selectedEdge.source]} to {labels[selectedEdge.target]}
                </span>
                <Button variant="ghost" size="sm" icon={<TrashIcon className="h-4 w-4" />} onClick={() => void flowApi.deleteElements({ edges: [{ id: selectedEdge.id }] })} data-action="remove-edge">
                  Remove connection
                </Button>
              </Panel>
            )}
          </ReactFlow>
          {selectedNode && (
            <aside className="side-panel absolute inset-y-0 right-0 z-10 w-full max-w-80 overflow-y-auto border-l border-gray-700 bg-gray-800/95 p-4 shadow-2xl backdrop-blur" aria-label="Node settings">
              <NodePanel
                node={selectedNode}
                info={typeMap.get(selectedNode.data.type)}
                problems={decor.problems[selectedNode.id] ?? []}
                onChange={(config) => updateConfig(selectedNode.id, config)}
                onRemove={() => void flowApi.deleteElements({ nodes: [{ id: selectedNode.id }] })}
                onClose={deselect}
              />
            </aside>
          )}
        </div>
      </DecorContext.Provider>
    </TypesContext.Provider>
  );
}

export function FlowEditor() {
  const { id = "" } = useParams();
  const flowId = Number(id);
  const flow = useFlow(flowId);
  const types = useNodeTypes();
  useTitle(flow.data?.name ?? "Flow");
  if (flow.isPending || types.isPending) return <Loading />;
  if (flow.error || !flow.data) return <Notice tone="red">{flow.error?.message ?? "Flow not found"}</Notice>;
  if (types.error || !types.data) return <Notice tone="red">{types.error?.message ?? "Cannot load the node types"}</Notice>;
  return (
    <ReactFlowProvider key={flowId}>
      <Editor flow={flow.data} types={types.data.types} />
    </ReactFlowProvider>
  );
}
