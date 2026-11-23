"""App-wide API: status, dashboard, jobs, logs, settings, setup and connection tests."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import config, db, http, jobs, notify, settings
from ..plex import Plex, PlexError
from ..posters import library, tasks, views
from ..posters.sources import fanart, mediux, tmdb, tpdb
from ..posters.sources.base import SourceError
from ..util import event
from .common import job_row, start_job
from .schemas import (Job, JobStarted, Libraries, LogPage, Ok, Overview, PathCheck, Saved, SettingsView, Status,
                      TestBody, TestResult, EnabledBody)

router = APIRouter(prefix="/api", tags=["system"])

JOB_FIELDS = "id, kind, title, status, done, total, message"


# -- status and dashboard --------------------------------------------------------------
@router.get("/status", response_model=Status)
async def status():
    running = db.get().query(f"SELECT {JOB_FIELDS} FROM jobs WHERE status IN ('queued','running') ORDER BY id")
    return {
        "version": config.VERSION,
        "setup_done": settings.get("setup_done"),
        "automation": settings.get("automation"),
        "last_sync": settings.get("last_sync") or 0,
        "counts": views.counts(),
        "jobs": running,
    }


@router.get("/overview", response_model=Overview)
async def overview():
    counts = views.counts()
    recent_jobs = [job_row(row) for row in db.get().query("SELECT * FROM jobs ORDER BY id DESC LIMIT 6")]
    return {
        "setup_done": settings.get("setup_done"),
        "automation": settings.get("automation"),
        "steps": views.setup_steps(),
        "last_sync": settings.get("last_sync") or 0,
        "last_set_check": settings.get("last_set_check") or 0,
        "todo": {
            "review": counts["review"],
            "waiting": counts["waiting"],
            "problems": views.problems(),
            "unlinked_with_files": views.unlinked_with_files("show"),
            "detect_open": counts["review_kinds"].get("detect", 0),
        },
        "recent": views.recent_cards(),
        "facts": {"movies": counts["movies"], "shows": counts["shows"], "linked": counts["linked"],
                  "complete": views.complete_count()},
        "jobs": recent_jobs,
    }


# -- jobs -------------------------------------------------------------------------------
@router.get("/jobs/{job_id}", response_model=Job)
async def job_status(job_id: int):
    row = job_row(db.get().one("SELECT * FROM jobs WHERE id=?", (job_id,)))
    if not row:
        raise HTTPException(404, "Job not found")
    return row


@router.post("/jobs/{job_id}/cancel", response_model=Ok)
async def job_cancel(job_id: int):
    jobs.runner.cancel(job_id)
    return {"ok": True, "message": "Cancelling"}


@router.post("/sync", response_model=JobStarted)
async def sync(full: bool = False):
    return start_job("sync", "Full sync with Plex" if full else "Sync with Plex", tasks.sync_and_process, full,
                     lane="bg", dedupe=True)


# -- log --------------------------------------------------------------------------------
@router.get("/log", response_model=LogPage)
async def log(level: str = ""):
    sql = "SELECT e.id, e.at, e.level, e.area, e.message, e.item_key, i.title FROM events e " \
          "LEFT JOIN items i ON i.key=e.item_key"
    if level == "warning":
        sql += " WHERE e.level IN ('warning','error')"
    elif level == "error":
        sql += " WHERE e.level='error'"
    sql += " ORDER BY e.id DESC LIMIT 400"
    rows = db.get().query(sql)
    for row in rows:
        if row["level"] not in ("info", "warning", "error"):
            row["level"] = "info"
    return {"rows": rows}


# -- settings -----------------------------------------------------------------------------
EDITABLE = {key for key in settings.DEFAULTS if key not in settings.INTERNAL} - {"hook_token", "setup_done"}
URL_KEYS = ("plex_url", "mediux_api", "mediux_site", "tpdb_base", "flaresolverr_url", "base_url", "tmdb_api",
            "tmdb_image", "fanart_api", "tvmaze_api")
CHOICES = {
    "naming_movie": ("auto", "poster", "file"),
    "naming_show": ("auto", "poster", "show", "folder"),
    "naming_season": ("auto", "root", "root_plain", "folder"),
    "conflicts": ("archive", "keep"),
    "mediux_quality": ("original", "optimized"),
    "set_changes": ("notify", "apply"),
}


def _hook_base(request: Request) -> str:
    return (settings.get("base_url") or str(request.base_url)).rstrip("/")


@router.get("/settings", response_model=SettingsView)
async def get_settings(request: Request):
    base = _hook_base(request)
    token = settings.get("hook_token")
    follows = settings.get("mediux_follows") or {}
    return {
        "values": settings.public_view(),
        "libraries": library.libraries(),
        "hooks": {name: f"{base}/api/hooks/{name}?token={token}" for name in ("sonarr", "radarr", "plex")},
        "discord_events": [{"key": key, "label": label} for key, label in settings.DISCORD_EVENTS.items()],
        "naming_detected": settings.get("naming_detected") or {},
        "follows": {"follow": follows.get("follow", []), "hide": follows.get("hide", [])},
        "data_dir": str(config.DATA_DIR),
        "version": config.VERSION,
    }


def _clean_designers(value) -> list[dict]:
    result, seen = [], set()
    for entry in value or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip().lstrip("@")
        source = entry.get("source", "any")
        source = source if source in ("any", "mediux", "tpdb") else "any"
        if not name or (name.casefold(), source) in seen:
            continue
        seen.add((name.casefold(), source))
        result.append({"source": source, "name": name[:64]})
    return result[:60]


def _clean_path_maps(value) -> list[dict]:
    result = []
    for entry in value or []:
        if not isinstance(entry, dict):
            continue
        plex_path, local = str(entry.get("plex", "")).strip(), str(entry.get("local", "")).strip()
        if plex_path and local:
            result.append({"plex": plex_path, "local": local})
    return result


@router.post("/settings", response_model=Saved)
async def save_settings(body: dict):
    """Partial update. Secrets: empty or true keeps the stored value, false clears it."""
    changes = {}
    for key, value in body.items():
        if key not in EDITABLE:
            continue
        if key in settings.SECRETS and value in (None, "", True):
            continue
        if key in settings.SECRETS and value is False:
            value = ""
        default = settings.DEFAULTS[key]
        if isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Invalid value for {key}") from None
        elif isinstance(default, float):
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Invalid value for {key}") from None
        elif isinstance(default, str):
            if value is None:
                value = ""
            if not isinstance(value, str) or (key in CHOICES and value not in CHOICES[key]):
                raise HTTPException(400, f"Invalid value for {key}")
        if key == "designers":
            value = _clean_designers(value)
        elif key == "path_maps":
            value = _clean_path_maps(value)
        elif key == "source_order":
            order = [str(v) for v in (value or []) if v in ("mediux", "tpdb")]
            value = order + [v for v in ("mediux", "tpdb") if v not in order]
        elif key == "discord_events":
            value = {k: bool(v) for k, v in (value or {}).items() if k in settings.DISCORD_EVENTS}
        elif key in URL_KEYS:
            value = str(value or "").strip().rstrip("/")
        changes[key] = value
    settings.update(changes)
    if "tpdb_email" in changes or "tpdb_password" in changes:
        settings.set("tpdb_cookies", {})
    if "mediux_token" in changes or "use_mediux_follows" in changes:
        tasks.seed_follows_async()
    if "automation" in changes:
        event("info", "status", "Automation on" if changes["automation"] else "Automation off")
    return {"ok": True, "saved": sorted(changes)}


@router.post("/libraries/refresh", response_model=Libraries)
async def libraries_refresh():
    try:
        await library.refresh_libraries()
    except PlexError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"libraries": library.libraries()}


@router.post("/libraries", response_model=Ok)
async def libraries_save(body: EnabledBody):
    enabled = {str(key) for key in body.enabled}
    database = db.get()
    for lib in library.libraries():
        database.execute("UPDATE libraries SET enabled=? WHERE key=?", (1 if lib["key"] in enabled else 0, lib["key"]))
    return {"ok": True, "message": "Saved"}


@router.get("/paths/check", response_model=PathCheck)
async def paths_check():
    return library.folder_status()


@router.post("/setup/finish", response_model=JobStarted)
async def setup_finish():
    settings.set("setup_done", True)
    event("info", "sync", "Setup finished, first sync starts")
    return start_job("sync", "First sync with Plex", tasks.sync_and_process, True, lane="bg", dedupe=True)


# -- connection tests ---------------------------------------------------------------------
@router.post("/test/{what}", response_model=TestResult)
async def test_connection(what: str, body: TestBody):
    try:
        if what == "plex":
            url = body.extra or settings.get("plex_url")
            token = body.value or settings.get("plex_token")
            info = await Plex(url, token).identity()
            return {"ok": True, "message": f"Connected to {info['name']} (Plex {info['version']})"}
        if what == "mediux":
            name = await mediux.test(body.value or None)
            return {"ok": True, "message": f"MediUX token is valid ({name})"}
        if what == "tpdb":
            name = await tpdb.login()
            return {"ok": True, "message": f"Signed in to ThePosterDB ({name})"}
        if what == "tmdb":
            return {"ok": True, "message": await tmdb.test(body.value or None)}
        if what == "fanart":
            return {"ok": True, "message": await fanart.test(body.value or None)}
        if what == "flaresolverr":
            endpoint = (body.value or settings.get("flaresolverr_url")).rstrip("/")
            if not endpoint:
                return {"ok": False, "message": "No address"}
            async with http.client(timeout=20.0) as client:
                response = await client.get(endpoint + "/")
            data = response.json()
            return {"ok": True, "message": data.get("msg") or f"FlareSolverr {data.get('version', '')} reachable"}
        if what == "discord":
            ok = await notify.send("test", "Programarr is connected", "This is how Programarr messages look.",
                                   webhook=body.value or None)
            return {"ok": ok, "message": "Test message sent" if ok else "Discord rejected the message"}
    except (SourceError, PlexError, http.FetchError) as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - shown next to the button
        return {"ok": False, "message": f"Error: {exc.__class__.__name__}"}
    raise HTTPException(404, "Unknown test")
