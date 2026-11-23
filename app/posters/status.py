"""Ended shows and "Complete": a show that has ended and has every poster as a file."""
from __future__ import annotations

import asyncio

from . import catalog
from .. import db
from .sources import tmdb, tvmaze
from .sources.base import SourceError
from ..util import event

ENDED = {"ended", "canceled", "cancelled"}
RUNNING = {"returning series", "in production", "planned", "pilot", "running", "to be determined", "in development"}
MAX_AGE = {"running": 7 * 86400, "ended": 30 * 86400, None: 14 * 86400}
BATCH = 40

# Show slot and every season in Plex (without specials) have a file.
DONE_SQL = (
    "(i.type='show' AND COALESCE(i.show_status, '')='ended'"
    " AND EXISTS (SELECT 1 FROM slots fa WHERE fa.item_key=i.key AND fa.kind='show' AND fa.file IS NOT NULL)"
    " AND NOT EXISTS (SELECT 1 FROM seasons fs LEFT JOIN slots fz ON fz.item_key=fs.show_key AND fz.kind='season'"
    " AND fz.season=fs.number WHERE fs.show_key=i.key AND fs.gone=0 AND fs.number<>0 AND fz.file IS NULL))"
)


def normalize(raw: str | None) -> str | None:
    value = (raw or "").strip().lower()
    if value in ENDED:
        return "ended"
    if value in RUNNING:
        return "running"
    return None


def is_done(item_key: str) -> bool:
    return bool(db.get().scalar(f"SELECT 1 FROM items i WHERE i.key=? AND {DONE_SQL}", (item_key,), 0))


def done_keys(keys: list[str] | None = None) -> set[str]:
    rows = db.get().query(f"SELECT i.key FROM items i WHERE i.gone=0 AND {DONE_SQL}")
    found = {row["key"] for row in rows}
    return found if keys is None else found & set(keys)


async def fetch(item: dict) -> str | None:
    """Ended or running, from TMDB (with key) or TVmaze; None if neither knows."""
    if tmdb.configured():
        try:
            item = await catalog.ensure_ids(item)
            if item.get("tmdb_id"):
                data = await tmdb._get(f"/tv/{item['tmdb_id']}")
                state = normalize((data or {}).get("status"))
                if state:
                    return state
        except SourceError:
            pass
    if tvmaze.configured():
        for param, value in (("thetvdb", item.get("tvdb_id")), ("imdb", item.get("imdb_id"))):
            if not value:
                continue
            data = await tvmaze._get("/lookup/shows", {param: value})
            if data:
                return normalize(data.get("status"))
    return None


async def refresh(item_key: str) -> str | None:
    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    if not item or item["type"] != "show":
        return None
    state = await fetch(item)
    database.update("items", "key", item_key, {"show_status": state, "status_at": db.now()})
    return state


async def refresh_stale(limit: int = BATCH) -> int:
    """Look up the status of a few shows per run; ended shows are checked less often."""
    if not (tmdb.configured() or tvmaze.configured()):
        return 0
    now = db.now()
    due = db.get().query(
        "SELECT key FROM items WHERE type='show' AND gone=0 AND (status_at IS NULL OR status_at < ? - CASE show_status "
        "WHEN 'ended' THEN ? WHEN 'running' THEN ? ELSE ? END) ORDER BY status_at IS NOT NULL, status_at LIMIT ?",
        (now, MAX_AGE["ended"], MAX_AGE["running"], MAX_AGE[None], limit))
    done = 0
    for row in due:
        try:
            await refresh(row["key"])
            done += 1
        except SourceError as exc:
            event("warning", "status", f"Cannot get show status: {exc}", row["key"])
            break
        await asyncio.sleep(0.4)
    return done


def forget(item_key: str) -> None:
    """A new season showed up: look again soon."""
    db.get().execute("UPDATE items SET status_at=NULL WHERE key=?", (item_key,))
