"""Mirror of the Plex libraries and of the poster files in the media folders."""
from __future__ import annotations

import hashlib
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import db, imaging, settings
from . import paths
from ..plex import TYPE_MOVIE, TYPE_SEASON, TYPE_SHOW, Plex, locked_fields, media_files, parse_guids, year_of
from .sources.base import slot_id
from ..util import event

GRACE = 6 * 3600  # items added shortly before the baseline still count as new
DETAILS_MAX_AGE = 7 * 86400


@dataclass
class SyncResult:
    new_items: list[str] = field(default_factory=list)
    new_seasons: list[tuple[str, int]] = field(default_factory=list)
    gone: int = 0
    items: int = 0
    seasons: int = 0
    first_sync: list[str] = field(default_factory=list)
    # Slots whose poster changed in Plex since the last sync (Plex gives the image a new address).
    thumb_changed: list[str] = field(default_factory=list)
    # From the file scan: files that went missing, and files that appeared or changed in the folder.
    vanished: list[str] = field(default_factory=list)
    appeared: list[str] = field(default_factory=list)


def mapper() -> paths.PathMapper:
    return paths.PathMapper(settings.get("path_maps"))


def naming() -> dict:
    detected = settings.get("naming_detected") or {}
    result: dict = {"explicit": set()}
    for kind, default in (("movie", "poster"), ("show", "poster"), ("season", "root")):
        value = settings.get(f"naming_{kind}")
        if value == "auto":
            result[kind] = detected.get(kind) or default
        else:
            result[kind] = value
            result["explicit"].add(kind)
    return result


# -- libraries ---------------------------------------------------------------
async def refresh_libraries(plex: Plex | None = None) -> list[dict]:
    plex = plex or Plex.from_settings()
    sections = await plex.sections()
    database = db.get()
    known = {row["key"]: row for row in database.query("SELECT * FROM libraries")}
    for section in sections:
        row = known.get(section["key"])
        database.upsert(
            "libraries",
            ("key",),
            {
                "key": section["key"],
                "title": section["title"],
                "type": section["type"],
                "enabled": row["enabled"] if row else 0,
                "locations": db.dumps(section["locations"]),
                "baseline_at": row["baseline_at"] if row else None,
                "synced_at": row["synced_at"] if row else None,
            },
        )
    present = {s["key"] for s in sections}
    for key in known:
        if key not in present:
            database.execute("UPDATE libraries SET enabled=0 WHERE key=?", (key,))
    return database.query("SELECT * FROM libraries ORDER BY type, title")


def libraries(enabled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM libraries" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY type DESC, title"
    rows = db.get().query(sql)
    for row in rows:
        row["locations"] = db.loads(row["locations"], [])
    return rows


# -- sync --------------------------------------------------------------------
def _ensure_slot(slot: str, item_key: str, kind: str, season: int | None) -> None:
    db.get().execute(
        "INSERT OR IGNORE INTO slots (id, item_key, kind, season) VALUES (?,?,?,?)", (slot, item_key, kind, season)
    )


async def sync(plex: Plex | None = None, full_scan: bool = False, also_scan: set[str] | None = None) -> SyncResult:
    """Titles from Plex. Folders are read only for titles that are new or that Plex changed, plus also_scan."""
    plex = plex or Plex.from_settings()
    database = db.get()
    result = SyncResult()
    now = db.now()
    changed_items: set[str] = set()
    for lib in libraries(enabled_only=True):
        baseline = lib["baseline_at"]
        first = baseline is None
        if first:
            result.first_sync.append(lib["title"])
        seen: set[str] = set()
        if lib["type"] == "movie":
            for meta in await plex.section_items(lib["key"], TYPE_MOVIE):
                key = str(meta["ratingKey"])
                seen.add(key)
                if _store_movie(meta, lib, now, baseline, first, result):
                    changed_items.add(key)
        else:
            shows = await plex.section_items(lib["key"], TYPE_SHOW)
            for meta in shows:
                key = str(meta["ratingKey"])
                seen.add(key)
                if await _store_show(plex, meta, lib, now, baseline, first, result):
                    changed_items.add(key)
            seasons = await plex.section_items(lib["key"], TYPE_SEASON)
            changed_items |= await _store_seasons(plex, seasons, seen, now, baseline, first, result)
        # Items Plex no longer lists
        rows = database.query("SELECT key FROM items WHERE library_key=? AND gone=0", (lib["key"],))
        for row in rows:
            if row["key"] not in seen:
                database.execute("UPDATE items SET gone=1 WHERE key=?", (row["key"],))
                result.gone += 1
        database.execute(
            "UPDATE libraries SET synced_at=?, baseline_at=COALESCE(baseline_at, ?) WHERE key=?",
            (now, now, lib["key"]),
        )
        result.items += len(seen)
    settings.set("last_sync", now)
    keys = None if full_scan else changed_items | (also_scan or set())
    notes: dict[str, list[str]] = {"vanished": [], "appeared": []}
    scan_files(keys, notes)
    result.vanished, result.appeared = notes["vanished"], notes["appeared"]
    detect_naming()
    return result


def _thumb_changed(old: dict | None, thumb: str | None, first: bool) -> bool:
    return bool(old and not first and old.get("thumb") and thumb and old["thumb"] != thumb and not old.get("gone"))


def _store_movie(meta: dict, lib: dict, now: int, baseline: int | None, first: bool, result: SyncResult) -> bool:
    database = db.get()
    key = str(meta["ratingKey"])
    old = database.one("SELECT * FROM items WHERE key=?", (key,))
    ids = parse_guids(meta)
    files = media_files(meta)
    folder = str(PurePosixPath(files[0].replace("\\", "/")).parent) if files else None
    added = int(meta.get("addedAt") or now)
    is_new = 0
    if old is None and not first and added >= (baseline or now) - GRACE:
        is_new = 1
        result.new_items.append(key)
    values = {
        "key": key,
        "library_key": lib["key"],
        "type": "movie",
        "title": meta.get("title", ""),
        "original_title": meta.get("originalTitle"),
        "year": year_of(meta),
        "sort_title": meta.get("titleSort") or meta.get("title", ""),
        "tmdb_id": ids["tmdb"],
        "tvdb_id": ids["tvdb"],
        "imdb_id": ids["imdb"],
        "folder": folder,
        "files": db.dumps(files),
        "added_at": added,
        "updated_at": int(meta.get("updatedAt") or 0),
        "thumb": meta.get("thumb"),
        "synced_at": now,
        "gone": 0,
    }
    if old is None:
        values.update({"first_seen": now, "is_new": is_new})
    if _thumb_changed(old, values["thumb"], first):
        result.thumb_changed.append(slot_id("movie", key))
    database.upsert("items", ("key",), values)
    _ensure_slot(slot_id("movie", key), key, "movie", None)
    return (old is None or old["updated_at"] != values["updated_at"] or old["folder"] != folder
            or old["files"] != values["files"] or bool(old["gone"]))  # a new file: Radarr may have cleared the folder


async def _store_show(plex: Plex, meta: dict, lib: dict, now: int, baseline: int | None, first: bool,
                      result: SyncResult) -> bool:
    database = db.get()
    key = str(meta["ratingKey"])
    old = database.one("SELECT * FROM items WHERE key=?", (key,))
    added = int(meta.get("addedAt") or now)
    updated = int(meta.get("updatedAt") or 0)
    ids = parse_guids(meta)
    is_new = 0
    if old is None and not first and added >= (baseline or now) - GRACE:
        is_new = 1
        result.new_items.append(key)
    values = {
        "key": key,
        "library_key": lib["key"],
        "type": "show",
        "title": meta.get("title", ""),
        "original_title": meta.get("originalTitle"),
        "year": year_of(meta),
        "sort_title": meta.get("titleSort") or meta.get("title", ""),
        "tmdb_id": ids["tmdb"] or (old or {}).get("tmdb_id"),
        "tvdb_id": ids["tvdb"] or (old or {}).get("tvdb_id"),
        "imdb_id": ids["imdb"] or (old or {}).get("imdb_id"),
        "added_at": added,
        "updated_at": updated,
        "thumb": meta.get("thumb"),
        "synced_at": now,
        "gone": 0,
    }
    if old is None:
        values.update({"first_seen": now, "is_new": is_new})
    if _thumb_changed(old, values["thumb"], first):
        result.thumb_changed.append(slot_id("show", key))
    stale = (
        old is None
        or not old.get("folder")
        or old.get("updated_at") != updated
        or (old.get("details_at") or 0) < now - DETAILS_MAX_AGE
        or bool(old.get("gone"))
    )
    database.upsert("items", ("key",), values)
    _ensure_slot(slot_id("show", key), key, "show", None)
    if stale:
        await load_show_details(plex, key)
    return stale


async def load_show_details(plex: Plex, key: str) -> None:
    """Show folder, ids, locks and the folder of every season (from its episodes)."""
    database = db.get()
    meta = await plex.metadata(key)
    if not meta:
        return
    locations = [loc.get("path") for loc in meta.get("Location", []) or [] if loc.get("path")]
    ids = parse_guids(meta)
    updates = {
        "folder": locations[0] if locations else None,
        "locked": db.dumps(locked_fields(meta)),
        "details_at": db.now(),
    }
    for name in ("tmdb", "tvdb", "imdb"):
        if ids[name]:
            updates[f"{name}_id"] = ids[name]
    database.update("items", "key", key, updates)
    try:
        leaves = await plex.all_leaves(key)
    except Exception:  # noqa: BLE001 - season folders are a nice-to-have
        leaves = []
    folders: dict[int, str] = {}
    for episode in leaves:
        number = episode.get("parentIndex")
        files = media_files(episode)
        if number is None or not files or int(number) in folders:
            continue
        folders[int(number)] = str(PurePosixPath(files[0].replace("\\", "/")).parent)
    for number, folder in folders.items():
        database.execute("UPDATE seasons SET folder=? WHERE show_key=? AND number=?", (folder, key, number))
    if folders:
        database.update("items", "key", key, {"files": db.dumps({str(k): v for k, v in folders.items()})})


async def _store_seasons(plex: Plex, seasons: list[dict], show_keys: set[str], now: int, baseline: int | None,
                         first: bool, result: SyncResult) -> set[str]:
    database = db.get()
    changed: set[str] = set()
    new_show_keys = set(result.new_items)
    for meta in seasons:
        show_key = str(meta.get("parentRatingKey") or "")
        if show_key not in show_keys or meta.get("index") is None:
            continue
        number = int(meta["index"])
        key = str(meta["ratingKey"])
        known = database.one("SELECT key FROM seasons WHERE show_key=? AND number=? AND gone=0", (show_key, number))
        old = database.one("SELECT * FROM seasons WHERE key=?", (key,))
        added = int(meta.get("addedAt") or now)
        is_new = 0
        if known is None and old is None:
            changed.add(show_key)
            had_poster = database.one(
                "SELECT 1 FROM slots WHERE id=? AND (file IS NOT NULL OR applied_at IS NOT NULL)",
                (slot_id("season", show_key, number),),
            )
            if (not first and not had_poster and show_key not in new_show_keys
                    and added >= (baseline or now) - GRACE):
                is_new = 1
                result.new_seasons.append((show_key, number))
        if known is not None and known["key"] != key:
            # Plex re-created the season: keep one row per number.
            database.execute("UPDATE seasons SET gone=1 WHERE key=?", (known["key"],))
        show_files = db.loads(database.scalar("SELECT files FROM items WHERE key=?", (show_key,)), {}) or {}
        folder = show_files.get(str(number)) if isinstance(show_files, dict) else None
        values = {
            "key": key,
            "show_key": show_key,
            "number": number,
            "title": meta.get("title", ""),
            "added_at": added,
            "thumb": meta.get("thumb"),
            "synced_at": now,
            "gone": 0,
        }
        if folder:
            values["folder"] = folder
        if old is None:
            values.update({"first_seen": now, "is_new": is_new})
        if _thumb_changed(old, values["thumb"], first):
            result.thumb_changed.append(slot_id("season", show_key, number))
        database.upsert("seasons", ("key",), values)
        _ensure_slot(slot_id("season", show_key, number), show_key, "season", number)
        result.seasons += 1
    # Seasons gone from Plex
    seen_keys = {str(m["ratingKey"]) for m in seasons}
    for row in database.query("SELECT key, show_key FROM seasons WHERE gone=0"):
        if row["show_key"] in show_keys and row["key"] not in seen_keys:
            database.execute("UPDATE seasons SET gone=1 WHERE key=?", (row["key"],))
    # New seasons need their folders
    for show_key in changed:
        row = database.one("SELECT details_at FROM items WHERE key=?", (show_key,))
        if row and (row["details_at"] or 0) < now:
            await load_show_details(plex, show_key)
    return changed


# -- places and files ----------------------------------------------------------
def place_for(item: dict, kind: str, season: int | None = None, mp: paths.PathMapper | None = None) -> paths.Place:
    mp = mp or mapper()
    database = db.get()
    if kind == "movie":
        files = db.loads(item.get("files"), []) or []
        if not files:
            raise paths.PathError("Plex lists no video file for this movie")
        folder = mp.to_local(item["folder"])
        shared = (
            database.scalar(
                "SELECT COUNT(*) FROM items WHERE type='movie' AND gone=0 AND folder=? AND key<>?",
                (item["folder"], item["key"]),
                0,
            )
            > 0
        )
        return paths.Place("movie", folder, video_stem=paths.video_stem(files[0].rsplit("/", 1)[-1]), shared=shared)
    if not item.get("folder"):
        raise paths.PathError("Plex lists no folder for this show (wait for the next sync)")
    folder = mp.to_local(item["folder"])
    if kind == "show":
        return paths.Place("show", folder)
    season_row = database.one(
        "SELECT folder FROM seasons WHERE show_key=? AND number=? ORDER BY gone ASC LIMIT 1", (item["key"], season)
    )
    season_folder = mp.to_local(season_row["folder"]) if season_row and season_row.get("folder") else None
    return paths.Place("season", folder, season=season, season_folder=season_folder)


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_item(item_key: str, mp: paths.PathMapper | None = None, notes: dict | None = None) -> None:
    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    if not item:
        return
    mp = mp or mapper()
    names = naming()
    for slot in database.query("SELECT * FROM slots WHERE item_key=?", (item_key,)):
        if slot["kind"] == "season":
            alive = database.one(
                "SELECT 1 FROM seasons WHERE show_key=? AND number=? AND gone=0", (item_key, slot["season"])
            )
            if not alive and not slot["file"]:
                continue
        try:
            place = place_for(item, slot["kind"], slot["season"], mp)
        except paths.PathError:
            continue
        _update_slot_file(slot, paths.current(place, names), notes)


def _update_slot_file(slot: dict, path: Path | None, notes: dict | None = None) -> None:
    database = db.get()
    if path is None:
        if slot["file"]:
            database.update("slots", "id", slot["id"], {
                "file": None, "file_sha1": None, "file_size": None, "file_mtime": None,
                "plex_state": "no_file",
            })
            if notes is not None:
                notes["vanished"].append(slot["id"])
        elif slot["plex_state"] is None:
            database.update("slots", "id", slot["id"], {"plex_state": "no_file"})
        return
    try:
        stat = path.stat()
    except OSError:
        return
    if slot["file"] == str(path) and slot["file_size"] == stat.st_size and slot["file_mtime"] == stat.st_mtime:
        return
    sha1 = _sha1(path)
    if notes is not None and slot["plex_state"] is not None and sha1 not in (slot["file_sha1"], slot["written_sha1"]):
        notes["appeared"].append(slot["id"])  # new or changed in the folder: Plex has to read it again
    updates: dict = {"file": str(path), "file_size": stat.st_size, "file_mtime": stat.st_mtime, "file_sha1": sha1}
    if slot["plex_state"] in (None, "no_file"):
        updates["plex_state"] = "unknown"
    if slot["written_sha1"] and sha1 != slot["written_sha1"]:
        # Someone changed the file outside Programarr: the folder is the truth,
        # and set updates leave this slot alone from now on.
        updates.update({"mode": "extern", "source": "extern", "set_key": None, "image_id": None,
                        "image_modified": None, "designer": None, "written_sha1": None})
        event("info", "files", f"File changed outside Programarr: {path.name}", slot["item_key"])
    elif not slot["written_sha1"] and slot["source"] in (None, "extern"):
        stamp = imaging.read_stamp_file(path)
        if stamp and stamp.get("src"):
            updates.update({"source": stamp.get("src"), "set_key": stamp.get("set"), "image_id": stamp.get("img"),
                            "designer": stamp.get("by"), "mode": stamp.get("mode") or "detected",
                            "written_sha1": sha1})
        else:
            updates.setdefault("source", "extern")
    database.update("slots", "id", slot["id"], updates)


def scan_files(item_keys: set[str] | None = None, notes: dict | None = None) -> int:
    database = db.get()
    mp = mapper()
    if item_keys is None:
        keys = [row["key"] for row in database.query("SELECT key FROM items WHERE gone=0")]
    else:
        keys = list(item_keys)
    for key in keys:
        scan_item(key, mp, notes)
    return len(keys)


def detect_naming() -> dict:
    """Which file names the library already uses, for new files in 'auto' mode."""
    database = db.get()
    counts: dict[str, Counter] = {"movie": Counter(), "show": Counter(), "season": Counter()}
    mp = mapper()
    rows = database.query(
        "SELECT s.kind, s.season, s.file, i.* FROM slots s JOIN items i ON i.key=s.item_key "
        "WHERE s.file IS NOT NULL AND i.gone=0 LIMIT 3000"
    )
    for row in rows:
        try:
            place = place_for(row, row["kind"], row["season"], mp)
        except paths.PathError:
            continue
        scheme = paths.scheme_of(Path(row["file"]), place)
        if scheme:
            counts[row["kind"]][scheme] += 1
    detected = {kind: counter.most_common(1)[0][0] for kind, counter in counts.items() if counter}
    if "movie" in detected and detected["movie"] not in ("file",) + paths.MOVIE_STEMS:
        detected.pop("movie")
    detected["counts"] = {kind: dict(counter) for kind, counter in counts.items()}
    detected["at"] = db.now()
    settings.set("naming_detected", detected)
    return detected


def folder_status(sample: int = 25) -> dict:
    """How many item folders are reachable and writable through the path mapping."""
    database = db.get()
    mp = mapper()
    rows = database.query(
        "SELECT folder, type FROM items WHERE gone=0 AND folder IS NOT NULL ORDER BY RANDOM() LIMIT ?", (sample,)
    )
    ok, missing, readonly, examples = 0, 0, 0, []
    for row in rows:
        local = mp.to_local(row["folder"])
        if local is None or not local.is_dir():
            missing += 1
            if len(examples) < 3:
                examples.append({"plex": row["folder"], "local": str(local)})
        elif not os.access(local, os.W_OK):
            readonly += 1
            if len(examples) < 3:
                examples.append({"plex": row["folder"], "local": str(local), "readonly": True})
        else:
            ok += 1
    return {"checked": len(rows), "ok": ok, "missing": missing, "readonly": readonly, "examples": examples}
