"""Collections in Plex as the flows want them, and the queue.

A run of a flow hands its collection outputs the titles that reached them. Titles in the library go in
(mirror also takes out what dropped out of the result), titles Radarr or Sonarr has but Plex not yet wait
in the queue, everything else is listed as missing. After each sync with Plex, queued titles that arrived
go in. A dry run reads Plex but writes nothing.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from .. import arr, db, jobs, notify
from ..arr import library as arr_library
from ..flows.nodes import Title, Verdict
from ..media import store as media_store
from ..plex import COLLECTION_SORT, TYPE_MOVIE, TYPE_SHOW, Plex, PlexError
from ..posters import sources
from ..posters.sources.base import ImageRef
from ..util import event
from . import posters, store

log = logging.getLogger("programarr.collections")

PORTS = ("added", "present", "queued", "missing", "removed", "skipped", "failed")
CREATE_BATCH = 100  # titles in the request that creates a collection; the rest are added after
_locks: dict[int, asyncio.Lock] = {}


def _lock(collection_id: int) -> asyncio.Lock:
    """One change at a time per collection: a flow run, the queue after a sync, a poster, visibility."""
    return _locks.setdefault(collection_id, asyncio.Lock())


@dataclass
class Outcome:
    verdicts: dict[int, Verdict] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=lambda: {port: 0 for port in PORTS})
    notes: list[str] = field(default_factory=list)
    title: str = ""

    def put(self, media_id: int, port: str, detail: str = "") -> None:
        """A title's outcome; a later one replaces an earlier one (a skipped title leaves the collection)."""
        if media_id in self.verdicts:
            self.counts[self.verdicts[media_id].port] -= 1
        self.verdicts[media_id] = Verdict(port, detail)
        self.counts[port] += 1


def _ordered(titles: list[Title]) -> list[Title]:
    """Best list position first; titles without one (a Plex library) by name."""
    return sorted(titles, key=lambda title: (title.rank if title.rank is not None else 10**6,
                                             (title.media.get("title") or "").lower()))


async def requested(titles: list[Title], also: dict[int, str] | None = None) -> tuple[dict[int, str], list[str]]:
    """Which of these titles Radarr (movies) or Sonarr (shows) has: media id -> service."""
    found = dict(also or {})
    notes: list[str] = []
    for service, kind, column in (("radarr", "movie", "tmdb_id"), ("sonarr", "show", "tvdb_id")):
        wanted = [title for title in titles if title.media.get("kind") == kind and title.media_id not in found]
        if not wanted or not arr.configured(service):
            continue
        try:
            index = await arr_library.index(service)
        except arr.ArrError as exc:
            notes.append(str(exc))
            continue
        for title in wanted:
            external = title.media.get(column)
            try:
                if external and int(external) in index:
                    found[title.media_id] = service
            except (TypeError, ValueError):
                continue
    return found, notes


async def _members(plex: Plex, collection: dict) -> list[str] | None:
    """Item keys in the Plex collection in its order; None if there is none (any more)."""
    if not collection.get("plex_key"):
        return None
    if collection.get("plex_library") and collection["plex_library"] != collection["library_key"]:
        return None  # it moves to another library
    if await plex.collection(collection["plex_key"]) is None:
        return None
    return [str(item.get("ratingKey")) for item in await plex.collection_items(collection["plex_key"])]


async def run_output(collection_id: int, titles: list[Title], arr_titles: dict[int, str], list_name: str | None,
                     dry_run: bool) -> Outcome:
    """One collection output in a flow run: the titles' states, and in a live run Plex brought in line."""
    async with _lock(collection_id):
        return await _run_output(collection_id, titles, arr_titles, list_name, dry_run)


async def _run_output(collection_id: int, titles: list[Title], arr_titles: dict[int, str], list_name: str | None,
                      dry_run: bool) -> Outcome:
    outcome = Outcome()
    collection = store.get(collection_id)
    lib = store.library(collection["library_key"]) if collection else None
    if collection is None or lib is None:
        for title in titles:
            outcome.put(title.media_id, "failed", "Library not found, check Settings › Plex")
        outcome.notes.append("Collection: library not found")
        return outcome
    kind = "movie" if lib["type"] == "movie" else "show"
    outcome.title = store.resolve(collection["name_template"], list_name)
    target: list[Title] = []
    for title in _ordered(titles):
        if title.media.get("kind") != kind:
            outcome.put(title.media_id, "skipped", "Not a movie" if kind == "movie" else "Not a show")
        else:
            target.append(title)
    limit = int(collection["max_items"] or 0)
    if limit and len(target) > limit:
        for title in target[limit:]:
            outcome.put(title.media_id, "skipped", f"Over the limit of {limit}")
        target = target[:limit]
    in_library = media_store.in_plex([title.media_id for title in target], lib["key"])
    asked, notes = await requested([title for title in target if title.media_id not in in_library], arr_titles)
    outcome.notes.extend(notes)
    plex = Plex.from_settings()
    current = {row["media_id"]: row for row in store.items(collection_id)}
    try:
        members = await _members(plex, collection)
    except PlexError as exc:
        outcome.notes.append(f"{outcome.title}: {exc}")
        members = [row["item_key"] for row in current.values() if row["state"] == "in_plex" and row["item_key"]]
    in_collection = set(members or [])
    now = db.now()
    days = store.queue_days()
    rows: dict[int, dict] = {}
    for position, title in enumerate(target, 1):
        old = current.get(title.media_id) or {}
        row = {"position": position, "first_at": old.get("first_at") or now, "item_key": None, "service": None,
               "queued_at": None, "expires_at": None}
        if title.media_id in in_library:
            row.update(state="in_plex", item_key=in_library[title.media_id])
            if row["item_key"] in in_collection:
                outcome.put(title.media_id, "present", "In the collection")
            else:
                outcome.put(title.media_id, "added", "")
        elif title.media_id in asked:
            service = asked[title.media_id]
            queued_at = old.get("queued_at") if old.get("state") == "queued" and old.get("queued_at") else now
            expires = queued_at + days * 86400 if days else None
            if expires is not None and expires < now:
                row["state"] = "missing"
                outcome.put(title.media_id, "missing", f"Queued for {days} days, not in Plex")
            else:
                row.update(state="queued", service=service, queued_at=queued_at, expires_at=expires)
                outcome.put(title.media_id, "queued", f"Added to {arr.label(service)} in this run"
                            if title.media_id in arr_titles else f"In {arr.label(service)}, not in Plex yet")
        else:
            row["state"] = "missing"
            outcome.put(title.media_id, "missing", "Not in Plex")
        rows[title.media_id] = row
    mirror = collection["sync_mode"] == "mirror"
    next_position = len(target) + 1
    for media_id, old in sorted(current.items(), key=lambda pair: (pair[1]["position"] or 0, pair[0])):
        if media_id in rows:
            continue
        keep = not mirror and (old["state"] == "in_plex"
                               or old["state"] == "queued" and (not old["expires_at"] or old["expires_at"] >= now))
        if keep:
            rows[media_id] = {**{key: old[key] for key in ("first_at", "item_key", "service", "queued_at",
                                                            "expires_at", "state")}, "position": next_position}
            next_position += 1
        elif old["state"] == "in_plex":
            skipped = outcome.verdicts.get(media_id)
            outcome.put(media_id, "removed", skipped.detail if skipped else "No longer in the result")
    if dry_run:
        if not collection["plex_key"]:
            store.update(collection_id, {"title": outcome.title})  # the name to show until it is in Plex
        return outcome
    database = db.get()
    with database.transaction() as conn:
        conn.execute("DELETE FROM collection_items WHERE collection_id=?", (collection_id,))
        conn.executemany(
            "INSERT INTO collection_items (collection_id, media_id, position, state, item_key, service, queued_at, "
            "expires_at, first_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [(collection_id, media_id, row["position"], row["state"], row["item_key"], row["service"],
              row["queued_at"], row["expires_at"], row["first_at"]) for media_id, row in rows.items()])
        conn.execute("UPDATE collections SET title=? WHERE id=?", (outcome.title, collection_id))
    try:
        await _push(collection_id)
    except PlexError as exc:
        store.update(collection_id, {"error": str(exc)})
        outcome.notes.append(f"{outcome.title}: {exc}")
        for media_id, verdict in list(outcome.verdicts.items()):
            if verdict.port in ("added", "removed"):
                outcome.put(media_id, "failed", str(exc))
    return outcome


# -- Plex ----------------------------------------------------------------------------------------------

def _stable(order: list[str], actual: list[str]) -> set[str]:
    """The longest run of keys already in the wanted order: those stay, the others move."""
    where = {key: index for index, key in enumerate(actual)}
    keys = [key for key in order if key in where]
    tails: list[int] = []  # index into keys of the smallest tail for each length
    previous: list[int] = [-1] * len(keys)
    for index, key in enumerate(keys):
        value = where[key]
        low, high = 0, len(tails)
        while low < high:
            middle = (low + high) // 2
            if where[keys[tails[middle]]] < value:
                low = middle + 1
            else:
                high = middle
        if low > 0:
            previous[index] = tails[low - 1]
        if low == len(tails):
            tails.append(index)
        else:
            tails[low] = index
    stable: set[str] = set()
    index = tails[-1] if tails else -1
    while index >= 0:
        stable.add(keys[index])
        index = previous[index]
    return stable


async def _reorder(plex: Plex, rating_key: str, order: list[str]) -> int:
    actual = [str(item.get("ratingKey")) for item in await plex.collection_items(rating_key)]
    wanted = order + [key for key in actual if key not in order]  # titles added in Plex by hand stay at the end
    if actual == wanted:
        return 0
    stable = _stable(wanted, actual)
    moves = 0
    for index, key in enumerate(wanted):
        if key in stable or key not in actual:
            continue
        await plex.move_in_collection(rating_key, key, wanted[index - 1] if index else None)
        moves += 1
    return moves


async def push(collection_id: int) -> dict:
    """Bring the Plex collection in line with the stored titles: create, add, take out, name, order, show."""
    async with _lock(collection_id):
        return await _push(collection_id)


async def _push(collection_id: int) -> dict:
    collection = store.get(collection_id)
    lib = store.library(collection["library_key"]) if collection else None
    if collection is None or lib is None:
        return {"ok": False}
    plex = Plex.from_settings()
    rows = store.items(collection_id)
    wanted = [row["item_key"] for row in rows if row["state"] == "in_plex" and row["item_key"]]
    title = collection["title"] or store.resolve(collection["name_template"], None)
    plex_key = collection["plex_key"]
    if plex_key and collection["plex_library"] and collection["plex_library"] != lib["key"]:
        try:
            await plex.delete_collection(plex_key)  # the library changed: it starts over in the new one
        except PlexError as exc:
            if exc.status != 404:
                raise
        plex_key = None
    meta = await plex.collection(plex_key) if plex_key else None
    if plex_key and meta is None:
        plex_key = None  # deleted in Plex
    mirror = collection["sync_mode"] == "mirror"
    values: dict = {"synced_at": db.now(), "error": None}
    if not wanted:
        if plex_key and mirror:
            await plex.delete_collection(plex_key)  # Plex keeps no empty collections
            plex_key = None
        values.update(plex_key=plex_key, plex_library=lib["key"] if plex_key else None)
        store.update(collection_id, values)
        return {"ok": True, "items": 0}
    created = False
    if not plex_key:
        plex_key = await plex.create_collection(lib["key"], TYPE_MOVIE if lib["type"] == "movie" else TYPE_SHOW,
                                                title, wanted[:CREATE_BATCH])
        if len(wanted) > CREATE_BATCH:
            await plex.add_to_collection(plex_key, wanted[CREATE_BATCH:])
        created = True
        values.update(sort_applied=None, visibility_applied=None, poster_applied=None)
        collection.update(sort_applied=None, visibility_applied=None, poster_applied=None)
        event("info", "collections", f"Collection created in Plex: {title}")
    else:
        members = [str(item.get("ratingKey")) for item in await plex.collection_items(plex_key)]
        missing = [key for key in wanted if key not in members]
        if missing:
            await plex.add_to_collection(plex_key, missing)
        if mirror:
            for key in members:
                if key not in wanted:
                    await plex.remove_from_collection(plex_key, key)
        if (meta or {}).get("title") != title:
            await plex.edit_title(lib["key"], plex_key, title)
    sort = "custom" if collection["item_order"] == "list" else collection["item_order"]
    if collection.get("sort_applied") != sort:
        await plex.collection_settings(plex_key, collectionSort=COLLECTION_SORT[sort])
        values["sort_applied"] = sort
    if sort == "custom":
        await _reorder(plex, plex_key, wanted)
    flags = f"{collection['show_recommended']},{collection['show_home']},{collection['show_shared']}"
    if collection.get("visibility_applied") != flags:
        await plex.set_visibility(lib["key"], plex_key, bool(collection["show_recommended"]),
                                  bool(collection["show_home"]), bool(collection["show_shared"]))
        values["visibility_applied"] = flags
    if collection["poster"] and collection.get("poster_applied") != plex_key:
        data = posters.read(collection)
        if data:
            await plex.upload_poster(plex_key, data, posters.content_type(collection["poster"]))
            values["poster_applied"] = plex_key
    values.update(plex_key=plex_key, plex_library=lib["key"])
    store.update(collection_id, values)
    return {"ok": True, "items": len(wanted), "created": created}


async def apply_visibility(collection_id: int) -> None:
    """New visibility flags right away, if the collection is in Plex already."""
    async with _lock(collection_id):
        collection = store.get(collection_id)
        if not collection or not collection["plex_key"]:
            return
        await Plex.from_settings().set_visibility(collection["plex_library"] or collection["library_key"],
                                                  collection["plex_key"], bool(collection["show_recommended"]),
                                                  bool(collection["show_home"]), bool(collection["show_shared"]))
        flags = f"{collection['show_recommended']},{collection['show_home']},{collection['show_shared']}"
        store.update(collection_id, {"visibility_applied": flags})


async def set_poster(collection_id: int, ref: dict) -> dict:
    """A poster from the picker (ThePosterDB, an upload or a link): kept here and uploaded to Plex."""
    image = ImageRef.from_dict(ref)
    data, _ = await sources.fetch(image)
    async with _lock(collection_id):
        collection = store.get(collection_id)
        if collection is None:
            return {"ok": False, "message": "Collection not found"}
        name = await asyncio.to_thread(posters.save, collection_id, data)
        store.update(collection_id, {"poster": name, "poster_source": image.source, "poster_applied": None})
        title = collection["title"] or collection["name_template"]
        if not collection["plex_key"]:
            return {"ok": True, "message": "Poster saved, Plex gets it with the collection"}
        await Plex.from_settings().upload_poster(collection["plex_key"], posters.read({**collection, "poster": name}) or b"",
                                                 posters.content_type(name))
        store.update(collection_id, {"poster_applied": collection["plex_key"]})
    event("info", "collections", f"Poster set: {title}")
    return {"ok": True, "message": "Poster set"}


# -- queue ---------------------------------------------------------------------------------------------

def _live(collection: dict) -> bool:
    """Only collections of live flows change Plex between runs."""
    if not collection["in_flow"] or not collection["flow_id"]:
        return False
    return db.get().scalar("SELECT dry_run FROM flows WHERE id=?", (collection["flow_id"],), 1) == 0


def _arrivals(collection_id: int, library_key: str, mirror: bool, now: int) -> tuple[list[dict], int]:
    """Queued or missing titles of one collection that Plex has now; queued ones past their time leave."""
    database = db.get()
    rows = database.query(
        "SELECT ci.*, m.title AS media_title, m.year AS media_year FROM collection_items ci "
        "JOIN media_cache m ON m.id=ci.media_id WHERE ci.collection_id=? AND ci.state IN ('queued','missing') "
        "ORDER BY ci.position", (collection_id,))
    found = media_store.in_plex([row["media_id"] for row in rows], library_key)
    arrived, expired = [], 0
    for row in rows:
        if row["media_id"] in found:
            database.execute(
                "UPDATE collection_items SET state='in_plex', item_key=?, service=NULL, queued_at=NULL, "
                "expires_at=NULL WHERE collection_id=? AND media_id=?",
                (found[row["media_id"]], collection_id, row["media_id"]))
            arrived.append(row)
        elif row["state"] == "queued" and row["expires_at"] and row["expires_at"] < now:
            if mirror:
                database.execute("UPDATE collection_items SET state='missing', service=NULL WHERE collection_id=? "
                                 "AND media_id=?", (collection_id, row["media_id"]))
            else:
                database.execute("DELETE FROM collection_items WHERE collection_id=? AND media_id=?",
                                 (collection_id, row["media_id"]))
            expired += 1
    return arrived, expired


async def process_queue() -> dict:
    """After a sync: titles that reached Plex go into their collection (queued ones, and missing ones that
    came another way); queued titles past their time leave the queue. Titles Plex no longer has are missing."""
    database = db.get()
    now = db.now()
    result = {"arrived": 0, "expired": 0}
    gone = database.query(
        "SELECT ci.collection_id, ci.media_id FROM collection_items ci LEFT JOIN items i ON i.key=ci.item_key "
        "WHERE ci.state='in_plex' AND (i.key IS NULL OR i.gone=1)")
    for row in gone:
        database.execute("UPDATE collection_items SET state='missing', item_key=NULL WHERE collection_id=? AND media_id=?",
                         (row["collection_id"], row["media_id"]))
    waiting = [row["collection_id"] for row in database.query(
        "SELECT DISTINCT collection_id FROM collection_items WHERE state IN ('queued','missing') ORDER BY collection_id")]
    for collection_id in waiting:
        async with _lock(collection_id):
            collection = store.get(collection_id)
            if collection is None:
                continue
            arrived, expired = _arrivals(collection_id, collection["library_key"], collection["sync_mode"] == "mirror", now)
            result["expired"] += expired
            if not arrived or not _live(collection):
                continue
            title = collection["title"] or store.resolve(collection["name_template"], None)
            try:
                await _push(collection_id)
            except PlexError as exc:
                store.update(collection_id, {"error": str(exc)})
                event("error", "collections", f"{title}: {exc}")
                continue
        result["arrived"] += len(arrived)
        names = [f"{row['media_title']} ({row['media_year']})" if row["media_year"] else row["media_title"]
                 for row in arrived]
        event("info", "collections", f"{title}: {', '.join(names)} added")
        await notify.send("collection", f"Added to {title}", ", ".join(names), path=f"/collections/{collection_id}")
    return result


def unqueue(collection_id: int, media_id: int) -> bool:
    """Take a title out of the queue: in a mirror it stays listed as missing."""
    database = db.get()
    row = database.one("SELECT ci.state, c.sync_mode FROM collection_items ci JOIN collections c ON c.id=ci.collection_id "
                       "WHERE ci.collection_id=? AND ci.media_id=?", (collection_id, media_id))
    if not row or row["state"] != "queued":
        return False
    if row["sync_mode"] == "mirror":
        database.execute("UPDATE collection_items SET state='missing', service=NULL, queued_at=NULL, expires_at=NULL "
                         "WHERE collection_id=? AND media_id=?", (collection_id, media_id))
    else:
        database.execute("DELETE FROM collection_items WHERE collection_id=? AND media_id=?", (collection_id, media_id))
    return True


# -- other collections ---------------------------------------------------------------------------------

def _flag(value) -> bool:
    return value in (True, 1, "1", "true")


async def others() -> list[dict]:
    """Collections in the selected libraries that Programarr did not make (Kometa, made by hand)."""
    from ..posters import library as poster_library

    plex = Plex.from_settings()
    managed = store.managed_keys()
    result = []
    for lib in poster_library.libraries(enabled_only=True):
        for meta in await plex.collections(lib["key"]):
            key = str(meta.get("ratingKey") or "")
            if not key or key in managed:
                continue
            result.append({
                "key": key,
                "title": meta.get("title") or "",
                "library_key": lib["key"],
                "library": lib["title"],
                "count": int(meta.get("childCount") or 0),
                "smart": _flag(meta.get("smart")),
                "thumb": meta.get("thumb") or meta.get("composite") or "",
                "updated_at": meta.get("updatedAt"),
                "labels": [label.get("tag") for label in meta.get("Label", []) or [] if label.get("tag")],
            })
    return result


async def delete_others(keys: list[str]) -> dict:
    """Delete collections Programarr did not make; its own ones are refused."""
    plex = Plex.from_settings()
    managed = store.managed_keys()
    deleted, failed = 0, []
    wanted = [key for key in dict.fromkeys(str(key) for key in keys) if key and key not in managed]
    for index, key in enumerate(wanted):
        jobs.progress(index, len(wanted), "Deleting collections…")
        try:
            await plex.delete_collection(key)
            deleted += 1
        except PlexError as exc:
            if exc.status == 404:
                continue
            failed.append(str(exc))
    if deleted:
        event("info", "collections", f"{deleted} collections deleted in Plex")
    return {"deleted": deleted, "failed": len(failed), "message": failed[0] if failed else None}


async def delete(collection_id: int, keep_in_plex: bool = False) -> bool:
    """Remove a collection that is no longer in a flow: from Plex too, unless it should stay there."""
    async with _lock(collection_id):
        collection = store.get(collection_id)
        if collection is None:
            return False
        if collection["plex_key"] and not keep_in_plex:
            try:
                await Plex.from_settings().delete_collection(collection["plex_key"])
            except PlexError as exc:
                if exc.status != 404:
                    raise
        posters.remove(collection)
        db.get().execute("DELETE FROM collections WHERE id=?", (collection_id,))
    _locks.pop(collection_id, None)
    return True
