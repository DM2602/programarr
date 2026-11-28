"""Webhooks from Sonarr, Radarr and Plex: start a sync soon after something new arrived.

Radarr and Sonarr name the title: the sync then also reads its folders, so a poster Radarr deleted
with the old release comes back from the backup.
"""
from __future__ import annotations

import hmac
import json
import time

from fastapi import APIRouter, HTTPException, Request

from .. import jobs, settings
from ..posters import tasks
from ..util import event

router = APIRouter(prefix="/api/hooks")

DELAY = 90  # seconds: let Plex scan the new files first
_pending_until = 0.0


def _check(token: str | None) -> None:
    expected = settings.get("hook_token")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(403, "Missing or invalid token")


def _schedule(reason: str) -> bool:
    global _pending_until
    now = time.time()
    if now < _pending_until:
        return False
    _pending_until = now + DELAY

    async def submit():
        jobs.runner.submit("sync", "Sync after webhook", tasks.sync_and_process, dedupe=True)

    jobs.runner.later(DELAY, submit)
    event("info", "webhook", f"{reason}: sync in {DELAY} s")
    return True


@router.post("/sonarr")
async def sonarr(request: Request, token: str | None = None):
    _check(token)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    kind = body.get("eventType", "")
    if kind == "Test":
        return {"ok": True, "message": "Programarr received the test"}
    if kind in ("Download", "SeriesAdd", "Rename", "ImportComplete"):
        series = body.get("series") or {}
        if kind != "SeriesAdd":
            tasks.name_title("show", {"tvdb_id": series.get("tvdbId"), "tmdb_id": series.get("tmdbId"),
                                      "imdb_id": series.get("imdbId")})
        _schedule(f"Sonarr {kind} ({series.get('title', 'Show')})")
    return {"ok": True}


@router.post("/radarr")
async def radarr(request: Request, token: str | None = None):
    _check(token)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    kind = body.get("eventType", "")
    if kind == "Test":
        return {"ok": True, "message": "Programarr received the test"}
    if kind in ("Download", "MovieAdded", "Rename"):
        movie = body.get("movie") or {}
        if kind != "MovieAdded":
            tasks.name_title("movie", {"tmdb_id": movie.get("tmdbId"), "imdb_id": movie.get("imdbId")})
        _schedule(f"Radarr {kind} ({movie.get('title', 'Movie')})")
    return {"ok": True}


@router.post("/plex")
async def plex(request: Request, token: str | None = None):
    _check(token)
    form = await request.form()
    try:
        payload = json.loads(form.get("payload") or "{}")
    except ValueError:
        payload = {}
    if payload.get("event") == "library.new":
        title = (payload.get("Metadata") or {}).get("grandparentTitle") or (payload.get("Metadata") or {}).get("title", "")
        _schedule(f"Plex: new in library ({title})")
    return {"ok": True}
