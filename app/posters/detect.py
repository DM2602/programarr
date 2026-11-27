"""Match existing: which set do the poster files already in the folders come from?"""
from __future__ import annotations

import asyncio
from pathlib import Path

from . import binding, catalog, reviews, sources, status
from .. import db, imaging, jobs, notify, settings
from .sources.base import ImageRef, PosterSet, SourceError, slot_id
from ..util import event

MAX_SETS_DEEP = 4  # sets per title whose season posters are compared too


async def _local_hashes(item: dict) -> dict[str, dict]:
    database = db.get()
    result = {}
    for slot in database.query("SELECT * FROM slots WHERE item_key=? AND file IS NOT NULL", (item["key"],)):
        if slot["kind"] == "season":
            alive = database.one("SELECT 1 FROM seasons WHERE show_key=? AND number=? AND gone=0",
                                 (item["key"], slot["season"]))
            if not alive:
                continue
        key = f"file:{slot['file_sha1']}"
        row = database.one("SELECT p, d, g FROM hashes WHERE key=?", (key,))
        if row:
            result[slot["id"]] = row
            continue
        try:
            data = await asyncio.to_thread(Path(slot["file"]).read_bytes)
            hashes = await asyncio.to_thread(imaging.hashes, data)
        except (OSError, imaging.ImageError):
            continue
        database.upsert("hashes", ("key",), {"key": key, **hashes, "at": db.now()})
        result[slot["id"]] = hashes
    return result


async def remote_hashes(ref: ImageRef) -> dict | None:
    database = db.get()
    key = f"img:{ref.source}:{ref.image_id}:{ref.modified or ''}"
    row = database.one("SELECT p, d, g FROM hashes WHERE key=?", (key,))
    if row:
        return row
    try:
        data = await sources.fetch_thumb_cached(ref)
        hashes = await asyncio.to_thread(imaging.hashes, data)
    except (SourceError, imaging.ImageError):
        return None
    database.upsert("hashes", ("key",), {"key": key, **hashes, "at": db.now()})
    return hashes


def _stamp_proposal(item: dict) -> dict | None:
    """Files Litfass wrote carry their origin; no image comparison needed."""
    database = db.get()
    rows = database.query(
        "SELECT * FROM slots WHERE item_key=? AND file IS NOT NULL AND set_key IS NOT NULL AND source IN ('mediux','tpdb')",
        (item["key"],),
    )
    if not rows:
        return None
    keys = {(row["source"], row["set_key"]) for row in rows}
    if len(keys) != 1:
        return None
    source, set_key = keys.pop()
    return {"source": source, "set_key": set_key, "slots": [row["id"] for row in rows]}


async def _stamp_match(item: dict) -> tuple[PosterSet, dict[str, ImageRef], int] | None:
    """The set named in the stamps of the files, with the images that match them."""
    stamped = _stamp_proposal(item)
    if not stamped:
        return None
    try:
        poster_set = await catalog.load_set(stamped["source"], stamped["set_key"], item, fresh=False)
    except SourceError:
        return None
    if not poster_set:
        return None
    database = db.get()
    matched = {}
    for slot in stamped["slots"]:
        row = database.one("SELECT kind, season FROM slots WHERE id=?", (slot,))
        image = poster_set.image_for(row["kind"], row["season"])
        if image:
            matched[slot] = image
    return (poster_set, matched, len(stamped["slots"])) if matched else None


async def relink(item_key: str) -> bool:
    """Files that carry a set's stamp (restored from the backup) link the title to that set again."""
    item = db.get().one("SELECT * FROM items WHERE key=?", (item_key,))
    if not item or binding.get(item_key):
        return False
    match = await _stamp_match(item)
    if not match:
        return False
    binding.bind_existing(item_key, match[0], match[1], origin="backup")
    return True


async def detect_item(item_key: str, store: bool = True) -> dict | None:
    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    if not item or binding.get(item_key):
        return None
    local = await _local_hashes(item)
    if not local:
        return None
    main_slot = slot_id(item["type"], item_key)
    match = await _stamp_match(item)
    if match:
        return _propose(item, match[0], match[1], match[2], "high", 1.0, store, via="origin stamp in the file")
    found = await catalog.find_sets(item)
    sets = found.all()
    if not sets:
        return None
    scored: list[tuple[PosterSet, dict]] = []
    for poster_set in sets:
        main = poster_set.image_for(item["type"])
        if main_slot in local and main:
            hashes = await remote_hashes(main)
            if not hashes:
                continue
            result = imaging.compare(local[main_slot], hashes)
            if result["match"]:
                scored.append((poster_set, result))
        elif main_slot not in local and item["type"] == "show" and poster_set.seasons():
            scored.append((poster_set, {"score": 0.0, "match": False}))
        jobs.progress()
    if not scored:
        return None
    scored.sort(key=lambda entry: -entry[1]["score"])
    best: list[tuple[PosterSet, dict, float, int]] = []
    for poster_set, main_result in scored[:MAX_SETS_DEEP]:
        if item["type"] == "show" and poster_set.source == "tpdb" and not poster_set.complete:
            try:
                poster_set = await catalog.complete_tpdb(item, poster_set)
            except SourceError:
                pass
        matched: dict[str, ImageRef] = {}
        compared = 0
        total_score = 0.0
        if main_result.get("match"):
            matched[main_slot] = poster_set.image_for(item["type"])
            compared += 1
            total_score += main_result["score"]
        for slot, hashes in local.items():
            if slot == main_slot:
                continue
            row = database.one("SELECT kind, season FROM slots WHERE id=?", (slot,))
            image = poster_set.image_for(row["kind"], row["season"])
            if not image:
                continue
            compared += 1
            remote = await remote_hashes(image)
            if remote:
                result = imaging.compare(hashes, remote)
                if result["match"]:
                    matched[slot] = image
                    total_score += result["score"]
        if matched:
            best.append((poster_set, matched, total_score / max(1, len(matched)), compared))
    if not best:
        return None
    best.sort(key=lambda entry: (-len(entry[1]), -entry[2]))
    poster_set, matched, avg, compared = best[0]
    runner_up = best[1] if len(best) > 1 else None
    local_count = len(local)
    share = len(matched) / max(1, local_count)
    confidence = "medium"
    if main_slot in matched and share >= 0.8 and (runner_up is None or len(runner_up[1]) < len(matched)):
        confidence = "high"
    return _propose(item, poster_set, matched, compared, confidence, avg, store, via="image comparison",
                    local_count=local_count)


def _propose(item: dict, poster_set: PosterSet, matched: dict[str, ImageRef], compared: int, confidence: str,
             score: float, store: bool, via: str, local_count: int | None = None) -> dict:
    proposal = {
        "set": poster_set.to_dict(),
        "matched": {slot: image.to_dict() for slot, image in matched.items()},
        "compared": compared,
        "local": local_count if local_count is not None else compared,
        "confidence": confidence,
        "score": round(score, 3),
        "via": via,
    }
    if store:
        reviews.add(
            item["key"],
            "detect",
            f"Set \"{poster_set.title}\" by {poster_set.designer}: {len(matched)} of "
            f"{proposal['local']} {'poster matches' if proposal['local'] == 1 else 'posters match'}",
            data=proposal,
        )
    return proposal


async def run(scope: str = "shows") -> dict:
    """Background job over the library (titles without binding that have poster files)."""
    database = db.get()
    types = {"shows": ("show",), "movies": ("movie",), "all": ("show", "movie")}.get(scope, ("show",))
    if "movie" in types and not settings.get("detect_movies") and scope != "movies":
        types = tuple(t for t in types if t != "movie")
    placeholders = ",".join("?" for _ in types)
    items = database.query(
        f"SELECT DISTINCT i.key, i.title FROM items i JOIN slots s ON s.item_key=i.key "
        f"LEFT JOIN bindings b ON b.item_key=i.key "
        f"WHERE i.gone=0 AND s.file IS NOT NULL AND b.item_key IS NULL AND i.type IN ({placeholders}) "
        f"AND NOT {status.DONE_SQL} ORDER BY i.sort_title",
        types,
    )
    await catalog.refresh_follows()
    total = len(items)
    found = high = errors = 0
    jobs.progress(0, total, "Starting…")
    for index, row in enumerate(items, start=1):
        jobs.progress(index - 1, total, row["title"])
        try:
            proposal = await detect_item(row["key"])
        except SourceError as exc:
            errors += 1
            event("warning", "match", f"{row['title']}: {exc}", row["key"])
            continue
        if proposal:
            found += 1
            if proposal["confidence"] == "high":
                high += 1
    jobs.progress(total, total, "Done")
    summary = {"checked": total, "found": found, "high": high, "errors": errors}
    event("info", "match", f"Matched {found} of {total} titles ({high} certain)")
    await notify.send("detect", "Match existing finished",
                      f"Checked {total} titles, found a set for {found} ({high} certain). "
                      "Confirm them under Match existing.")
    return summary


def confirm(review_id: int) -> bool:
    review = reviews.get(review_id)
    if not review or review["kind"] != "detect":
        return False
    data = review["data"]
    poster_set = PosterSet.from_dict(data["set"])
    matched = {slot: ImageRef.from_dict(image) for slot, image in data.get("matched", {}).items()}
    binding.bind_existing(review["item_key"], poster_set, matched)
    reviews.resolve(review_id)
    return True


def confirm_all(confidence: str = "high") -> int:
    count = 0
    for review in reviews.open_items("detect"):
        if review["data"].get("confidence") == confidence and confirm(review["id"]):
            count += 1
    return count
