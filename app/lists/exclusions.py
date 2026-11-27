"""Exclusions: titles that never count in a list and never reach Radarr or Sonarr.

Programarr's own exclusions and the import list exclusions of Radarr (movies, TMDB ids) and Sonarr
(shows, TVDB ids) are kept in step both ways. `arr_exclusions` mirrors those lists: "synced" rows are
there as of the last sync, "add" and "remove" rows wait to be written there. A title counts as excluded
when it has an own exclusion or a mirror row that is not waiting for removal.
"""
from __future__ import annotations

import asyncio
import logging

from .. import arr, cache, db
from ..arr import library
from ..media import normalize, store
from ..util import event
from .base import SourceError
from .runs import recount

log = logging.getLogger("programarr.lists")

SYNC_KEY = "exclusions:sync"
SERVICES = ("radarr", "sonarr")
_lock = asyncio.Lock()  # one change to the mirror at a time: syncs from flows, maintenance and the page


def _external(media: dict) -> tuple[str, int] | None:
    """Where a title goes in Radarr or Sonarr: (service, TMDB or TVDB id), if it has that id."""
    service = "radarr" if media.get("kind") == "movie" else "sonarr"
    value = media.get("tmdb_id") if service == "radarr" else media.get("tvdb_id")
    return (service, int(value)) if value else None


def _mirror(service: str, external_id: int) -> dict | None:
    return db.get().one("SELECT * FROM arr_exclusions WHERE service=? AND external_id=?", (service, external_id))


def set_excluded(media_id: int, excluded: bool) -> None:
    """Stored entries of this title follow: matched <-> excluded (ignored entries stay)."""
    database = db.get()
    old, new = ("matched", "excluded") if excluded else ("excluded", "matched")
    run_ids = [row["run_id"] for row in database.query(
        "SELECT DISTINCT run_id FROM parser_entries WHERE media_id=? AND status=?", (media_id, old)
    )]
    database.execute(
        "UPDATE parser_entries SET status=? WHERE media_id=? AND status=? AND IFNULL(method,'')!='ignored'",
        (new, media_id, old),
    )
    recount(run_ids)


def _refresh(service: str, external_ids: set[int]) -> None:
    """Entries of titles whose state changed on Radarr's or Sonarr's side follow."""
    if not external_ids:
        return
    column, kind = ("tmdb_id", "movie") if service == "radarr" else ("tvdb_id", "show")
    marks = ",".join("?" for _ in external_ids)
    rows = db.get().query(f"SELECT id FROM media_cache WHERE kind=? AND {column} IN ({marks})",
                          [kind, *external_ids])
    for row in rows:
        set_excluded(row["id"], normalize.excluded(row["id"]))


# -- writing there --------------------------------------------------------------------------------

async def _write_add(service: str, external_id: int) -> bool:
    row = _mirror(service, external_id)
    if not row or row["state"] != "add" or not arr.configured(service):
        return False
    try:
        arr_id = await library.add_exclusion(service, external_id, row["title"] or "", row["year"])
    except arr.ArrError as exc:
        if exc.status != 400:  # 400: already on the list; the next sync finds its id
            log.warning("Exclusion to %s: %s", service, exc)
            return False
        arr_id = None
    db.get().update_where("arr_exclusions", {"service": service, "external_id": external_id},
                          {"state": "synced", "arr_id": arr_id})
    return True


async def _write_remove(service: str, external_id: int) -> bool:
    row = _mirror(service, external_id)
    if not row or row["state"] != "remove":
        return False
    arr_id = row["arr_id"]
    if arr.configured(service):
        try:
            if not arr_id:
                arr_id = (await library.exclusion_list(service)).get(external_id, {}).get("arr_id")
            if arr_id:
                await library.remove_exclusion(service, int(arr_id))
        except arr.ArrError as exc:
            if exc.status != 404:  # 404: gone there already
                log.warning("Exclusion off %s: %s", service, exc)
                return False
    db.get().execute("DELETE FROM arr_exclusions WHERE service=? AND external_id=?", (service, external_id))
    return True


# -- Programarr's side ----------------------------------------------------------------------------

async def exclude(kind: str | None, tmdb_id: int | None, media_id: int | None, reason: str | None) -> int:
    async with _lock:
        return await _exclude(kind, tmdb_id, media_id, reason)


async def _exclude(kind: str | None, tmdb_id: int | None, media_id: int | None, reason: str | None) -> int:
    if media_id is None:
        if kind not in ("movie", "show") or not tmdb_id:
            raise SourceError("Choose a title")
        media_id = await store.ensure(kind, tmdb_id)
    media = store.get(media_id) if media_id else None
    if not media:
        raise SourceError("Title not found")
    database = db.get()
    database.upsert("exclusions", ("media_id", "scope"), {
        "media_id": media_id, "scope": "global", "reason": (reason or "").strip()[:200] or None,
        "created_at": db.now(),
    })
    target = _external(media)
    if target and arr.configured(target[0]):
        service, external_id = target
        row = _mirror(service, external_id)
        if row is None:
            database.execute(
                "INSERT OR IGNORE INTO arr_exclusions (service, external_id, title, year, state, added_at) "
                "VALUES (?,?,?,?, 'add', ?)", (service, external_id, media["title"], media.get("year"), db.now()))
            await _write_add(service, external_id)
        elif row["state"] == "remove":  # its removal had not reached Radarr/Sonarr yet: it stays there
            database.update_where("arr_exclusions", {"service": service, "external_id": external_id},
                                  {"state": "synced" if row["arr_id"] else "add"})
            await _write_add(service, external_id)
    set_excluded(media_id, True)
    event("info", "lists", f"Excluded: {media['title']} ({media.get('year') or '?'})")
    return database.scalar("SELECT id FROM exclusions WHERE media_id=? AND scope='global'", (media_id,))


async def _release(service: str, external_id: int) -> None:
    """Off the list in Radarr or Sonarr too (pending until it gets there)."""
    row = _mirror(service, external_id)
    if row is None:
        return
    if row["state"] == "add":  # never got there
        db.get().execute("DELETE FROM arr_exclusions WHERE service=? AND external_id=?", (service, external_id))
        return
    db.get().update_where("arr_exclusions", {"service": service, "external_id": external_id}, {"state": "remove"})
    await _write_remove(service, external_id)


async def unexclude(exclusion_id: int) -> None:
    async with _lock:
        await _unexclude(exclusion_id)


async def _unexclude(exclusion_id: int) -> None:
    database = db.get()
    row = database.one("SELECT * FROM exclusions WHERE id=?", (exclusion_id,))
    if not row:
        raise SourceError("Exclusion not found")
    database.execute("DELETE FROM exclusions WHERE id=?", (exclusion_id,))
    media = store.get(row["media_id"]) or {}
    target = _external(media) if row["scope"] == "global" else None
    if target:
        await _release(*target)
    if not normalize.excluded(row["media_id"]):
        set_excluded(row["media_id"], False)


async def unexclude_media(media_id: int) -> None:
    """Included again: Programarr's own exclusion and the one in Radarr or Sonarr."""
    async with _lock:
        row = db.get().one("SELECT id FROM exclusions WHERE media_id=? AND scope='global'", (media_id,))
        if row:
            await _unexclude(row["id"])
            return
        target = _external(store.get(media_id) or {})
        if not target or not _mirror(*target):
            raise SourceError("Title is not excluded")
        await _unexclude_arr(*target)


async def unexclude_arr(service: str, external_id: int) -> None:
    """A title on Radarr's or Sonarr's list, with or without an own exclusion."""
    async with _lock:
        await _unexclude_arr(service, external_id)


async def _unexclude_arr(service: str, external_id: int) -> None:
    if not _mirror(service, external_id):
        raise SourceError("Exclusion not found")
    column, kind = ("tmdb_id", "movie") if service == "radarr" else ("tvdb_id", "show")
    own = db.get().one(
        f"SELECT x.id FROM exclusions x JOIN media_cache m ON m.id=x.media_id "
        f"WHERE x.scope='global' AND m.kind=? AND m.{column}=?", (kind, external_id))
    if own:
        await _unexclude(own["id"])
        return
    await _release(service, external_id)
    _refresh(service, {external_id})


# -- keeping in step ------------------------------------------------------------------------------

def _own(service: str) -> dict[int, dict]:
    """Programarr's own global exclusions that belong in this service: external id -> media row."""
    column, kind = ("tmdb_id", "movie") if service == "radarr" else ("tvdb_id", "show")
    rows = db.get().query(
        f"SELECT x.id AS exclusion_id, m.* FROM exclusions x JOIN media_cache m ON m.id=x.media_id "
        f"WHERE x.scope='global' AND m.kind=? AND m.{column} IS NOT NULL", (kind,))
    return {int(row[column]): row for row in rows}


async def _sync(service: str, write: bool = True) -> dict:
    remote = await library.exclusion_list(service)
    database = db.get()
    own = _own(service)
    mirror = {row["external_id"]: row for row in database.query(
        "SELECT * FROM arr_exclusions WHERE service=?", (service,))}
    changed: set[int] = set()
    # Own exclusions without a mirror row (from before the sync, or Radarr/Sonarr set up later) go there.
    for external_id, media in own.items():
        if external_id not in mirror:
            database.execute(
                "INSERT OR IGNORE INTO arr_exclusions (service, external_id, title, year, state, added_at) "
                "VALUES (?,?,?,?, 'add', ?)", (service, external_id, media["title"], media.get("year"), db.now()))
            mirror[external_id] = _mirror(service, external_id)
    added = removed = 0
    for external_id, row in list(mirror.items()):
        there = remote.get(external_id)
        if row["state"] == "synced":
            if there:
                database.update_where("arr_exclusions", {"service": service, "external_id": external_id},
                                      {"arr_id": there["arr_id"], "title": there["title"] or row["title"],
                                       "year": there["year"] or row["year"]})
            else:  # taken off the list there: off here too
                database.execute("DELETE FROM arr_exclusions WHERE service=? AND external_id=?",
                                 (service, external_id))
                if external_id in own:
                    database.execute("DELETE FROM exclusions WHERE id=?", (own[external_id]["exclusion_id"],))
                changed.add(external_id)
                removed += 1
        elif row["state"] == "add":
            if there:
                database.update_where("arr_exclusions", {"service": service, "external_id": external_id},
                                      {"state": "synced", "arr_id": there["arr_id"]})
            elif write and await _write_add(service, external_id):
                added += 1
        elif row["state"] == "remove":
            if there:
                database.update_where("arr_exclusions", {"service": service, "external_id": external_id},
                                      {"arr_id": there["arr_id"]})
            elif write:  # gone there already
                database.execute("DELETE FROM arr_exclusions WHERE service=? AND external_id=?",
                                 (service, external_id))
            if write:
                await _write_remove(service, external_id)
    for external_id, there in remote.items():
        if external_id not in mirror:  # put on the list there: excluded here too
            database.execute(
                "INSERT OR IGNORE INTO arr_exclusions (service, external_id, arr_id, title, year, state, added_at) "
                "VALUES (?,?,?,?,?, 'synced', ?)",
                (service, external_id, there["arr_id"], there["title"], there["year"], db.now()))
            changed.add(external_id)
    _refresh(service, changed)
    pending = database.scalar("SELECT COUNT(*) FROM arr_exclusions WHERE service=? AND state!='synced'",
                              (service,), 0)
    return {"count": len(remote), "new": len([e for e in changed if e in remote]), "removed": removed,
            "written": added, "pending": pending, "error": None}


async def sync(write: bool = True) -> dict[str, dict]:
    """Both ways with every service that is set up; a service that cannot be reached keeps its last state.

    write=False (dry runs) only reads: changes there come in, nothing goes there.
    """
    results: dict[str, dict] = {}
    async with _lock:
        for service in SERVICES:
            if not arr.configured(service):
                continue
            try:
                results[service] = await _sync(service, write)
            except arr.ArrError as exc:
                results[service] = {"error": str(exc)}
                log.warning("Exclusions with %s: %s", service, exc)
    cache.put(SYNC_KEY, {"at": db.now(), "results": results}, 365 * 86400)
    return results


def last_sync() -> dict | None:
    return cache.get(SYNC_KEY)


# -- the page -------------------------------------------------------------------------------------

def exclusions() -> list[dict]:
    """Own exclusions with their state in Radarr/Sonarr, then titles only on those lists."""
    database = db.get()
    rows = database.query(
        "SELECT x.id AS exclusion_id, x.scope, x.reason, x.created_at AS excluded_at, m.* FROM exclusions x "
        "JOIN media_cache m ON m.id=x.media_id ORDER BY x.created_at DESC, x.id DESC"
    )
    mirror = {(row["service"], row["external_id"]): row for row in database.query("SELECT * FROM arr_exclusions")}
    linked: set[tuple[str, int]] = set()
    result = []
    plex = store.in_plex([row["id"] for row in rows])
    for row in rows:
        target = _external(row) if row["scope"] == "global" else None
        there = mirror.get(target) if target else None
        if target:
            linked.add(target)
        result.append({
            "id": row["exclusion_id"], "scope": row["scope"], "reason": row["reason"],
            "created_at": row["excluded_at"], "media": store.view(row), "plex_key": plex.get(row["id"]),
            "title": row["title"], "year": row.get("year"), "kind": row["kind"],
            "service": there["service"] if there and there["state"] != "remove" else None,
            "external_id": target[1] if target else None,
            "pending": bool(there and there["state"] == "add"),
        })
    for key, there in sorted(mirror.items(), key=lambda item: ((item[1]["title"] or "").lower(), item[0])):
        if key in linked or there["state"] == "remove":
            continue
        service, external_id = key
        column, kind = ("tmdb_id", "movie") if service == "radarr" else ("tvdb_id", "show")
        media = database.one(f"SELECT * FROM media_cache WHERE kind=? AND {column}=?", (kind, external_id))
        result.append({
            "id": None, "scope": "global", "reason": None, "created_at": there["added_at"],
            "media": store.view(media) if media else None, "plex_key": None,
            "title": there["title"] or f"{'TMDB' if service == 'radarr' else 'TVDB'} {external_id}",
            "year": there["year"], "kind": kind, "service": service, "external_id": external_id,
            "pending": there["state"] == "add",
        })
    return result
