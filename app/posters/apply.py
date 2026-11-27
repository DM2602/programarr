"""Turning a chosen image into the poster file Plex reads, and checking Plex took it."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

from .. import db, imaging, settings
from . import backup, library, paths, sources
from ..jobs import runner
from ..plex import TYPE_MOVIE, TYPE_SEASON, TYPE_SHOW, Plex, PlexError, locked_fields
from .sources.base import ImageRef, SourceError, parse_slot_id, slot_id
from ..util import event, season_label

_locks: dict[str, asyncio.Lock] = {}


def item_lock(item_key: str) -> asyncio.Lock:
    lock = _locks.get(item_key)
    if lock is None:
        lock = _locks[item_key] = asyncio.Lock()
    return lock


@dataclass
class Assignment:
    slot_id: str
    ref: ImageRef


@dataclass
class Outcome:
    slot_id: str
    ok: bool
    message: str
    path: str | None = None
    image: bytes | None = None

    def public(self) -> dict:
        return {"slot": self.slot_id, "ok": self.ok, "message": self.message, "path": self.path}


def slot_label(item: dict, slot: str) -> str:
    kind, _, season = parse_slot_id(slot)
    if kind == "season":
        return f"{item['title']} – {season_label(season)}"
    return item["title"]


async def apply(item_key: str, assignments: list[Assignment], origin: str, mode: str,
                refresh: bool = True, note: str | None = None) -> list[Outcome]:
    """Write the images to the media folders, then let Plex re-read the item.

    origin: who decided (manual, auto, set, detect, restore); mode is stored on the
    slot: 'set' slots follow their binding, 'manual' slots stay as chosen.
    """
    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    if not item:
        return [Outcome(a.slot_id, False, "Title not found") for a in assignments]
    outcomes: list[Outcome] = []
    async with item_lock(item_key):
        mp = library.mapper()
        naming = library.naming()
        for assignment in assignments:
            outcomes.append(await _write_one(item, assignment, origin, mode, mp, naming, note))
    if refresh and any(o.ok for o in outcomes):
        await refresh_in_plex(item, [o.slot_id for o in outcomes if o.ok])
    return outcomes


async def _write_one(item: dict, assignment: Assignment, origin: str, mode: str, mp: paths.PathMapper,
                     naming: dict, note: str | None) -> Outcome:
    database = db.get()
    slot = assignment.slot_id
    ref = assignment.ref
    try:
        kind, _, season = parse_slot_id(slot)
        if ref.source == "plex" and ref.extra.get("local"):
            return Outcome(slot, True, "This file is already in the folder")
        data, _ = await sources.fetch(ref)
        prepared = await asyncio.to_thread(imaging.prepare, data, bool(settings.get("keep_png")))
        place = library.place_for(item, kind, season, mp)
        target = paths.target(place, prepared.ext, naming)
        folder = target.parent
        allowed = [place.folder] + ([place.season_folder] if place.season_folder else [])
        if not any(paths.inside(target, root) for root in allowed):
            raise paths.PathError(f"Target is outside the folder: {target}")
        paths.check_folder(folder)
        local_copy = ref.source in ("archive", "backup")  # a kept file: its stamp says where it came from
        info = {
            "src": ref.extra.get("src", ref.source) if local_copy else ref.source,
            "set": ref.set_key,
            "img": ref.extra.get("img") if local_copy else (ref.image_id if ref.source != "upload" else None),
            "by": ref.designer,
            "mode": mode,
            "t": db.now(),
        }
        stamped = imaging.stamp(prepared.data, prepared.ext, {k: v for k, v in info.items() if v is not None})
        archived: list[str] = []
        if target.exists():
            archived.append(str(paths.archive(target, slot, move=False)))
        if settings.get("conflicts") == "archive":
            for other in paths.conflicts(place, target):
                archived.append(str(paths.archive(other, slot, move=True)))
        paths.write_atomic(target, stamped)
        stat = target.stat()
        sha1 = hashlib.sha1(stamped).hexdigest()
        source = info["src"]
        database.update(
            "slots",
            "id",
            slot,
            {
                "file": str(target),
                "file_sha1": sha1,
                "file_size": stat.st_size,
                "file_mtime": stat.st_mtime,
                "source": source,
                "set_key": ref.set_key,
                "image_id": info["img"] if local_copy else ref.image_id,
                "image_modified": ref.modified,
                "designer": ref.designer,
                "source_url": ref.url if ref.source not in ("upload", "archive", "backup") else None,
                "mode": mode,
                "applied_at": db.now(),
                "written_sha1": sha1,
                "plex_state": "pending",
                "waiting": 0,
                "wait_given_up": 0,
            },
        )
        database.execute(
            "INSERT INTO history (at, item_key, slot_id, action, path, archived, source, set_key, image_id, designer,"
            " origin, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (db.now(), item["key"], slot, "write", str(target), archived[0] if archived else None, source,
             ref.set_key, info["img"] if local_copy else ref.image_id, ref.designer, origin,
             note or (f"{len(archived) - 1} more file(s) archived" if len(archived) > 1 else None)),
        )
        backup.save_bytes(item, kind, season, stamped, target.suffix.lower())
        try:
            hashes = await asyncio.to_thread(imaging.hashes, prepared.data)
            database.upsert("hashes", ("key",), {"key": f"file:{sha1}", **hashes, "at": db.now()})
        except imaging.ImageError:
            pass
        paths.prune_archive(slot, int(settings.get("archive_keep") or 0))
        return Outcome(slot, True, f"Saved as {target.name}", str(target), prepared.data)
    except (SourceError, imaging.ImageError, paths.PathError) as exc:
        event("error", "write", f"{slot_label(item, slot)}: {exc}", item["key"])
        return Outcome(slot, False, str(exc))


def _plex_target(item: dict, slot: str) -> tuple[str | None, int]:
    kind, _, season = parse_slot_id(slot)
    if kind == "movie":
        return item["key"], TYPE_MOVIE
    if kind == "show":
        return item["key"], TYPE_SHOW
    row = db.get().one(
        "SELECT key FROM seasons WHERE show_key=? AND number=? AND gone=0", (item["key"], season)
    )
    return (row["key"] if row else None), TYPE_SEASON


async def refresh_in_plex(item: dict, slots: list[str], plex: Plex | None = None) -> None:
    """Unlock poster fields Plex keeps fixed, refresh the item and check the result later."""
    plex = plex or Plex.from_settings()
    try:
        if settings.get("unlock_fields"):
            for slot in slots:
                rating_key, plex_type = _plex_target(item, slot)
                if not rating_key:
                    continue
                meta = await plex.metadata(rating_key)
                if meta and "thumb" in locked_fields(meta):
                    await plex.unlock(item["library_key"], plex_type, rating_key)
                    db.get().execute(
                        "INSERT INTO history (at, item_key, slot_id, action, origin, note) VALUES (?,?,?,?,?,?)",
                        (db.now(), item["key"], slot, "unlock", "system", "Unlocked the poster in Plex"),
                    )
                    event("info", "plex", f"Poster unlocked: {slot_label(item, slot)}", item["key"])
        await plex.refresh(item["key"])
    except PlexError as exc:
        event("error", "plex", f"Refresh failed for {item['title']}: {exc}", item["key"])
        for slot in slots:
            db.get().update("slots", "id", slot, {"plex_state": "unknown"})
        return
    if settings.get("verify_plex") and runner.started:
        runner.later(12, verify, item["key"], slots, False)
        runner.later(75, verify, item["key"], slots, True)


async def verify(item_key: str, slots: list[str], final: bool = False, plex: Plex | None = None) -> dict:
    """Check that Plex shows the file from the folder for each slot."""
    plex = plex or Plex.from_settings()
    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    states: dict[str, str] = {}
    if not item:
        return states
    for slot in slots:
        row = database.one("SELECT plex_state, applied_at FROM slots WHERE id=?", (slot,))
        if final and row and row["plex_state"] == "ok":
            states[slot] = "ok"
            continue
        rating_key, _ = _plex_target(item, slot)
        if not rating_key:
            continue
        try:
            posters = await plex.posters(rating_key)
        except PlexError:
            states[slot] = "unknown"
            continue
        local = [p for p in posters if _is_local(p)]
        selected = [p for p in posters if p["selected"]]
        if selected and _is_local(selected[0]):
            state = "ok"
        elif local:
            state = "not_local"
        else:
            state = "pending"
        states[slot] = state
        database.update("slots", "id", slot, {"plex_state": state, "plex_checked_at": db.now()})
        applied = (row or {}).get("applied_at") if row else None
        if applied is None:
            applied = database.scalar("SELECT applied_at FROM slots WHERE id=?", (slot,), 0)
        # A big show can take Plex a while; only a file Plex knows but does not show,
        # or one it still has not read after an hour, is a problem worth asking about.
        if final and (state == "not_local" or (state == "pending" and (applied or 0) < db.now() - 3600)):
            from . import reviews  # late import: reviews uses apply

            reviews.add(item_key, "plex", "Plex does not show the file from the folder", season=parse_slot_id(slot)[2],
                        data={"slot": slot, "state": state})
        elif state == "ok":
            from . import reviews

            reviews.resolve_for(item_key, ("plex",), season=parse_slot_id(slot)[2], any_season=False)
    return states


async def recheck_pending(limit: int = 40) -> int:
    """Look again at files Plex had not read yet (called with every sync)."""
    database = db.get()
    rows = database.query(
        "SELECT item_key, id FROM slots WHERE plex_state='pending' AND file IS NOT NULL AND applied_at < ? "
        "ORDER BY plex_checked_at LIMIT ?",
        (db.now() - 600, limit),
    )
    for row in rows:
        try:
            await verify(row["item_key"], [row["id"]], final=True)
        except PlexError:
            break
    return len(rows)


def _is_local(poster: dict) -> bool:
    return poster.get("provider") == "local" or "localmedia" in (poster.get("rating_key") or "") \
        or (poster.get("rating_key") or "").startswith("file://")


async def force_local(item_key: str, slot: str, plex: Plex | None = None) -> str:
    """Select the folder file in Plex explicitly, then release the lock this sets."""
    plex = plex or Plex.from_settings()
    item = db.get().one("SELECT * FROM items WHERE key=?", (item_key,))
    if not item:
        raise SourceError("Title not found")
    rating_key, plex_type = _plex_target(item, slot)
    if not rating_key:
        raise SourceError("Plex does not know this season")
    await plex.refresh(item_key)
    await asyncio.sleep(2)
    posters = await plex.posters(rating_key)
    local = [p for p in posters if _is_local(p)]
    if not local:
        db.get().update("slots", "id", slot, {"plex_state": "pending"})
        return "Plex has not picked up the file yet. Check \"Use local assets\" in the library settings."
    await plex.select_poster(rating_key, local[0]["rating_key"] or local[0]["key"])
    await plex.unlock(item["library_key"], plex_type, rating_key)
    db.get().update("slots", "id", slot, {"plex_state": "ok", "plex_checked_at": db.now()})
    from . import reviews

    reviews.resolve_for(item_key, ("plex",), season=parse_slot_id(slot)[2], any_season=False)
    return "Plex now shows the file from the folder"


def _is_upload(poster: dict) -> bool:
    return (poster.get("rating_key") or "").startswith("upload://")


async def take_plex_choice(item_key: str, slot: str, plex: Plex | None = None, uploads: bool = False) -> str | None:
    """A poster picked or uploaded in Plex becomes the file in the folder; the lock goes.

    Plex locks the poster field when you pick or upload one; that lock is what counts. With
    uploads=True an unlocked upload counts too, for slots without a file. Without a lock the file
    in the folder wins: Plex reads it again. Returns "saved", "unlocked" (Plex showed the file),
    "refreshed" or None.
    """
    plex = plex or Plex.from_settings()
    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    row = database.one("SELECT * FROM slots WHERE id=?", (slot,))
    if not item or not row or item.get("gone"):
        return None
    rating_key, plex_type = _plex_target(item, slot)
    if not rating_key:
        return None
    meta = await plex.metadata(rating_key)
    locked = bool(meta) and "thumb" in locked_fields(meta)
    if not locked and not row["file"] and not uploads:
        return None
    chosen = next((p for p in await plex.posters(rating_key) if p["selected"]), None)
    if chosen is None:
        return None
    if _is_local(chosen):
        if not locked:
            return None
        await plex.unlock(item["library_key"], plex_type, rating_key)
        database.update("slots", "id", slot, {"plex_state": "ok", "plex_checked_at": db.now()})
        return "unlocked"
    if not locked and row["file"]:
        await refresh_in_plex(item, [slot], plex)  # Plex shows another poster although the folder has one
        return "refreshed"
    if not locked and not _is_upload(chosen):
        return None
    kind, _, season = parse_slot_id(slot)
    upload = _is_upload(chosen)
    ref = ImageRef("plex", chosen["rating_key"] or chosen["key"], kind, season, url=chosen["key"],
                   thumb_url=chosen["thumb"], label="Uploaded to Plex" if upload else "Chosen in Plex")
    outcomes = await apply(item_key, [Assignment(slot, ref)], "adopt" if upload else "plex", "manual")
    if not (outcomes and outcomes[0].ok):
        return None
    if locked and not settings.get("unlock_fields"):
        # The file holds the chosen poster now; with the lock Plex would never show the file.
        await plex.unlock(item["library_key"], plex_type, rating_key)
        await plex.refresh(item["key"])
    event("info", "plex", f"{'Uploaded to' if upload else 'Selected in'} Plex, saved as file: "
                          f"{slot_label(item, slot)}", item_key)
    return "saved"


async def keep_plex_poster(item_key: str, slot: str, plex: Plex | None = None) -> bool:
    """A slot without a file gets the poster Plex shows, as file (setting plex_poster_files)."""
    plex = plex or Plex.from_settings()
    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    row = database.one("SELECT * FROM slots WHERE id=?", (slot,))
    if not item or not row or row["file"] or item.get("gone"):
        return False
    rating_key, _ = _plex_target(item, slot)
    if not rating_key:
        return False
    chosen = next((p for p in await plex.posters(rating_key) if p["selected"]), None)
    if chosen is None or _is_local(chosen):
        return False
    kind, _, season = parse_slot_id(slot)
    ref = ImageRef("plex", chosen["rating_key"] or chosen["key"], kind, season, url=chosen["key"],
                   thumb_url=chosen["thumb"], label="Plex poster")
    outcomes = await apply(item_key, [Assignment(slot, ref)], "plex_file", "manual")
    return bool(outcomes and outcomes[0].ok)


async def remove_file(item_key: str, slot: str) -> str:
    """Move the poster file to the archive; Plex falls back to its own poster."""
    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    row = database.one("SELECT * FROM slots WHERE id=?", (slot,))
    if not item or not row:
        raise SourceError("Poster slot not found")
    kind, _, season = parse_slot_id(slot)
    async with item_lock(item_key):
        place = library.place_for(item, kind, season)
        files = paths.existing(place)
        if not files:
            return "No file"
        archived = [str(paths.archive(path, slot, move=True)) for path in files]
        backup.drop(item, kind, season)  # removed on purpose: the backup must not bring it back
        database.update("slots", "id", slot, {
            "file": None, "file_sha1": None, "file_size": None, "file_mtime": None, "source": None,
            "set_key": None, "image_id": None, "designer": None, "mode": "manual", "written_sha1": None,
            "plex_state": "no_file", "applied_at": db.now(),
        })
        database.execute(
            "INSERT INTO history (at, item_key, slot_id, action, path, archived, origin, note) VALUES (?,?,?,?,?,?,?,?)",
            (db.now(), item_key, slot, "remove", str(files[0]), archived[0], "manual", "Moved the file to the archive"),
        )
    await refresh_in_plex(item, [slot])
    return "Moved the file to the archive, Plex shows its own poster again"


async def restore(history_id: int) -> list[Outcome]:
    database = db.get()
    entry = database.one("SELECT * FROM history WHERE id=?", (history_id,))
    if not entry or not entry.get("archived"):
        raise SourceError("There is no archived file for this entry")
    archived = Path(entry["archived"])
    if not archived.exists():
        raise SourceError("The archived file no longer exists")
    stamp = imaging.read_stamp_file(archived) or {}
    kind, _, season = parse_slot_id(entry["slot_id"])
    ref = ImageRef(
        source="archive",
        image_id=archived.name,
        kind=kind,
        season=season,
        url=str(archived),
        designer=stamp.get("by"),
        set_key=stamp.get("set"),
        extra={"src": stamp.get("src") or "archive"},
    )
    return await apply(entry["item_key"], [Assignment(entry["slot_id"], ref)], "restore", "manual",
                       note=f"Restored: {paths.original_name(archived)}")


def slot_for(item_key: str, kind: str, season: int | None) -> str:
    return slot_id(kind, item_key, season)
