"""Flows and their runs in the database, and the views the web app reads."""
from __future__ import annotations

import math

from .. import db, settings
from ..lists import runs as list_runs
from ..media import store as media_store
from . import graph as graphs
from . import schedule
from .nodes import OUTCOMES, TYPES

PAGE = 50
OUTPUT_PORTS = tuple(OUTCOMES)
SHOW = ("all", *OUTPUT_PORTS, "filtered")
UNSET = object()  # update(): leave the schedule as it is


class FlowNotFound(Exception):
    pass


def _flow(row: dict | None) -> dict | None:
    if row is None:
        return None
    row["graph"] = graphs.normalise(db.loads(row["graph"], {}))
    return row


def get(flow_id: int) -> dict | None:
    return _flow(db.get().one("SELECT * FROM flows WHERE id=?", (flow_id,)))


def _name(value: str) -> str:
    name = " ".join(str(value or "").split())[:80]
    if not name:
        raise ValueError("Name the flow")
    return name


def create(name: str, graph: dict | None = None, schedule_text: str | None = None, enabled: bool = True) -> int:
    """New flows always start as a dry run."""
    now = db.now()
    value = schedule.normalise(schedule_text)
    return db.get().execute(
        "INSERT INTO flows (name, graph, version, schedule, enabled, dry_run, next_run_at, created_at, updated_at) "
        "VALUES (?,?,1,?,?,1,?,?,?)",
        (_name(name), db.dumps(graphs.normalise(graph)), value, int(enabled),
         schedule.next_run(value, now) if enabled else None, now, now),
    ).lastrowid


def update(flow_id: int, *, name: str | None = None, graph: dict | None = None, schedule_text=UNSET,
           enabled: bool | None = None, dry_run: bool | None = None) -> dict:
    flow = get(flow_id)
    if flow is None:
        raise FlowNotFound()
    values: dict = {}
    if name is not None:
        values["name"] = _name(name)
    if graph is not None:
        clean = graphs.normalise(graph)
        if clean != flow["graph"]:
            values["graph"] = db.dumps(clean)
            values["version"] = flow["version"] + 1
    if schedule_text is not UNSET:
        values["schedule"] = schedule.normalise(schedule_text)
    if enabled is not None:
        values["enabled"] = int(enabled)
    if dry_run is not None:
        values["dry_run"] = int(dry_run)
    if "schedule" in values or "enabled" in values:
        active = values.get("enabled", flow["enabled"])
        value = values.get("schedule", flow["schedule"])
        values["next_run_at"] = schedule.next_run(value, db.now()) if active else None
    if values:
        values["updated_at"] = db.now()
        db.get().update("flows", "id", flow_id, values)
    return get(flow_id)


# Lists read by a flow run that is gone keep flow_run_id 0: they stay in the match log but are no previews,
# and a later run with the same id never claims them.
GONE = 0


def delete(flow_id: int) -> bool:
    database = db.get()
    with database.transaction() as conn:
        conn.execute("UPDATE parser_runs SET flow_run_id=? WHERE flow_run_id IN "
                     "(SELECT id FROM flow_runs WHERE flow_id=?)", (GONE, flow_id))
        deleted = conn.execute("DELETE FROM flows WHERE id=?", (flow_id,)).rowcount
    return bool(deleted)


# -- views -----------------------------------------------------------------------------------------

def view(flow: dict, with_graph: bool = True) -> dict:
    checked = graphs.check(flow["graph"])
    last = db.get().one("SELECT * FROM flow_runs WHERE flow_id=? ORDER BY id DESC LIMIT 1", (flow["id"],))
    result = {
        "id": flow["id"],
        "name": flow["name"],
        "version": flow["version"],
        "schedule": flow["schedule"],
        "schedule_text": schedule.describe(flow["schedule"]),
        "enabled": bool(flow["enabled"]),
        "dry_run": bool(flow["dry_run"]),
        "next_run_at": flow["next_run_at"] if flow["enabled"] and flow["schedule"] else None,
        "created_at": flow["created_at"],
        "updated_at": flow["updated_at"],
        "problems": [problem.as_dict() for problem in checked.problems],
        "summaries": {node_id: info["summary"] for node_id, info in graphs.summaries(checked).items()},
        "outputs": sorted({TYPES.types[node["type"]].label for node in flow["graph"]["nodes"]
                           if node["type"] in TYPES.types and TYPES.types[node["type"]].category == "output"}),
        "nodes": len(flow["graph"]["nodes"]),
        "last_run": run_brief(last, flow["name"]) if last else None,
        "graph": flow["graph"] if with_graph else None,
    }
    return result


def flows() -> list[dict]:
    rows = db.get().query("SELECT * FROM flows ORDER BY name COLLATE NOCASE, id")
    return [view(_flow(row), with_graph=False) for row in rows]


def _totals(stats: dict) -> dict:
    totals = {port: 0 for port in OUTPUT_PORTS}
    totals.update({key: int(value) for key, value in (stats.get("totals") or {}).items() if key in totals})
    return totals


def run_brief(row: dict, flow_name: str | None = None) -> dict:
    stats = db.loads(row.get("stats"), {}) or {}
    return {
        "id": row["id"],
        "flow_id": row["flow_id"],
        "flow_name": flow_name if flow_name is not None else row.get("flow_name") or "",
        "flow_version": row["flow_version"],
        "trigger": row["trigger"] or "manual",
        "dry_run": bool(row["dry_run"]),
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "status": row["status"] or "running",
        "error": row["error"],
        "titles": int(stats.get("titles") or 0),
        **_totals(stats),
    }


def runs(flow_id: int | None = None, page: int = 1) -> dict:
    database = db.get()
    where, params = ("WHERE r.flow_id=?", [flow_id]) if flow_id else ("", [])
    total = database.scalar(f"SELECT COUNT(*) FROM flow_runs r {where}", params, 0)
    pages = max(1, math.ceil(total / PAGE))
    page = min(max(1, page), pages)
    rows = database.query(
        f"SELECT r.*, f.name AS flow_name FROM flow_runs r JOIN flows f ON f.id=r.flow_id {where} "
        f"ORDER BY r.id DESC LIMIT ? OFFSET ?",
        [*params, PAGE, (page - 1) * PAGE],
    )
    return {"runs": [run_brief(row) for row in rows], "total": total, "page": page, "pages": pages}


def _media(ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = db.get().query(f"SELECT * FROM media_cache WHERE id IN ({marks})", list(ids))
    return {row["id"]: media_store.view(row) for row in rows}


def _port_label(node: dict, port: str | None) -> str:
    if port is None:
        return ""
    node_type = TYPES.get(node.get("type", ""))
    if node_type is not None:
        if node_type.category == "output":
            return OUTCOMES.get(port, port)
        for known in node_type.ports:
            if known.key == port:
                return known.label
    return port


def run_detail(run_id: int) -> dict | None:
    database = db.get()
    row = database.one("SELECT r.*, f.name AS flow_name, f.version AS current_version FROM flow_runs r "
                       "JOIN flows f ON f.id=r.flow_id WHERE r.id=?", (run_id,))
    if not row:
        return None
    stats = db.loads(row["stats"], {}) or {}
    infos = stats.get("nodes") or {}
    order = stats.get("order") or list(infos)
    nodes = []
    for node_id in order:
        info = infos.get(node_id) or {}
        compare = info.get("compare")
        if compare:
            media = _media([*compare.get("flow_only", []), *compare.get("list_only", [])])
            compare = {
                "lists": [{"ref": entry["ref"], "run": entry.get("run"), "error": entry.get("error")}
                          for entry in compare.get("lists", [])],
                "both": len(compare.get("both", [])),
                "flow_only": [media[i] for i in compare.get("flow_only", []) if i in media],
                "list_only": [media[i] for i in compare.get("list_only", []) if i in media],
            }
        nodes.append({
            "id": node_id,
            "type": info.get("type", ""),
            "label": info.get("label", node_id),
            "category": info.get("category", ""),
            "summary": info.get("summary", ""),
            "count_in": int(info.get("in") or 0),
            "ports": [{"key": key, "label": _port_label(info, key), "count": int(count)}
                      for key, count in (info.get("out") or {}).items()],
            "entries": info.get("entries"),
            "run": info.get("run"),
            "error": info.get("error"),
            "compare": compare,
        })
    sources = database.query("SELECT * FROM parser_runs WHERE flow_run_id=? ORDER BY id", (run_id,))
    previous = database.scalar("SELECT id FROM flow_runs WHERE flow_id=? AND id<? ORDER BY id DESC LIMIT 1",
                               (row["flow_id"], run_id))
    following = database.scalar("SELECT id FROM flow_runs WHERE flow_id=? AND id>? ORDER BY id LIMIT 1",
                                (row["flow_id"], run_id))
    return {
        **run_brief(row),
        "notes": db.loads(row["notes"], []) or [],
        "current": row["flow_version"] == row["current_version"],
        "nodes": nodes,
        "sources": [list_runs.run_brief(source) for source in sources],
        "previous": previous,
        "next": following,
    }


def run_titles(run_id: int, show: str = "all", q: str = "", page: int = 1) -> dict | None:
    database = db.get()
    row = database.one("SELECT stats FROM flow_runs WHERE id=?", (run_id,))
    if row is None:
        return None
    stats = db.loads(row["stats"], {}) or {}
    infos = stats.get("nodes") or {}
    order = stats.get("order") or list(infos)
    rank = {node_id: index for index, node_id in enumerate(order)}
    outputs = [node_id for node_id in order if (infos.get(node_id) or {}).get("category") == "output"]
    trace = database.query(
        "SELECT t.media_id, t.node_id, t.port, t.detail, m.title FROM flow_trace t "
        "JOIN media_cache m ON m.id=t.media_id WHERE t.run_id=?", (run_id,))
    by_media: dict[int, list[dict]] = {}
    titles: dict[int, str] = {}
    for step in trace:
        by_media.setdefault(step["media_id"], []).append(step)
        titles[step["media_id"]] = step["title"] or ""

    def outcomes(media_id: int) -> list[dict]:
        return [step for step in by_media[media_id] if step["node_id"] in outputs]

    counts = {key: 0 for key in SHOW}
    for media_id in by_media:
        found = outcomes(media_id)
        counts["all"] += 1
        if not found:
            counts["filtered"] += 1
        for port in {step["port"] for step in found}:
            if port in counts:
                counts[port] += 1
    show = show if show in SHOW else "all"
    needle = q.strip().lower()
    chosen = []
    for media_id in by_media:
        found = outcomes(media_id)
        if show == "filtered" and found or show in OUTPUT_PORTS and not any(s["port"] == show for s in found):
            continue
        if needle and needle not in titles[media_id].lower():
            continue
        best = min((s["port"] for s in found), key=lambda port: OUTPUT_PORTS.index(port) if port in OUTPUT_PORTS
                   else 99, default="zz")
        chosen.append((OUTPUT_PORTS.index(best) if best in OUTPUT_PORTS else 99, titles[media_id].lower(), media_id))
    chosen.sort()
    total = len(chosen)
    pages = max(1, math.ceil(total / PAGE))
    page = min(max(1, page), pages)
    picked = [media_id for _, _, media_id in chosen[(page - 1) * PAGE: page * PAGE]]
    media = _media(picked)

    def step_view(step: dict) -> dict:
        info = infos.get(step["node_id"]) or {}
        return {"node": step["node_id"], "label": info.get("label", step["node_id"]),
                "category": info.get("category", ""), "port": step["port"],
                "port_label": _port_label(info, step["port"]), "detail": step["detail"] or ""}

    rows = []
    for media_id in picked:
        if media_id not in media:
            continue
        steps = sorted(by_media[media_id], key=lambda step: rank.get(step["node_id"], 999))
        rows.append({
            "media": media[media_id],
            "outcomes": [step_view(step) for step in steps if step["node_id"] in outputs],
            "steps": [step_view(step) for step in steps],
        })
    return {"rows": rows, "counts": counts, "total": total, "page": page, "pages": pages}


def failed_count() -> int:
    """Flows whose last run failed (dashboard)."""
    return db.get().scalar(
        "SELECT COUNT(*) FROM flow_runs WHERE status='error' "
        "AND id IN (SELECT MAX(id) FROM flow_runs GROUP BY flow_id)", (), 0)


# -- scheduling and housekeeping ---------------------------------------------------------------------

def due(now: int) -> list[dict]:
    rows = db.get().query(
        "SELECT * FROM flows WHERE enabled=1 AND schedule IS NOT NULL AND next_run_at IS NOT NULL "
        "AND next_run_at<=? ORDER BY next_run_at", (now,))
    return [_flow(row) for row in rows]


def scheduled(flow: dict, now: int) -> None:
    db.get().update("flows", "id", flow["id"], {"next_run_at": schedule.next_run(flow["schedule"], now)})


def recover() -> None:
    """Runs cut off by a restart."""
    db.get().execute(
        "UPDATE flow_runs SET status='error', error='Stopped by a restart', finished_at=? WHERE status='running'",
        (db.now(),),
    )


def prune() -> int:
    """Drops runs older than the setting; the last run of every flow stays."""
    days = max(1, int(settings.get("runs_keep_days") or 60))
    database = db.get()
    with database.transaction() as conn:
        removed = conn.execute(
            "DELETE FROM flow_runs WHERE started_at < ? AND id NOT IN (SELECT MAX(id) FROM flow_runs GROUP BY flow_id)",
            (db.now() - days * 86400,),
        ).rowcount
        conn.execute("UPDATE parser_runs SET flow_run_id=? WHERE flow_run_id > 0 "
                     "AND flow_run_id NOT IN (SELECT id FROM flow_runs)", (GONE,))
    return removed or 0
