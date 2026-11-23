"""Jobs the scheduler and the UI start."""
from __future__ import annotations

import time

from . import apply, auto, binding, catalog, detect, library, paths, reviews, status
from .. import cache, config, db, jobs, notify, settings
from .apply import Assignment
from ..plex import PlexError
from .sources.base import ImageRef, PosterSet, SourceError
from ..util import event, prune_events

_last_error_notice = 0.0


async def _plex_down(exc: Exception) -> None:
    global _last_error_notice
    event("error", "plex", f"Sync failed: {exc}")
    if time.time() - _last_error_notice > 3 * 3600:
        _last_error_notice = time.time()
        await notify.send("error", "Cannot reach Plex", str(exc))


async def sync_and_process(full_scan: bool = False) -> dict:
    jobs.progress(message="Syncing with Plex …")
    try:
        result = await library.sync(full_scan=full_scan)
    except PlexError as exc:
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
        jobs.progress(done, total, "New titles …")
        try:
            outcome = await auto.handle_new_item(key)
        except Exception as exc:  # noqa: BLE001 - one title must not stop the rest
            event("error", "auto", f"New title {key}: {exc}", key)
            outcome = "error"
        summary["auto"][outcome] = summary["auto"].get(outcome, 0) + 1
        done += 1
    for show_key, number in result.new_seasons:
        status.forget(show_key)
        jobs.progress(done, total, "New seasons …")
        try:
            outcome = await binding.handle_new_season(show_key, number)
        except Exception as exc:  # noqa: BLE001
            event("error", "season", f"New season {show_key}/{number}: {exc}", show_key)
            outcome = "error"
        summary["seasons"][outcome] = summary["seasons"].get(outcome, 0) + 1
        done += 1
    if result.new_items or result.new_seasons:
        event("info", "sync", f"Sync: {len(result.new_items)} new titles, {len(result.new_seasons)} new seasons")
    if settings.get("verify_plex"):
        try:
            summary["rechecked"] = await apply.recheck_pending()
        except PlexError:
            pass
    try:
        summary["status"] = await status.refresh_stale()
    except Exception as exc:  # noqa: BLE001 - only a hint for "Complete"
        event("warning", "status", f"Show status: {exc}")
    return summary


async def check_sets(item_keys: list[str] | None = None) -> dict:
    database = db.get()
    full = item_keys is None
    if item_keys is None:
        rows = database.query("SELECT b.item_key FROM bindings b JOIN items i ON i.key=b.item_key WHERE i.gone=0 "
                              f"AND NOT {status.DONE_SQL}")
        item_keys = [row["item_key"] for row in rows]
    totals = {"checked": 0, "applied": 0, "changed": 0, "waiting": 0, "gave_up": 0}
    for index, key in enumerate(item_keys):
        jobs.progress(index, len(item_keys), "Checking sets …")
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
    prune_events(60)
    cache.prune()
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
    return {"ok": True}


async def apply_image(item_key: str, slot: str, ref: dict, mode: str = "manual") -> dict:
    outcomes = await apply.apply(item_key, [Assignment(slot, ImageRef.from_dict(ref))], "manual", mode)
    if outcomes and outcomes[0].ok:
        reviews.resolve_for(item_key, ("new_item", "plex"))
        kind = slot.split(":")[0]
        if kind == "S":
            reviews.resolve_for(item_key, ("new_season",), season=int(slot.split(":")[2]), any_season=False)
            db.get().update("slots", "id", slot, {"waiting": 0})
    return {"outcomes": [o.public() for o in outcomes]}


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


async def adopt_uploads() -> dict:
    """Posters uploaded into Plex (e.g. by Posteria or Marquee) become files in the folder."""
    from ..plex import Plex
    from .sources.base import parse_slot_id

    database = db.get()
    plex = Plex.from_settings()
    rows = database.query(
        "SELECT s.id, s.item_key, s.kind, s.season FROM slots s JOIN items i ON i.key=s.item_key "
        "WHERE s.file IS NULL AND i.gone=0 ORDER BY i.sort_title, s.kind, s.season"
    )
    by_item: dict[str, list[Assignment]] = {}
    checked = 0
    for index, row in enumerate(rows):
        jobs.progress(index, len(rows), "Checking Plex posters …")
        kind, item_key, season = parse_slot_id(row["id"])
        if kind == "season":
            season_row = database.one("SELECT key FROM seasons WHERE show_key=? AND number=? AND gone=0",
                                      (item_key, season))
            if not season_row:
                continue
            rating_key = season_row["key"]
        else:
            rating_key = item_key
        checked += 1
        try:
            posters = await plex.posters(rating_key)
        except PlexError:
            continue
        chosen = next((p for p in posters if p["selected"]), None)
        if not chosen or not (chosen["rating_key"] or "").startswith("upload://"):
            continue
        ref = ImageRef("plex", chosen["rating_key"], kind, season, url=chosen["key"], thumb_url=chosen["thumb"],
                       label="Uploaded to Plex")
        by_item.setdefault(item_key, []).append(Assignment(row["id"], ref))
    adopted = failed = 0
    for index, (item_key, assignments) in enumerate(by_item.items()):
        jobs.progress(index, len(by_item), "Saving as files …")
        outcomes = await apply.apply(item_key, assignments, "adopt", "manual")
        adopted += sum(1 for o in outcomes if o.ok)
        failed += sum(1 for o in outcomes if not o.ok)
    event("info", "plex", f"Uploaded Plex posters saved as files: {adopted}"
                          f"{f', {failed} failed' if failed else ''} ({checked} checked)")
    return {"checked": checked, "adopted": adopted, "failed": failed}


def seed_follows_async() -> None:
    jobs.runner.later(1, catalog.refresh_follows, True)
