"""Checks a flow's graph: known node types, settings, ports and connections, and no loops."""
from __future__ import annotations

from dataclasses import dataclass, field

from .nodes import TYPES, ConfigError, NodeType

MAX_NODES = 60


@dataclass
class Problem:
    node: str | None
    message: str

    def as_dict(self) -> dict:
        return {"node": self.node, "message": self.message}


@dataclass
class Checked:
    graph: dict
    configs: dict[str, dict] = field(default_factory=dict)  # node id -> checked settings
    order: list[str] = field(default_factory=list)  # every node, inputs before the nodes they feed
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def node(self, node_id: str) -> dict:
        return next(node for node in self.graph["nodes"] if node["id"] == node_id)

    def type_of(self, node_id: str) -> NodeType:
        return TYPES.types[self.node(node_id)["type"]]

    def inputs(self, node_id: str) -> list[dict]:
        return [edge for edge in self.graph["edges"] if edge["target"] == node_id]


def _float(value, default: float = 0.0) -> float:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return default


def normalise(graph: dict | None) -> dict:
    """The graph with only the known keys; broken entries are dropped."""
    graph = graph if isinstance(graph, dict) else {}
    nodes: list[dict] = []
    for raw in graph.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        node_id = str(raw.get("id") or "").strip()[:40]
        if not node_id:
            continue
        position = raw.get("position") if isinstance(raw.get("position"), dict) else {}
        nodes.append({
            "id": node_id,
            "type": str(raw.get("type") or "").strip(),
            "config": raw.get("config") if isinstance(raw.get("config"), dict) else {},
            "position": {"x": _float(position.get("x")), "y": _float(position.get("y"))},
        })
    edges: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in graph.get("edges") or []:
        if not isinstance(raw, dict):
            continue
        edge = (str(raw.get("source") or ""), str(raw.get("port") or ""), str(raw.get("target") or ""))
        if all(edge) and edge not in seen:
            seen.add(edge)
            edges.append({"source": edge[0], "port": edge[1], "target": edge[2]})
    return {"nodes": nodes, "edges": edges}


def check(graph: dict | None) -> Checked:
    graph = normalise(graph)
    result = Checked(graph)
    problems = result.problems
    nodes = graph["nodes"]
    if len(nodes) > MAX_NODES:
        problems.append(Problem(None, f"A flow holds at most {MAX_NODES} nodes"))
    ids = [node["id"] for node in nodes]
    if len(set(ids)) != len(ids):
        problems.append(Problem(None, "Two nodes have the same id"))
        return result
    known: dict[str, NodeType] = {}
    for node in nodes:
        node_type = TYPES.get(node["type"])
        if node_type is None:
            problems.append(Problem(node["id"], f"Unknown node type: {node['type'] or '–'}"))
            continue
        known[node["id"]] = node_type
        missing = node_type.missing()
        if missing:
            problems.append(Problem(node["id"], missing))
        try:
            result.configs[node["id"]] = node_type.clean(node["config"])
        except ConfigError as exc:
            problems.append(Problem(node["id"], str(exc)))

    # Connections to removed nodes, to ports a node does not have or into a source are dropped.
    # Those to a node of unknown type stay, so nothing is lost while that node is reported.
    kept, edges = [], []
    for edge in graph["edges"]:
        if edge["source"] not in ids or edge["target"] not in ids:
            continue
        source, target = known.get(edge["source"]), known.get(edge["target"])
        if source is not None and edge["port"] not in {port.key for port in source.ports}:
            continue
        if target is not None and not target.inputs:
            continue
        kept.append(edge)
        if source is not None and target is not None:
            edges.append(edge)
    graph["edges"] = kept

    incoming = {node_id: 0 for node_id in known}
    for edge in edges:
        incoming[edge["target"]] += 1
    for node_id, node_type in known.items():
        has_in = incoming[node_id] > 0
        has_out = any(edge["source"] == node_id for edge in edges)
        if node_type.inputs and not has_in:
            problems.append(Problem(node_id, "Connect an input"))
        if node_type.ports and not has_out:
            problems.append(Problem(node_id, "Connect an output"))

    # Kahn's algorithm keeps the editor's order where it can; what is left forms a loop.
    order: list[str] = []
    waiting = dict(incoming)
    ready = [node_id for node_id in ids if node_id in known and waiting[node_id] == 0]
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for edge in edges:
            if edge["source"] == node_id:
                waiting[edge["target"]] -= 1
                if waiting[edge["target"]] == 0:
                    ready.append(edge["target"])
    stuck = {node_id for node_id in known if node_id not in order}
    # Nodes only fed by a loop are stuck too; peel those off from the far end to name the loop itself.
    while True:
        ends = {node_id for node_id in stuck
                if not any(edge["source"] == node_id and edge["target"] in stuck for edge in edges)}
        if not ends:
            break
        stuck -= ends
    looped = [node_id for node_id in ids if node_id in stuck]
    if looped:
        names = ", ".join(known[node_id].label for node_id in looped)
        problems.append(Problem(looped[0], f"These nodes form a loop: {names}"))
    result.order = order

    categories = {node_type.category for node_type in known.values()}
    if "source" not in categories:
        problems.append(Problem(None, "Add a source"))
    if "output" not in categories:
        problems.append(Problem(None, "Add Radarr or Sonarr"))
    return result


def summaries(checked: Checked) -> dict[str, dict]:
    """Type, label and a one-line description per node, kept with every run."""
    result = {}
    for node in checked.graph["nodes"]:
        node_type = TYPES.get(node["type"])
        config = checked.configs.get(node["id"])
        summary = ""
        if node_type is not None and config is not None:
            try:
                summary = node_type.summary(config)
            except (KeyError, TypeError, ValueError):
                summary = ""
        result[node["id"]] = {
            "type": node["type"],
            "label": node_type.label if node_type else node["type"],
            "category": node_type.category if node_type else "",
            "summary": summary,
        }
    return result
