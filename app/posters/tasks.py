"""Jobs the scheduler and the UI start."""
from __future__ import annotations

import logging
import time

from . import apply, auto, backup, binding, catalog, detect, library, paths, reviews, status, views
from .. import cache, config, db, jobs, notify, settings
from ..collections import sync as collection_sync
from .apply import Assignment
from ..plex import Plex, PlexError
from .sources.base import ImageRef, PosterSet, SourceError, parse_slot_id
from ..util import event, prune_events

log = logging.getLogger("programarr.tasks")

PLEX_FILE_KINDS = {"movies": ("movie",), "all": ("movie", "show", "season")}
ID_COLUMNS = ("tmdb_id", "tvdb_id", "imdb_id")

# Titles Radarr or Sonarr named in a webhook (import, upgrade, rename). The next sync reads their
# folders whatever Plex says: on an upgrade Radarr deletes the folder, poster included.
_named: list[tuple[str, dict[str, str]]] = []
_named_keys: set[str] = set()  # resolved, waiting for a sync that reached Plex


def name_title(kind: str, ids: dict) -> bool:
    clean = {column: str(value) for column, value in ids.items() if column in ID_COLUMNS and value}
    if clean:
        _named.append((kind, clean))
    return bool(clean)


def _named_items() -> set[str]:
    database = db.get()
    while _named:
        kind, ids = _named.pop()
        where = " OR ".join(f"{column}=?" for column in ids)
        rows = database.query(f"SELECT key FROM items WHERE gone=0 AND type=? AND ({where})", (kind, *ids.values()))
        _named_keys.update(row["key"] for row in rows)
    keys = set(_named_keys)
    _named_keys.clear()
    return keys
PLEX_FILE_RETRY = 7 * 86400  # a title whose Plex poster could not be saved is tried again after a week

_last_error_notice = 0.0


async def _plex_down(exc: Exception) -> None:
    global _last_error_notice
    event("error", "plex", f"Sync failed: {exc}")
    if time.time() - _last_error_notice > 3 * 3600:
        _last_error_notice = time.time()
        await notify.send("error", "Cannot reach Plex", str(exc))


async def sync_and_process(full_scan: bool = False) -> dict:
    jobs.progress(message="Syncing with Plex…")
    named = _named_items()
    try:
        result = await library.sync(full_scan=full_scan, also_scan=named)
    except PlexError as exc:
        _named_keys.update(named)  # next time
        settings.set("last_sync", db.now())
        await _plex_down(exc)
        raise
    summary = {
        "items": result.items,
        "new_items": len(result.new_items),
        "new_seasons": len(result.new_seasons),
        "gone": result.gone,
        "first_sync": result.first_sync,
        "auto": {},
        "seasons": {},
    }
    if result.first_sync:
        event("info", "sync", "First sync for " + ", ".join(result.first_sync)
              + ": existing titles are left as they are, automation applies to new titles from now on")
    if not settings.get("automation"):
        # Nothing is written without a click; new titles and seasons are only recorded.
        skipped = len(result.new_items) + len(result.new_seasons)
        if skipped:
            summary["skipped"] = skipped
            event("info", "sync", f"Automation is off: {len(result.new_items)} new titles and "
                                  f"{len(result.new_seasons)} new seasons left as they are")
        for show_key, _ in result.new_seasons:
            status.forget(show_key)
        result.new_items, result.new_seasons = [], []
    total = len(result.new_items) + len(result.new_seasons)
    done = 0
    for key in result.new_items:
        jobs.progress(done, total, "New titles…")
        try:
            outcome = await auto.handle_new_item(key)
        except Exception as exc:  # noqa: BLE001 - one title must not stop the rest
            event("error", "auto", f"New title {key}: {exc}", key)
            outcome = "error"
        summary["auto"][outcome] = summary["auto"].get(outcome, 0) + 1
        done += 1
    for show_key, number in result.new_seasons:
        status.forget(show_key)
        jobs.progress(done, total, "New seasons…")
        try:
            outcome = await binding.handle_new_season(show_key, number)
        except Exception as exc:  # noqa: BLE001
            event("error", "season", f"New season {show_key}/{number}: {exc}", show_key)
            outcome = "error"
        summary["seasons"][outcome] = summary["seasons"].get(outcome, 0) + 1
        done += 1
    if result.new_items or result.new_seasons:
        event("info", "sync", f"Sync: {len(result.new_items)} new titles, {len(result.new_seasons)} new seasons")
    if settings.get("automation"):
        summary.update(await _follow_up(result))
    # Titles Radarr or Sonarr named while this sync ran
    while _named or _named_keys:
        notes: dict[str, list[str]] = {"vanished": [], "appeared": []}
        library.scan_files(_named_items(), notes)
        if settings.get("automation"):
            more = await _follow_up(library.SyncResult(vanished=notes["vanished"], appeared=notes["appeared"]))
            for key, value in more.items():
                summary[key] = summary.get(key, 0) + value
    if settings.get("verify_plex"):
        try:
            summary["rechecked"] = await apply.recheck_pending()
        except PlexError:
            pass
    try:
        summary["status"] = await status.refresh_stale()
    except Exception as exc:  # noqa: BLE001 - only a hint for "Complete"
        event("warning", "status", f"Show status: {exc}")
    try:
        summary["queue"] = await collection_sync.process_queue()
    except Exception as exc:  # noqa: BLE001 - the queue waits for the next sync
        log.exception("Queue")
        event("error", "collections", f"Queue: {exc}")
    return summary


def _by_item(slots: list[str], skip: set[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for slot in slots:
        item_key = parse_slot_id(slot)[1]
        if item_key not in skip and slot not in grouped.get(item_key, []):
            grouped.setdefault(item_key, []).append(slot)
    return grouped


async def _follow_up(result: library.SyncResult) -> dict:
    """After a sync, with automation on: a poster picked in Plex becomes the file, a missing file
    comes back from the backup, and a file that appeared or changed in the folder is read by Plex."""
    new_items = set(result.new_items)
    plex = Plex.from_settings()
    summary = {"plex_choices": 0, "restored": 0, "refreshed": 0}
    for item_key, slots in _by_item(result.thumb_changed, new_items).items():
        for slot in slots:
            try:
                outcome = await apply.take_plex_choice(item_key, slot, plex)
            except (PlexError, SourceError) as exc:
                event("warning", "plex", f"Poster selected in Plex not saved: {exc}", item_key)
                continue
            summary["plex_choices"] += outcome == "saved"
            summary["refreshed"] += outcome == "refreshed"
    if backup.enabled():
        for item_key, slots in _by_item(result.vanished, new_items).items():
            summary["restored"] += len(await backup.restore(item_key, slots))
    appeared = [slot for slot in result.appeared if slot not in result.vanished]
    for item_key, slots in _by_item(appeared, new_items).items():
        item = db.get().one("SELECT * FROM items WHERE key=?", (item_key,))
        if item and not item.get("gone"):
            await apply.refresh_in_plex(item, slots, plex)
            summary["refreshed"] += 1
    return {key: value for key, value in summary.items() if value}


async def check_folders() -> dict:
    """Every folder, for poster files added, replaced or deleted outside Programarr (setting
    folder_check_hours; a sync reads only the folders of titles Plex changed)."""
    settings.set("last_folder_check", db.now())
    jobs.progress(message="Checking folders…")
    notes: dict[str, list[str]] = {"vanished": [], "appeared": []}
    result: dict = {"checked": library.scan_files(None, notes)}
    if settings.get("automation"):
        try:
            result.update(await _follow_up(library.SyncResult(vanished=notes["vanished"], appeared=notes["appeared"])))
        except PlexError as exc:
            event("warning", "plex", f"Folder check: {exc}")
    return result


async def keep_plex_posters(limit: int | None = 200) -> dict:
    """Titles without a poster file get the poster Plex shows, as file (setting plex_poster_files).

    A copy in the backup comes first. Titles waiting under Review are left alone, and so are
    posters removed on purpose. A title gets an hour after it appeared, so Plex has its poster by then.
    """
    kinds = PLEX_FILE_KINDS.get(settings.get("plex_poster_files"))
    if not kinds:
        return {"kept": 0, "restored": 0, "checked": 0}
    database = db.get()
    marks = ",".join("?" for _ in kinds)
    rows = database.query(
        "SELECT s.id, s.item_key FROM slots s JOIN items i ON i.key=s.item_key "
        f"WHERE s.file IS NULL AND i.gone=0 AND s.kind IN ({marks}) AND COALESCE(i.added_at, 0) < ? "
        "AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.item_key=i.key AND r.status='open' AND r.kind='new_item') "
        "AND (s.kind <> 'season' OR EXISTS (SELECT 1 FROM seasons z WHERE z.show_key=s.item_key "
        "     AND z.number=s.season AND z.gone=0)) "
        "AND COALESCE((SELECT h.action FROM history h WHERE h.slot_id=s.id AND h.action IN ('write', 'remove') "
        "     ORDER BY h.id DESC LIMIT 1), '') <> 'remove' "
        "ORDER BY i.added_at DESC",
        (*kinds, db.now() - 3600),
    )
    plex = Plex.from_settings()
    kept = restored = checked = 0
    for index, row in enumerate(rows):
        if limit is not None and checked >= limit:
            break
        if cache.get(f"plexfile:tried:{row['id']}"):
            continue
        jobs.progress(index, len(rows), "Keeping Plex posters as files…")
        checked += 1
        if await backup.restore(row["item_key"], [row["id"]]):
            restored += 1
            continue
        try:
            if await apply.keep_plex_poster(row["item_key"], row["id"], plex):
                kept += 1
                continue
        except PlexError:
            break  # Plex not reachable: next time
        except SourceError:
            pass
        cache.put(f"plexfile:tried:{row['id']}", 1, PLEX_FILE_RETRY)
    if kept:
        event("info", "plex", f"Plex posters kept as files: {kept}")
    return {"kept": kept, "restored": restored, "checked": checked}


async def take_from_plex() -> dict:
    """Posters picked or uploaded in Plex become files, then titles without a file get Plex' poster."""
    database = db.get()
    plex = Plex.from_settings()
    rows = database.query(
        "SELECT s.id, s.item_key FROM slots s JOIN items i ON i.key=s.item_key WHERE i.gone=0 "
        "AND (s.kind <> 'season' OR EXISTS (SELECT 1 FROM seasons z WHERE z.show_key=s.item_key "
        "     AND z.number=s.season AND z.gone=0)) ORDER BY i.sort_title, s.kind, s.season"
    )
    adopted = unlocked = refreshed = failed = 0
    for index, row in enumerate(rows):
        jobs.progress(index, len(rows), "Checking Plex posters…")
        try:
            outcome = await apply.take_plex_choice(row["item_key"], row["id"], plex, uploads=True)
        except (PlexError, SourceError):
            failed += 1
            continue
        adopted += outcome == "saved"
        unlocked += outcome == "unlocked"
        refreshed += outcome == "refreshed"
    files = await keep_plex_posters(limit=None)
    event("info", "plex", f"Imported from Plex: {adopted} selected in Plex, {files['kept']} Plex posters kept"
                          f"{f', {failed} failed' if failed else ''} ({len(rows)} checked)")
    return {"checked": len(rows), "adopted": adopted, "kept": files["kept"], "restored": files["restored"],
            "unlocked": unlocked, "refreshed": refreshed, "failed": failed}


async def keep_plex_for_review(review_id: int) -> dict:
    """Keep what a title or season under Review shows now: files in the folder stay, the posters
    Plex shows become files."""
    database = db.get()
    review = database.one("SELECT * FROM reviews WHERE id=? AND status='open'", (review_id,))
    if not review:
        raise SourceError("This review is already closed")
    item = database.one("SELECT * FROM items WHERE key=?", (review["item_key"],))
    if not item:
        raise SourceError("Title not found")
    seasons = catalog.present_seasons(item["key"]) if item["type"] == "show" else []
    slots = [poster["slot"] for poster in views.current_posters({**review, "type": item["type"]}, seasons)]
    plex = Plex.from_settings()
    kept = 0
    for slot in slots:
        kept += await _keep(item, slot, plex)
    if slots and not kept:
        raise SourceError("Plex shows no poster to keep")
    reviews.resolve(review_id)
    if review["kind"] == "new_season" and review["season"] is not None:
        database.update("slots", "id", f"S:{item['key']}:{review['season']}", {"waiting": 0})
    event("info", "plex", f"Current posters kept: {kept}", item["key"])
    return {"kept": kept}


async def backup_now() -> dict:
    jobs.progress(message="Checking folders…")
    library.scan_files(None)
    return backup.run()


async def restore_backup() -> dict:
    """Every poster that is missing in its folder and has a copy in the backup comes back."""
    jobs.progress(message="Checking folders…")
    library.scan_files(None)
    database = db.get()
    keys = [row["item_key"] for row in database.query(
        "SELECT DISTINCT s.item_key FROM slots s JOIN items i ON i.key=s.item_key WHERE s.file IS NULL AND i.gone=0")]
    restored = 0
    for index, key in enumerate(keys):
        jobs.progress(index, len(keys), "Restoring from backup…")
        restored += len(await backup.restore(key))
    return {"restored": restored, "checked": len(keys)}


async def check_sets(item_keys: list[str] | None = None) -> dict:
    database = db.get()
    full = item_keys is None
    if item_keys is None:
        rows = database.query("SELECT b.item_key FROM bindings b JOIN items i ON i.key=b.item_key WHERE i.gone=0 "
                              f"AND NOT {status.DONE_SQL}")
        item_keys = [row["item_key"] for row in rows]
    totals = {"checked": 0, "applied": 0, "changed": 0, "waiting": 0, "gave_up": 0}
    for index, key in enumerate(item_keys):
        jobs.progress(index, len(item_keys), "Checking sets…")
        try:
            result = await binding.check_binding(key)
        except Exception as exc:  # noqa: BLE001
            event("warning", "set", f"Set check failed: {exc}", key)
            continue
        totals["checked"] += 1
        for name in ("applied", "changed", "waiting", "gave_up"):
            totals[name] += result.get(name, 0)
    if full:
        settings.set("last_set_check", db.now())
    jobs.progress(len(item_keys), len(item_keys), "Done")
    return totals


async def maintenance() -> dict:
    from ..flows import store as flows  # late imports: lists and flows depend on the media modules
    from ..lists import exclusions, runs

    prune_events(60)
    cache.prune()
    flows.prune()
    runs.prune()
    await exclusions.sync()  # both ways with Radarr and Sonarr, every six hours with the clean-up
    database = db.get()
    database.execute("DELETE FROM jobs WHERE finished_at IS NOT NULL AND finished_at < ?", (db.now() - 30 * 86400,))
    uploads = config.DATA_DIR / "uploads"
    if uploads.exists():
        for path in uploads.iterdir():
            try:
                if path.stat().st_mtime < time.time() - 2 * 86400:
                    path.unlink()
            except OSError:
                pass
    keep = int(settings.get("archive_keep") or 0)
    if keep:
        for row in database.query("SELECT DISTINCT slot_id FROM history WHERE archived IS NOT NULL"):
            paths.prune_archive(row["slot_id"], keep)
    result: dict = {"ok": True}
    try:
        result["backup"] = backup.run()
    except OSError as exc:
        event("error", "backup", f"Poster backup failed: {exc}")
    if settings.get("automation"):
        try:
            result["plex_files"] = await keep_plex_posters()
        except (PlexError, SourceError) as exc:
            event("warning", "plex", f"Keeping Plex posters as files: {exc}")
    return result


def _slot_decided(item_key: str, slot: str) -> None:
    """A poster was chosen for the slot: the reviews asking for one are done."""
    reviews.resolve_for(item_key, ("new_item", "plex"))
    if slot.split(":")[0] == "S":
        reviews.resolve_for(item_key, ("new_season",), season=int(slot.split(":")[2]), any_season=False)
        db.get().update("slots", "id", slot, {"waiting": 0})


async def apply_image(item_key: str, slot: str, ref: dict, mode: str = "manual") -> dict:
    outcomes = await apply.apply(item_key, [Assignment(slot, ImageRef.from_dict(ref))], "manual", mode)
    if outcomes and outcomes[0].ok:
        _slot_decided(item_key, slot)
    return {"outcomes": [o.public() for o in outcomes]}


async def _keep(item: dict, slot: str, plex: Plex) -> bool:
    """Keep what the slot shows now: the file in the folder (Plex reads it again if it shows another
    poster), or the poster Plex shows, saved as file."""
    row = db.get().one("SELECT * FROM slots WHERE id=?", (slot,))
    if not row:
        return False
    if row["file"]:
        if row["plex_state"] not in ("ok", None):
            await apply.refresh_in_plex(item, [slot], plex)
        return True
    return await apply.keep_plex_poster(item["key"], slot, plex)


async def keep_current(item_key: str, slot: str) -> dict:
    item = db.get().one("SELECT * FROM items WHERE key=?", (item_key,))
    if not item:
        raise SourceError("Title not found")
    had_file = bool(db.get().scalar("SELECT file FROM slots WHERE id=?", (slot,)))
    if not await _keep(item, slot, Plex.from_settings()):
        raise SourceError("Plex shows no poster to keep")
    _slot_decided(item_key, slot)
    return {"kept": 1, "message": "File kept" if had_file else "Plex poster kept as file"}


async def bind_set(item_key: str, poster_set: dict, replace_all: bool = True, review_id: int | None = None) -> dict:
    result = await binding.bind(item_key, PosterSet.from_dict(poster_set), origin="manual", replace_all=replace_all)
    result.pop("image", None)
    if review_id and result["written"]:
        reviews.resolve(review_id)
    return result


async def check_one(item_key: str) -> dict:
    return await binding.check_binding(item_key)


async def detect_library(scope: str = "shows") -> dict:
    return await detect.run(scope)


async def detect_one(item_key: str) -> dict:
    proposal = await detect.detect_item(item_key)
    confidence = (proposal or {}).get("confidence")
    return {"checked": 1, "found": 1 if proposal else 0, "high": 1 if confidence == "high" else 0,
            "confidence": confidence}


async def force_local(item_key: str, slot: str) -> dict:
    message = await apply.force_local(item_key, slot)
    return {"message": message}


async def remove_file(item_key: str, slot: str) -> dict:
    return {"message": await apply.remove_file(item_key, slot)}


async def restore(history_id: int) -> dict:
    outcomes = await apply.restore(history_id)
    return {"outcomes": [o.public() for o in outcomes]}


async def accept_changes(review_id: int, take: bool) -> dict:
    return await binding.accept_changes(review_id, take)


async def rescan(item_key: str | None = None) -> dict:
    if item_key:
        item = db.get().one("SELECT * FROM items WHERE key=?", (item_key,))
        if item and item["type"] == "show":
            from ..plex import Plex

            await library.load_show_details(Plex.from_settings(), item_key)
        library.scan_item(item_key)
        return {"scanned": 1}
    count = library.scan_files(None)
    library.detect_naming()
    return {"scanned": count}


async def plex_refresh(item_key: str) -> dict:
    item = db.get().one("SELECT * FROM items WHERE key=?", (item_key,))
    if not item:
        raise SourceError("Title not found")
    slots = [row["id"] for row in db.get().query("SELECT id FROM slots WHERE item_key=? AND file IS NOT NULL",
                                                   (item_key,))]
    await apply.refresh_in_plex(item, slots)
    return {"message": "Refreshing metadata"}


async def retry_new_item(item_key: str) -> dict:
    reviews.resolve_for(item_key, ("new_item",))
    return {"outcome": await auto.handle_new_item(item_key)}


def seed_follows_async() -> None:
    jobs.runner.later(1, catalog.refresh_follows, True)
