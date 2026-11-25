"""Runs a flow: reads the sources, sends each title through the filters and hands the rest to Radarr and Sonarr.

Every title leaves a trace per node (port and detail), so the run page can show its path.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from .. import db, jobs, notify, settings
from ..lists import exclusions as exclusion_sync
from ..lists import runs as list_runs
from ..lists.flixpatrol import SECTIONS
from ..media import store as media_store
from ..util import event
from . import graph as graphs
from . import outputs, store
from .nodes import ArrOutput, Filter, PlexFilter, Title

log = logging.getLogger("programarr.flows")

_jobs: dict[int, int] = {}  # flow id -> job id while queued or running


class FlowError(Exception):
    """A flow cannot run; the message is meant for the UI."""


def busy(flow_id: int) -> bool:
    job_id = _jobs.get(flow_id)
    if job_id is None:
        return False
    row = db.get().one("SELECT status FROM jobs WHERE id=?", (job_id,))
    if row and row["status"] in ("queued", "running"):
        return True
    _jobs.pop(flow_id, None)
    return False


def start(flow_id: int, trigger: str = "manual", dry_run: bool | None = None) -> int:
    """Queue a run in the flows lane; raises FlowError when it cannot start."""
    flow = store.get(flow_id)
    if flow is None:
        raise FlowError("Flow not found")
    if busy(flow_id):
        raise FlowError("This flow is running")
    if trigger == "manual":
        checked = graphs.check(flow["graph"])
        if not checked.ok:
            raise FlowError(f"Fix the flow first: {checked.problems[0].message}")
    dry = bool(flow["dry_run"]) if dry_run is None else bool(dry_run)
    title = f"{'Dry run' if dry else 'Run'}: {flow['name']}"
    job_id = jobs.runner.submit("flow", title, run, flow_id, trigger, dry, lane="flows")
    _jobs[flow_id] = job_id
    return job_id


def start_due(now: int) -> int:
    """Called by the scheduler: queue every flow whose time has come."""
    started = 0
    for flow in store.due(now):
        store.scheduled(flow, now)
        if busy(flow["id"]):
            continue
        try:
            start(flow["id"], trigger="schedule")
            started += 1
        except FlowError as exc:
            log.warning("Flow %s: %s", flow["name"], exc)
    return started


# -- running ---------------------------------------------------------------------------------------

async def run(flow_id: int, trigger: str = "manual", dry_run: bool | None = None) -> dict:
    database = db.get()
    flow = store.get(flow_id)
    if flow is None:
        return {"run": None, "outcome": "error", "message": "Flow not found"}
    dry = bool(flow["dry_run"]) if dry_run is None else bool(dry_run)
    checked = graphs.check(flow["graph"])
    infos = graphs.summaries(checked)
    run_id = database.execute(
        "INSERT INTO flow_runs (flow_id, flow_version, trigger, dry_run, started_at, status, stats) "
        "VALUES (?,?,?,?,?, 'running', ?)",
        (flow_id, flow["version"], trigger, int(dry), db.now(), db.dumps({"nodes": infos, "order": checked.order})),
    ).lastrowid
    notes: list[str] = []
    try:
        if not checked.ok:
            raise FlowError("Fix the flow first: " + "; ".join(problem.message for problem in checked.problems[:3]))
        stats = await _execute(run_id, checked, infos, dry, notes)
    except FlowError as exc:
        _finish(run_id, "error", None, notes, str(exc))
        await _notify(flow, run_id, dry, "error", {}, str(exc))
        return {"run": run_id, "outcome": "error", "dry_run": dry, "message": str(exc)}
    except jobs.Cancelled:
        _finish(run_id, "error", None, notes, "Cancelled")
        raise
    except Exception as exc:
        log.exception("Flow %s failed", flow["name"])
        _finish(run_id, "error", None, notes, f"Error: {exc}")
        await _notify(flow, run_id, dry, "error", {}, str(exc))
        raise
    totals = stats["totals"]
    status = "partial" if notes or totals["failed"] else "ok"
    _finish(run_id, status, stats, notes, None)
    message = _message(dry, totals)
    if status == "partial":
        event("warning", "flows", f"{flow['name']}: {message}; {'; '.join(notes[:2])}" if notes else message)
    await _notify(flow, run_id, dry, status, totals, None, stats)
    return {"run": run_id, "outcome": status, "dry_run": dry, "message": message, **totals}


def _finish(run_id: int, status: str, stats: dict | None, notes: list[str], error: str | None) -> None:
    values = {"status": status, "finished_at": db.now(), "error": error, "notes": db.dumps(notes) if notes else None}
    if stats is not None:
        values["stats"] = db.dumps(stats)
    db.get().update("flow_runs", "id", run_id, values)


def _rank(entry: dict, extra: dict) -> int | None:
    value = extra.get("rank")
    if isinstance(value, int) and value > 0:
        return value
    return entry["position"] or None


def _merge(checked: graphs.Checked, node_id: str, sent: dict[str, dict[str, dict[int, Title]]]) -> dict[int, Title]:
    """The titles on all connections into a node; a title from several lists keeps its best position."""
    merged: dict[int, Title] = {}
    for edge in checked.inputs(node_id):
        for media_id, title in sent.get(edge["source"], {}).get(edge["port"], {}).items():
            current = merged.get(media_id)
            if current is None:
                merged[media_id] = title
            elif title.rank is not None and (current.rank is None or title.rank < current.rank):
                merged[media_id] = replace(current, rank=title.rank)
    return merged


async def _read(run_id: int, node_id: str, source: str, config: dict, info: dict, notes: list[str],
                trace: dict[tuple[int, str], tuple[str | None, str]]) -> tuple[dict[int, Title], dict]:
    jobs.progress(message=f"Reading {info['summary'] or info['label']}")
    outcome = await list_runs.preview(source, config, flow_run_id=run_id)
    adapter = list_runs.adapter(source)
    # Read again: a source can learn its title while reading (MDBList).
    stats: dict = {"run": outcome["run"], "in": 0, "out": {"out": 0}, "name": adapter.name(config),
                   "summary": adapter.describe(config)}
    if outcome["outcome"] == "error":
        notes.append(f"{info['label']}: {outcome['message']}")
        stats["error"] = outcome["message"]
        return {}, stats
    entries = db.get().query(
        "SELECT position, extra, media_id, status FROM parser_entries WHERE run_id=? ORDER BY position, id",
        (outcome["run"],))
    counts = {"matched": 0, "uncertain": 0, "failed": 0, "excluded": 0}
    titles: dict[int, Title] = {}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        media_id = entry["media_id"]
        if not media_id:
            continue
        extra = db.loads(entry["extra"], {}) or {}
        rank = _rank(entry, extra)
        if entry["status"] == "matched":
            current = titles.get(media_id)
            if current is None or rank is not None and (current.rank is None or rank < current.rank):
                titles[media_id] = Title(media_id, {}, rank)
                section = SECTIONS.get(extra.get("section") or "")
                trace[(media_id, node_id)] = ("out", f"#{rank} {section}" if rank and section else
                                              f"#{rank}" if rank else "")
        elif (media_id, node_id) not in trace:
            trace[(media_id, node_id)] = (None, "Excluded" if entry["status"] == "excluded"
                                          else "Uncertain match, see the match log")
    rows = {row["id"]: row for row in db.get().query(
        f"SELECT * FROM media_cache WHERE id IN ({','.join('?' for _ in titles)})", list(titles))} if titles else {}
    for media_id, title in list(titles.items()):
        if media_id in rows:
            title.media = rows[media_id]
        else:
            titles.pop(media_id)
    stats.update({"entries": counts, "in": len(entries), "out": {"out": len(titles)}})
    return titles, stats


async def _execute(run_id: int, checked: graphs.Checked, infos: dict, dry: bool, notes: list[str]) -> dict:
    # Exclusions first, so titles excluded in Radarr or Sonarr since the last run never get through.
    # A dry run only reads them: what waits to be written there stays for the next sync.
    jobs.progress(message="Syncing exclusions with Radarr and Sonarr")
    for result in (await exclusion_sync.sync(write=not dry)).values():
        if result.get("error"):
            notes.append(f"Exclusions: {result['error']}")
    sent: dict[str, dict[str, dict[int, Title]]] = {}
    trace: dict[tuple[int, str], tuple[str | None, str]] = {}
    nodes: dict[str, dict] = {node_id: dict(info) for node_id, info in infos.items()}
    totals = {port: 0 for port in store.OUTPUT_PORTS}
    compared_lists: dict[str, dict] = {}
    for node_id in checked.order:
        node_type = checked.type_of(node_id)
        config = checked.configs[node_id]
        info = nodes[node_id]
        if node_type.category == "source":
            titles, stats = await _read(run_id, node_id, node_type.key, config, info, notes, trace)
            sent[node_id] = {"out": titles}
            info.update(stats)
            continue
        incoming = _merge(checked, node_id, sent)
        info["in"] = len(incoming)
        if isinstance(node_type, Filter):
            if isinstance(node_type, PlexFilter):
                plex = media_store.in_plex(list(incoming))
                incoming = {media_id: replace(title, in_plex=media_id in plex) for media_id, title in incoming.items()}
            ports: dict[str, dict[int, Title]] = {port.key: {} for port in node_type.ports_for(config)}
            for title in incoming.values():
                keys, detail = node_type.route(config, title)
                for key in keys:
                    ports.setdefault(key, {})[title.media_id] = title
                trace[(title.media_id, node_id)] = (",".join(keys), detail)  # several when each fitting rule counts
            sent[node_id] = ports
            info["out"] = {port: len(titles) for port, titles in ports.items()}
            continue
        assert isinstance(node_type, ArrOutput)
        titles = list(incoming.values())
        result = await outputs.send(node_type, config, titles, dry)
        for media_id, verdict in result.verdicts.items():
            trace[(media_id, node_id)] = (verdict.port, verdict.detail)
        notes.extend(result.notes)
        info["out"] = {port: count for port, count in result.counts.items() if count}
        for port, count in result.counts.items():
            totals[port] += count
        compared = await outputs.compare(node_type, config, titles, run_id, compared_lists)
        if compared is not None:
            info["compare"] = compared
            failed = [entry["ref"] for entry in compared["lists"] if entry["error"]]
            if failed:
                notes.append(f"Compare with MDBList failed for {', '.join(failed)}")
    rows = [(run_id, media_id, node_id, port, detail[:500]) for (media_id, node_id), (port, detail) in trace.items()]
    database = db.get()
    with database.transaction() as conn:
        conn.executemany("INSERT OR REPLACE INTO flow_trace (run_id, media_id, node_id, port, detail) "
                         "VALUES (?,?,?,?,?)", rows)
    return {
        "nodes": nodes,
        "order": checked.order,
        "totals": totals,
        "titles": len({media_id for media_id, _ in trace}),
    }


def _message(dry: bool, totals: dict) -> str:
    parts = []
    if totals["added"]:
        parts.append(f"{totals['added']} to add" if dry else f"{totals['added']} added")
    if totals["tagged"]:
        parts.append(f"{totals['tagged']} to tag" if dry else f"{totals['tagged']} tagged")
    if totals["present"]:
        parts.append(f"{totals['present']} already there")
    if totals["failed"]:
        parts.append(f"{totals['failed']} failed")
    text = ", ".join(parts) if parts else "Nothing to add"
    return f"Dry run: {text}" if dry else text[:1].upper() + text[1:]


async def _notify(flow: dict, run_id: int, dry: bool, status: str, totals: dict, error: str | None,
                  stats: dict | None = None) -> None:
    """Discord summary: what was added or tagged, and errors (once per error, not every schedule)."""
    if not settings.get("discord_webhook"):
        return
    path = f"/flows/runs/{run_id}"
    title = f"{flow['name']}{' (dry run)' if dry else ''}"
    if status == "error":
        previous = db.get().one("SELECT status, error FROM flow_runs WHERE flow_id=? AND id<? ORDER BY id DESC LIMIT 1",
                                (flow["id"], run_id))
        if previous and previous["status"] == "error" and previous["error"] == error:
            return
        await notify.send("error", f"Flow failed: {title}", error or "", path=path)
        return
    if not (totals.get("added") or totals.get("tagged") or totals.get("failed")):
        return
    lines = []
    names = _added_names(run_id, stats or {})
    for label, verb_dry, verb_live, key in (("Add", "to add", "added", "added"), ("Tags", "to tag", "tagged", "tagged")):
        count = totals.get(key) or 0
        if count:
            listed = ", ".join(names.get(key, [])[:8])
            more = f" and {count - 8} more" if count > 8 else ""
            lines.append(f"**{count} {verb_dry if dry else verb_live}**: {listed}{more}")
    if totals.get("failed"):
        lines.append(f"**{totals['failed']} failed**")
    await notify.send("flow_dry" if dry else "flow", title, "\n".join(lines), path=path)


def _added_names(run_id: int, stats: dict) -> dict[str, list[str]]:
    outputs_ids = [node_id for node_id, info in (stats.get("nodes") or {}).items() if info.get("category") == "output"]
    if not outputs_ids:
        return {}
    marks = ",".join("?" for _ in outputs_ids)
    rows = db.get().query(
        f"SELECT t.port, m.title, m.year FROM flow_trace t JOIN media_cache m ON m.id=t.media_id "
        f"WHERE t.run_id=? AND t.node_id IN ({marks}) AND t.port IN ('added','tagged') ORDER BY m.title",
        (run_id, *outputs_ids))
    names: dict[str, list[str]] = {}
    for row in rows:
        names.setdefault(row["port"], []).append(f"{row['title']} ({row['year']})" if row["year"] else row["title"])
    return names
