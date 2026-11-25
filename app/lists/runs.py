"""Preview runs: read a list source, match every entry and keep the outcome as a parser run.

Corrections (fix, ignore) and exclusions apply to every stored entry of the same raw title, so the
match log shows the current state and the next run uses them.
"""
from __future__ import annotations

from .. import db, jobs, settings
from ..media import mdblist, normalize, store, tmdb
from ..util import event
from .base import Adapter, RawEntry, SourceError
from .flixpatrol import FlixPatrol
from .mdblist import Mdblist
from .tmdb import Tmdb
from .trakt import Trakt

ADAPTERS: dict[str, Adapter] = {adapter.key: adapter for adapter in (FlixPatrol(), Mdblist(), Tmdb(), Trakt())}
STATUSES = ("matched", "uncertain", "failed", "excluded")
OPEN = ("uncertain", "failed")
PAGE = 50


def adapter(key: str) -> Adapter:
    found = ADAPTERS.get(key)
    if found is None:
        raise SourceError("Unknown source")
    return found


def prepare(source: str, params: dict) -> dict:
    """Checked parameters for a run; raises SourceError with a message for the form."""
    chosen = adapter(source)
    missing = chosen.missing()
    if missing:
        raise SourceError(missing)
    return chosen.clean(params or {})


# -- running ---------------------------------------------------------------------------------------

async def preview(source: str, params: dict, flow_run_id: int | None = None) -> dict:
    """Read and match one list; the result is a parser run. Takes checked parameters (see prepare)."""
    chosen = adapter(source)
    database = db.get()
    label = chosen.describe(params)
    run_id = database.execute(
        "INSERT INTO parser_runs (flow_run_id, source, source_ref, label, started_at, status, params, "
        "raw_count, matched, uncertain, failed, excluded) VALUES (?,?,?,?,?, 'running', ?, 0,0,0,0,0)",
        (flow_run_id, source, chosen.ref(params), label, db.now(), db.dumps(params)),
    ).lastrowid
    try:
        jobs.progress(message="Reading the list")
        fetched = await chosen.fetch(params)
        notes = list(fetched.notes)
        if chosen.describe(params) != label:  # the source told its title while reading (MDBList)
            label = chosen.describe(params)
            database.update("parser_runs", "id", run_id, {"label": label})
        rows = []
        for index, entry in enumerate(fetched.entries):
            jobs.progress(index, len(fetched.entries), f"Matching {entry.title}")
            fp, match = await normalize.resolve(source, entry)
            rows.append((run_id, entry.position, entry.title, entry.year, entry.kind, db.dumps(entry.ids),
                         entry.url, db.dumps(entry.extra), fp, match.method, match.media_id, match.status,
                         match.score, db.dumps(match.candidates), match.error))
    except SourceError as exc:
        _finish(run_id, "error", error=str(exc))
        event("warning", "lists", f"{label}: {exc}")
        return {"run": run_id, "outcome": "error", "message": str(exc)}
    except BaseException as exc:
        _finish(run_id, "error", error="Cancelled" if isinstance(exc, jobs.Cancelled) else f"Error: {exc}")
        raise
    with database.transaction():
        database.executemany(
            "INSERT INTO parser_entries (run_id, position, raw_title, raw_year, raw_kind, raw_ids, raw_url, extra, "
            "fingerprint, method, media_id, status, score, candidates, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    media_ids = sorted({row[10] for row in rows if row[10]})
    try:
        jobs.progress(len(rows), len(rows), "Loading ratings")
        await mdblist.refresh_ratings(media_ids)
    except mdblist.MdblistError as exc:
        notes.append(f"Ratings: {exc}")
    tmdb_errors = sum(1 for row in rows if row[9] == "error")
    if tmdb_errors:
        notes.append(f"TMDB could not answer for {tmdb_errors} of {len(rows)} titles")
    counts = recount([run_id])[run_id]
    status = "partial" if notes else "ok"
    _finish(run_id, status, notes=notes)
    open_count = counts["uncertain"] + counts["failed"]
    message = f"{label}: {counts['matched']} of {len(rows)} matched"
    if open_count:
        message += f", {open_count} to check"
    return {"run": run_id, "outcome": status, "message": message}


def _finish(run_id: int, status: str, error: str | None = None, notes: list[str] | None = None) -> None:
    db.get().update("parser_runs", "id", run_id, {
        "status": status, "error": error, "notes": db.dumps(notes) if notes else None, "finished_at": db.now(),
    })


def recount(run_ids: list[int]) -> dict[int, dict]:
    database = db.get()
    result = {}
    for run_id in run_ids:
        counts = {status: 0 for status in STATUSES}
        for row in database.query(
            "SELECT status, COUNT(*) AS n FROM parser_entries WHERE run_id=? GROUP BY status", (run_id,)
        ):
            counts[row["status"]] = row["n"]
        database.update("parser_runs", "id", run_id, {**counts, "raw_count": sum(counts.values())})
        result[run_id] = counts
    return result


def recover() -> None:
    """Runs cut off by a restart."""
    db.get().execute(
        "UPDATE parser_runs SET status='error', error='Stopped by a restart', finished_at=? WHERE status='running'",
        (db.now(),),
    )


def prune() -> int:
    """Drop runs older than the setting; the last run of every list stays."""
    days = max(1, int(settings.get("runs_keep_days") or 60))
    # Runs read by the last run of a flow stay with it.
    cursor = db.get().execute(
        "DELETE FROM parser_runs WHERE started_at < ? "
        "AND id NOT IN (SELECT MAX(id) FROM parser_runs GROUP BY source_ref) "
        "AND id NOT IN (SELECT MAX(id) FROM parser_runs WHERE status IN ('ok','partial') GROUP BY source_ref) "
        "AND (flow_run_id IS NULL OR flow_run_id NOT IN (SELECT MAX(id) FROM flow_runs GROUP BY flow_id))",
        (db.now() - days * 86400,),
    )
    return cursor.rowcount or 0


# -- reading ---------------------------------------------------------------------------------------

def run_brief(row: dict) -> dict:
    return {
        "id": row["id"],
        "source": row["source"],
        "source_ref": row["source_ref"],
        "label": row["label"] or row["source_ref"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
        "error": row["error"],
        "total": row["raw_count"] or 0,
        "matched": row["matched"] or 0,
        "uncertain": row["uncertain"] or 0,
        "failed": row["failed"] or 0,
        "excluded": row["excluded"] or 0,
    }


def runs(source_ref: str | None = None, limit: int = 100, previews_only: bool = False) -> list[dict]:
    """Recent runs; previews_only leaves out the lists flows read."""
    sql = "SELECT * FROM parser_runs WHERE 1=1"
    params: list = []
    if source_ref:
        sql += " AND source_ref=?"
        params.append(source_ref)
    if previews_only:
        sql += " AND flow_run_id IS NULL"
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    return [run_brief(row) for row in db.get().query(sql, params)]


def candidate_view(row: dict) -> dict:
    return {
        "kind": row.get("kind"),
        "tmdb_id": row.get("tmdb_id"),
        "title": row.get("title") or "",
        "original_title": row.get("original_title") or None,
        "year": row.get("year"),
        "popularity": row.get("popularity") or 0,
        "similarity": row.get("similarity"),
        "poster": tmdb.poster_url(row.get("poster_path")),
    }


def entry_views(rows: list[dict]) -> list[dict]:
    media_ids = sorted({row["media_id"] for row in rows if row.get("media_id")})
    media = {}
    if media_ids:
        marks = ",".join("?" for _ in media_ids)
        media = {row["id"]: row for row in db.get().query(f"SELECT * FROM media_cache WHERE id IN ({marks})", media_ids)}
    plex = store.in_plex(media_ids)
    result = []
    for row in rows:
        media_row = media.get(row.get("media_id"))
        view = {
            "id": row["id"],
            "run_id": row["run_id"],
            "position": row["position"],
            "title": row["raw_title"] or "",
            "year": row["raw_year"],
            "kind": row["raw_kind"],
            "ids": db.loads(row["raw_ids"], {}) or {},
            "url": row["raw_url"],
            "extra": db.loads(row["extra"], {}) or {},
            "fingerprint": row["fingerprint"],
            "status": row["status"],
            "method": row["method"],
            "score": row["score"],
            "error": row["error"],
            "media": store.view(media_row) if media_row else None,
            "plex_key": plex.get(row.get("media_id")),
            "candidates": [candidate_view(c) for c in db.loads(row["candidates"], []) or []],
        }
        if "source" in row:
            view["source"] = row["source"]
            view["label"] = row.get("label")
        result.append(view)
    return result


def run_detail(run_id: int) -> dict | None:
    database = db.get()
    row = database.one("SELECT * FROM parser_runs WHERE id=?", (run_id,))
    if not row:
        return None
    entries = database.query("SELECT * FROM parser_entries WHERE run_id=? ORDER BY position, id", (run_id,))
    previous = database.scalar(
        "SELECT id FROM parser_runs WHERE source_ref=? AND id<? ORDER BY id DESC LIMIT 1", (row["source_ref"], run_id)
    )
    return {
        **run_brief(row),
        "params": db.loads(row["params"], {}) or {},
        "notes": db.loads(row["notes"], []) or [],
        "previous": previous,
        "entries": entry_views(entries),
    }


def _latest_runs() -> list[int]:
    return [row["id"] for row in db.get().query(
        "SELECT MAX(id) AS id FROM parser_runs WHERE status IN ('ok','partial') GROUP BY source_ref"
    )]


def _latest_entries(where: str = "", params: tuple = ()) -> list[dict]:
    ids = _latest_runs()
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    return db.get().query(
        f"SELECT e.*, r.source, r.label FROM parser_entries e JOIN parser_runs r ON r.id=e.run_id "
        f"WHERE e.run_id IN ({marks}) {where} ORDER BY e.run_id DESC, e.position",
        [*ids, *params],
    )


def _grouped(rows: list[dict]) -> list[dict]:
    """One row per raw title and source, with the lists it appears in."""
    groups: dict[tuple, dict] = {}
    for row in rows:
        key = (row["source"], row["fingerprint"])
        group = groups.get(key)
        if group is None:
            group = groups[key] = {**row, "_lists": []}
        group["_lists"].append({"run_id": row["run_id"], "label": row["label"] or "", "position": row["position"]})
    views = entry_views(list(groups.values()))
    for view, group in zip(views, groups.values()):
        view["lists"] = group["_lists"]
    return views


def problems() -> list[dict]:
    return _grouped(_latest_entries("AND e.status IN ('uncertain','failed')"))


def problem_count() -> int:
    return len({(row["source"], row["fingerprint"]) for row in _latest_entries("AND e.status IN ('uncertain','failed')")})


def matches(status: str = "", query: str = "", page: int = 1) -> dict:
    where, params = "", []
    if status in STATUSES:
        where += " AND e.status=?"
        params.append(status)
    elif status == "open":
        where += " AND e.status IN ('uncertain','failed')"
    if query.strip():
        where += " AND (e.raw_title LIKE ? OR e.media_id IN (SELECT id FROM media_cache WHERE title LIKE ?))"
        like = f"%{query.strip()}%"
        params += [like, like]
    rows = _grouped(_latest_entries(where, tuple(params)))
    page = max(1, page)
    return {"rows": rows[(page - 1) * PAGE: page * PAGE], "total": len(rows), "page": page, "per_page": PAGE}


# -- corrections -----------------------------------------------------------------------------------

def _entry(entry_id: int) -> dict:
    row = db.get().one(
        "SELECT e.*, r.source FROM parser_entries e JOIN parser_runs r ON r.id=e.run_id WHERE e.id=?", (entry_id,)
    )
    if not row:
        raise SourceError("Entry not found")
    return row


def _apply(source: str, fp: str, values: dict) -> int:
    """Set the outcome on every stored entry of this raw title; returns how many changed."""
    database = db.get()
    run_ids = [row["run_id"] for row in database.query(
        "SELECT DISTINCT e.run_id FROM parser_entries e JOIN parser_runs r ON r.id=e.run_id "
        "WHERE e.fingerprint=? AND r.source=?", (fp, source),
    )]
    if not run_ids:
        return 0
    sets = ",".join(f"{column}=?" for column in values)
    cursor = database.execute(
        f"UPDATE parser_entries SET {sets} WHERE fingerprint=? "
        "AND run_id IN (SELECT id FROM parser_runs WHERE source=?)",
        [*values.values(), fp, source],
    )
    recount(run_ids)
    return cursor.rowcount or 0


async def fix(entry_id: int, kind: str, tmdb_id: int) -> dict:
    """This raw title is that TMDB title, now and in future runs."""
    entry = _entry(entry_id)
    if kind not in ("movie", "show"):
        raise SourceError("Choose a movie or a show")
    media_id = await store.ensure(kind, tmdb_id, max_age_days=0)
    if not media_id:
        raise SourceError("TMDB has no title with this ID")
    db.get().upsert("parser_overrides", ("source", "fingerprint"), {
        "source": entry["source"], "fingerprint": entry["fingerprint"], "media_id": media_id, "ignore": 0,
        "created_at": db.now(),
    })
    status = "excluded" if normalize.excluded(media_id) else "matched"
    changed = _apply(entry["source"], entry["fingerprint"], {
        "status": status, "method": "fixed", "media_id": media_id, "score": 1.0, "error": None,
    })
    try:
        await mdblist.refresh_ratings([media_id])
    except mdblist.MdblistError:
        pass
    row = store.get(media_id) or {}
    event("info", "lists", f"Match fixed: {entry['raw_title']} → {row.get('title')} ({row.get('year') or '?'})")
    return {"media_id": media_id, "changed": changed}


def ignore(entry_id: int) -> int:
    """Leave this raw title out, now and in future runs."""
    entry = _entry(entry_id)
    db.get().upsert("parser_overrides", ("source", "fingerprint"), {
        "source": entry["source"], "fingerprint": entry["fingerprint"], "media_id": None, "ignore": 1,
        "created_at": db.now(),
    })
    event("info", "lists", f"Ignored in lists: {entry['raw_title']}")
    return _apply(entry["source"], entry["fingerprint"], {
        "status": "excluded", "method": "ignored", "media_id": None, "score": 1.0, "error": None,
    })


def overrides() -> list[dict]:
    rows = db.get().query(
        "SELECT o.*, (SELECT e.id FROM parser_entries e JOIN parser_runs r ON r.id=e.run_id "
        "  WHERE e.fingerprint=o.fingerprint AND (o.source='' OR r.source=o.source) ORDER BY e.id DESC LIMIT 1) "
        "  AS entry_id FROM parser_overrides o ORDER BY o.created_at DESC, o.id DESC"
    )
    entry_ids = [row["entry_id"] for row in rows if row["entry_id"]]
    entries = {}
    if entry_ids:
        marks = ",".join("?" for _ in entry_ids)
        entries = {row["id"]: row for row in db.get().query(f"SELECT * FROM parser_entries WHERE id IN ({marks})", entry_ids)}
    media_ids = [row["media_id"] for row in rows if row["media_id"]]
    media = {}
    if media_ids:
        marks = ",".join("?" for _ in media_ids)
        media = {row["id"]: row for row in db.get().query(f"SELECT * FROM media_cache WHERE id IN ({marks})", media_ids)}
    result = []
    for row in rows:
        entry = entries.get(row["entry_id"]) or {}
        result.append({
            "id": row["id"],
            "source": row["source"] or "",
            "fingerprint": row["fingerprint"],
            "title": entry.get("raw_title") or row["fingerprint"].split("|")[1],
            "year": entry.get("raw_year"),
            "kind": entry.get("raw_kind"),
            "ignore": bool(row["ignore"]),
            "media": store.view(media[row["media_id"]]) if row["media_id"] in media else None,
            "created_at": row["created_at"],
        })
    return result


async def reset(entry_id: int) -> int:
    """Remove the correction behind this entry (fix or ignore)."""
    entry = _entry(entry_id)
    rule = normalize.override_for(entry["source"], entry["fingerprint"])
    if not rule:
        raise SourceError("No correction for this entry")
    return await remove_override(rule["id"])


async def remove_override(override_id: int) -> int:
    """Back to automatic matching; stored entries of this raw title are matched again."""
    database = db.get()
    row = database.one("SELECT * FROM parser_overrides WHERE id=?", (override_id,))
    if not row:
        raise SourceError("Correction not found")
    database.execute("DELETE FROM parser_overrides WHERE id=?", (override_id,))
    sources = [row["source"]] if row["source"] else [
        r["source"] for r in database.query("SELECT DISTINCT source FROM parser_runs")
    ]
    changed = 0
    for source in sources:
        latest = database.one(
            "SELECT e.* FROM parser_entries e JOIN parser_runs r ON r.id=e.run_id "
            "WHERE e.fingerprint=? AND r.source=? ORDER BY e.id DESC LIMIT 1", (row["fingerprint"], source),
        )
        if not latest:
            continue
        entry = RawEntry(position=latest["position"] or 0, title=latest["raw_title"] or "", year=latest["raw_year"],
                         kind=latest["raw_kind"], ids=db.loads(latest["raw_ids"], {}) or {})
        _, match = await normalize.resolve(source, entry)
        changed += _apply(source, row["fingerprint"], {
            "status": match.status, "method": match.method, "media_id": match.media_id, "score": match.score,
            "candidates": db.dumps(match.candidates), "error": match.error,
        })
    return changed
