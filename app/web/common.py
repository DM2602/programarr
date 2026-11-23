"""Small helpers shared by the API routers."""
from __future__ import annotations

from fastapi import HTTPException

from .. import db, jobs
from ..posters.sources.base import parse_slot_id


def item_or_404(key: str) -> dict:
    item = db.get().one("SELECT * FROM items WHERE key=?", (key,))
    if not item:
        raise HTTPException(404, "Title not found")
    return item


def start_job(kind: str, title: str, func, *args, lane: str = "ui", dedupe: bool = False) -> dict:
    return {"job": jobs.runner.submit(kind, title, func, *args, lane=lane, dedupe=dedupe)}


def slot_of(item: dict, slot: str) -> tuple[str, int | None]:
    try:
        kind, key, season = parse_slot_id(slot)
    except (ValueError, IndexError) as exc:
        raise HTTPException(400, f"Unknown poster slot: {slot}") from exc
    if key != item["key"]:
        raise HTTPException(400, "Slot belongs to another title")
    return kind, season


def job_row(row: dict | None) -> dict | None:
    if row is None:
        return None
    row["result"] = db.loads(row.get("result"), None)
    return row
