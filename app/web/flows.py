"""Flows: editing, running (dry run or live), their history title by title, and Radarr/Sonarr options."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException

from .. import arr, db
from ..arr import library
from ..flows import engine, schedule, store
from ..flows import graph as graphs
from ..flows.nodes import TYPES
from .schemas import (
    ArrOptions,
    FlowCheck,
    FlowCheckBody,
    FlowCreate,
    FlowRunBody,
    FlowRunDetail,
    FlowRunsPage,
    FlowsPage,
    FlowTitlesPage,
    FlowUpdate,
    FlowView,
    JobStarted,
    NodeTypes,
    Ok,
    ScheduleCheck,
)

router = APIRouter(prefix="/api", tags=["flows"])


def _flow_or_404(flow_id: int) -> dict:
    flow = store.get(flow_id)
    if flow is None:
        raise HTTPException(404, "Flow not found")
    return flow


@router.get("/flows", response_model=FlowsPage)
async def flows():
    return {"flows": store.flows()}


@router.post("/flows", response_model=FlowView)
async def create(body: FlowCreate):
    try:
        flow_id = store.create(body.name, body.graph.model_dump() if body.graph else None, body.schedule)
    except (ValueError, schedule.ScheduleError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return store.view(store.get(flow_id))


@router.get("/flows/nodes", response_model=NodeTypes)
async def node_types():
    return {"types": TYPES.infos()}


@router.post("/flows/check", response_model=FlowCheck)
async def check(body: FlowCheckBody):
    checked = graphs.check(body.graph.model_dump())
    infos = graphs.summaries(checked)
    return {"problems": [problem.as_dict() for problem in checked.problems],
            "summaries": {node_id: info["summary"] for node_id, info in infos.items()},
            "ports": {node_id: [{"key": key, "label": label} for key, label in info["ports"].items()]
                      for node_id, info in infos.items()}}


@router.get("/flows/schedule", response_model=ScheduleCheck)
async def check_schedule(value: str = ""):
    try:
        normalised = schedule.normalise(value)
    except schedule.ScheduleError as exc:
        return {"ok": False, "schedule": None, "text": "", "next_run_at": None, "message": str(exc)}
    return {"ok": True, "schedule": normalised, "text": schedule.describe(normalised),
            "next_run_at": schedule.next_run(normalised, db.now()), "message": None}


@router.get("/flows/runs", response_model=FlowRunsPage)
async def runs(flow: int = 0, page: int = 1):
    return store.runs(flow or None, page)


@router.get("/flows/runs/{run_id}", response_model=FlowRunDetail)
async def run_detail(run_id: int):
    detail = store.run_detail(run_id)
    if detail is None:
        raise HTTPException(404, "Run not found")
    return detail


@router.get("/flows/runs/{run_id}/titles", response_model=FlowTitlesPage)
async def run_titles(run_id: int, show: str = "all", q: str = "", page: int = 1):
    result = store.run_titles(run_id, show, q, page)
    if result is None:
        raise HTTPException(404, "Run not found")
    return result


@router.get("/flows/{flow_id}", response_model=FlowView)
async def flow(flow_id: int):
    return store.view(_flow_or_404(flow_id))


@router.put("/flows/{flow_id}", response_model=FlowView)
async def update(flow_id: int, body: FlowUpdate):
    _flow_or_404(flow_id)
    sent = body.model_fields_set
    if "dry_run" in sent and body.dry_run is False:
        # Going live needs a flow that passes its checks.
        current = store.get(flow_id)
        graph = body.graph.model_dump() if body.graph else current["graph"]
        checked = graphs.check(graph)
        if not checked.ok:
            raise HTTPException(400, f"Fix the flow first: {checked.problems[0].message}")
    try:
        updated = store.update(
            flow_id,
            name=body.name if "name" in sent and body.name is not None else None,
            graph=body.graph.model_dump() if body.graph is not None else None,
            schedule_text=body.schedule if "schedule" in sent else store.UNSET,
            enabled=body.enabled if "enabled" in sent else None,
            dry_run=body.dry_run if "dry_run" in sent else None,
        )
    except (ValueError, schedule.ScheduleError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return store.view(updated)


@router.delete("/flows/{flow_id}", response_model=Ok)
async def delete(flow_id: int):
    if engine.busy(flow_id):
        raise HTTPException(409, "This flow is running")
    if not store.delete(flow_id):
        raise HTTPException(404, "Flow not found")
    return {"ok": True, "message": "Flow deleted"}


@router.post("/flows/{flow_id}/run", response_model=JobStarted)
async def run(flow_id: int, body: FlowRunBody | None = None):
    _flow_or_404(flow_id)
    try:
        job = engine.start(flow_id, "manual", body.dry_run if body else None)
    except engine.FlowError as exc:
        raise HTTPException(409 if "running" in str(exc) else 400, str(exc)) from exc
    return {"job": job}


@router.get("/arr/{service}/options", response_model=ArrOptions)
async def arr_options(service: Literal["radarr", "sonarr"], fresh: bool = False):
    result = {"service": service, "configured": arr.configured(service), "profiles": [], "root_folders": [],
              "tags": [], "error": None}
    if not result["configured"]:
        return result
    try:
        result.update(await library.options(service, fresh=fresh))
    except arr.ArrError as exc:
        result["error"] = str(exc)
    return result
