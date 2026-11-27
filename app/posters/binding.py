"""Set bindings: a title follows one poster set; new seasons and set updates come from it."""
from __future__ import annotations

from . import apply, catalog, reviews
from .. import db, notify, settings
from .apply import Assignment
from .sources.base import ImageRef, PosterSet, SourceError, slot_id
from ..util import event, season_label


def get(item_key: str) -> dict | None:
    row = db.get().one("SELECT * FROM bindings WHERE item_key=?", (item_key,))
    if row:
        row["snapshot"] = db.loads(row["snapshot"], {})
    return row


def _alive_slots(item: dict) -> list[tuple[str, str, int | None]]:
    if item["type"] == "movie":
        return [(slot_id("movie", item["key"]), "movie", None)]
    slots = [(slot_id("show", item["key"]), "show", None)]
    for number in catalog.present_seasons(item["key"]):
        slots.append((slot_id("season", item["key"], number), "season", number))
    return slots


def followable(poster_set: PosterSet) -> bool:
    """Single TPDB posters outside a set have nothing to follow."""
    return not poster_set.set_key.startswith("poster:")


def _store(item: dict, poster_set: PosterSet, origin: str, snapshot: dict) -> None:
    if not followable(poster_set):
        return
    old = get(item["key"])
    if old and old["set_key"] == poster_set.set_key and old["source"] == poster_set.source:
        merged = {**old["snapshot"], **snapshot}
    else:
        merged = snapshot
    db.get().upsert(
        "bindings",
        ("item_key",),
        {
            "item_key": item["key"],
            "source": poster_set.source,
            "set_key": poster_set.set_key,
            "set_title": poster_set.title,
            "designer": poster_set.designer,
            "set_url": poster_set.url,
            "bound_at": old["bound_at"] if old and old["set_key"] == poster_set.set_key else db.now(),
            "bound_by": origin,
            "checked_at": db.now(),
            "set_updated": poster_set.updated,
            "snapshot": db.dumps(merged),
            "state": "ok",
            "note": None,
        },
    )


def _mark_waiting(item: dict, poster_set: PosterSet) -> list[int]:
    """Seasons Plex has but the set does not (yet): they stay pending."""
    if item["type"] != "show" or not followable(poster_set):
        return []
    database = db.get()
    waiting = []
    have = poster_set.seasons()
    for number in catalog.present_seasons(item["key"]):
        slot = slot_id("season", item["key"], number)
        row = database.one("SELECT * FROM slots WHERE id=?", (slot,))
        if not row:
            continue
        if number in have:
            continue
        if row["mode"] in ("manual", "extern") and row["file"]:
            continue
        if not row["waiting"]:
            database.update("slots", "id", slot, {"waiting": 1, "waiting_since": db.now(), "wait_checked_at": db.now(),
                                                  "wait_given_up": 0})
        waiting.append(number)
    return waiting


async def bind(item_key: str, poster_set: PosterSet, origin: str = "manual", replace_all: bool = True,
               only_slots: list[str] | None = None) -> dict:
    """Bind a title to a set and write all images the set has for it."""
    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    if not item:
        raise SourceError("Title not found")
    if poster_set.source == "tpdb" and not poster_set.complete:
        poster_set = await catalog.complete_tpdb(item, poster_set)
    assignments: list[Assignment] = []
    snapshot: dict = {}
    for slot, kind, season in _alive_slots(item):
        if only_slots is not None and slot not in only_slots:
            continue
        image = poster_set.image_for(kind, season)
        if image is None:
            continue
        row = database.one("SELECT * FROM slots WHERE id=?", (slot,))
        if not replace_all and row and row["file"] and row["mode"] in ("manual", "extern"):
            continue
        assignments.append(Assignment(slot, image))
        snapshot[slot] = image.version()
    mode = "set" if followable(poster_set) else "manual"
    outcomes = await apply.apply(item_key, assignments, origin, mode) if assignments else []
    ok = [o for o in outcomes if o.ok]
    snapshot = {slot: version for slot, version in snapshot.items() if any(o.slot_id == slot and o.ok for o in outcomes)}
    if ok or not assignments:
        _store(item, poster_set, origin, snapshot)
        waiting = _mark_waiting(item, poster_set)
        reviews.resolve_for(item_key, ("new_item", "new_season", "detect"))
    else:
        waiting = []
    if ok:
        event("info", "set", f"{item['title']}: linked to set \"{poster_set.title}\" by {poster_set.designer} "
                             f"({poster_set.source}), {len(ok)} poster(s) set", item_key)
    return {
        "outcomes": [o.public() for o in outcomes],
        "written": len(ok),
        "failed": len(outcomes) - len(ok),
        "waiting": waiting,
        "image": next((o.image for o in ok if o.image), None),
    }


def bind_existing(item_key: str, poster_set: PosterSet, matched: dict[str, ImageRef], origin: str = "detect") -> None:
    """Bind without writing: the files in the folder already are this set's images."""
    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    if not item:
        return
    snapshot = {}
    for slot, image in matched.items():
        snapshot[slot] = image.version()
        row = database.one("SELECT * FROM slots WHERE id=?", (slot,))
        values = {"mode": "set" if followable(poster_set) else "detected", "source": image.source,
                  "set_key": poster_set.set_key if followable(poster_set) else None, "image_id": image.image_id,
                  "image_modified": image.modified, "designer": image.designer or poster_set.designer,
                  "source_url": image.url}
        if row and row["file_sha1"]:
            values["written_sha1"] = row["file_sha1"]
        database.update("slots", "id", slot, values)
    _store(item, poster_set, origin, snapshot)
    waiting = _mark_waiting(item, poster_set)
    reviews.resolve_for(item_key, ("new_item", "new_season", "detect"))
    event("info", "set", f"{item['title']}: linked to set \"{poster_set.title}\" by {poster_set.designer} "
                         f"({len(matched)} file(s) matched{', ' + str(len(waiting)) + ' pending' if waiting else ''})",
          item_key)


def unbind(item_key: str) -> None:
    database = db.get()
    database.execute("DELETE FROM bindings WHERE item_key=?", (item_key,))
    database.execute(
        "UPDATE slots SET waiting=0, wait_given_up=0, mode=CASE WHEN mode='set' THEN 'manual' ELSE mode END "
        "WHERE item_key=?",
        (item_key,),
    )
    event("info", "set", "Unlinked from set", item_key)


async def handle_new_season(item_key: str, number: int) -> str:
    """A season appeared in Plex. Returns what happened (for logs and tests)."""
    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    if not item:
        return "missing"
    slot = slot_id("season", item_key, number)
    row = database.one("SELECT * FROM slots WHERE id=?", (slot,))
    if row and row["file"]:
        return "has_file"
    bound = get(item_key)
    label = f"{item['title']} – {season_label(number)}"
    if bound:
        try:
            poster_set = await catalog.load_set(bound["source"], bound["set_key"], item, fresh=True)
        except SourceError as exc:
            event("warning", "set", f"{label}: cannot load set ({exc}), retrying at the next set check", item_key)
            database.update("slots", "id", slot, {"waiting": 1, "waiting_since": db.now(), "wait_checked_at": db.now()})
            return "error"
        if poster_set is None:
            database.update("bindings", "item_key", item_key, {"state": "gone", "note": "Set no longer available"})
            reviews.add(item_key, "new_season", "Linked set is no longer available", season=number)
            await notify.send("review", f"Review: {label}", "The linked set is no longer available.",
                              item_key=item_key)
            return "set_gone"
        image = poster_set.image_for("season", number)
        if image:
            outcomes = await apply.apply(item_key, [Assignment(slot, image)], "season", "set")
            if outcomes and outcomes[0].ok:
                snapshot = bound["snapshot"]
                snapshot[slot] = image.version()
                database.update("bindings", "item_key", item_key, {"snapshot": db.dumps(snapshot),
                                                                   "checked_at": db.now()})
                event("info", "season", f"{label}: poster set from \"{bound['set_title']}\"", item_key)
                await notify.send("season", f"New season: {label}",
                                  f"Poster from the set \"{bound['set_title']}\" by {bound['designer']}.",
                                  image=outcomes[0].image, item_key=item_key)
                return "applied"
            return "failed"
        database.update("slots", "id", slot, {"waiting": 1, "waiting_since": db.now(), "wait_checked_at": db.now(),
                                              "wait_given_up": 0})
        event("info", "season", f"{label}: not in the set yet, pending", item_key)
        await notify.send("waiting", f"Pending: {label}",
                          f"The set \"{bound['set_title']}\" by {bound['designer']} has no poster for "
                          f"{season_label(number)} yet. Checked again with every set check.", item_key=item_key)
        return "waiting"
    # No binding: offer sets that have this season.
    candidates = []
    try:
        ranked, _ = await catalog.gather_ranked(item, complete_top=3)
        candidates = [r.public() for r in ranked if r.set.image_for("season", number)][:6]
    except SourceError:
        candidates = []
    reviews.add(item_key, "new_season", "New season, the show is not linked to a set", season=number,
                data={"candidates": candidates})
    await notify.send("review", f"Review: {label}", "New season, the show is not linked to a set.",
                      item_key=item_key)
    return "review"


async def check_binding(item_key: str) -> dict:
    """Look at a bound set again: fill pending seasons, report changed images."""
    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    bound = get(item_key)
    result = {"applied": 0, "changed": 0, "waiting": 0, "gave_up": 0}
    if not item or not bound or item.get("gone"):
        return result
    try:
        poster_set = await catalog.load_set(bound["source"], bound["set_key"], item, fresh=True)
    except SourceError as exc:
        database.update("bindings", "item_key", item_key, {"checked_at": db.now(), "note": str(exc)[:300]})
        return result
    if poster_set is None:
        if bound["state"] != "gone":
            database.update("bindings", "item_key", item_key, {"state": "gone", "note": "Set no longer available",
                                                               "checked_at": db.now()})
            await notify.send("error", f"Set gone: {item['title']}",
                              f"The linked set \"{bound['set_title']}\" is no longer available at {bound['source']}.",
                              item_key=item_key)
        return result
    snapshot = bound["snapshot"]
    notified = snapshot.get("_notified", {})
    to_apply: list[Assignment] = []
    changes = []
    max_wait = int(settings.get("wait_max_days") or 0) * 86400
    for slot, kind, season in _alive_slots(item):
        row = database.one("SELECT * FROM slots WHERE id=?", (slot,))
        if not row:
            continue
        image = poster_set.image_for(kind, season)
        if row["mode"] == "manual" or (row["mode"] == "extern" and row["file"]):
            continue
        if image is None:
            if row["waiting"]:
                result["waiting"] += 1
                if max_wait and row["waiting_since"] and row["waiting_since"] < db.now() - max_wait:
                    database.update("slots", "id", slot, {"waiting": 0, "wait_given_up": 1})
                    result["gave_up"] += 1
                    await notify.send("waiting", f"No longer pending: {item['title']}, {season_label(season)}",
                                      f"The set still has no poster after {settings.get('wait_max_days')} days. "
                                      "Select one manually.", item_key=item_key)
                else:
                    database.update("slots", "id", slot, {"wait_checked_at": db.now()})
            continue
        previous = snapshot.get(slot)
        if row["waiting"] or not row["file"]:
            to_apply.append(Assignment(slot, image))
        elif previous and previous != image.version() and row["set_key"] == poster_set.set_key:
            if settings.get("set_changes") == "apply":
                to_apply.append(Assignment(slot, image))
            elif notified.get(slot) != image.version():
                changes.append({"slot": slot, "season": season, "kind": kind, "new": image.to_dict(),
                                "old_image": row["image_id"]})
    if to_apply:
        outcomes = await apply.apply(item_key, to_apply, "set_update", "set")
        for outcome, assignment in zip(outcomes, to_apply):
            if outcome.ok:
                snapshot[assignment.slot_id] = assignment.ref.version()
                result["applied"] += 1
                if assignment.ref.kind == "season":
                    await notify.send("season", f"Poster set: {item['title']}, "
                                                f"{season_label(assignment.ref.season)}",
                                      f"The set \"{poster_set.title}\" now has a poster for it.",
                                      image=outcome.image, item_key=item_key)
    if changes:
        result["changed"] = len(changes)
        reviews.add(item_key, "set_changed", f"{len(changes)} poster(s) changed in the set \"{poster_set.title}\"",
                    data={"changes": changes, "set": {"source": poster_set.source, "set_key": poster_set.set_key,
                                                      "title": poster_set.title}})
        for change in changes:
            notified[change["slot"]] = ImageRef.from_dict(change["new"]).version()
        await notify.send("set_changed", f"Set changed: {item['title']}",
                          f"{len(changes)} poster(s) changed in the set \"{poster_set.title}\" by {poster_set.designer}. "
                          "Apply or keep them under Review.", item_key=item_key)
    snapshot["_notified"] = notified
    database.update("bindings", "item_key", item_key, {
        "snapshot": db.dumps(snapshot), "checked_at": db.now(), "set_updated": poster_set.updated,
        "set_title": poster_set.title, "state": "ok", "note": None,
    })
    return result


async def accept_changes(review_id: int, take: bool) -> dict:
    review = reviews.get(review_id)
    if not review or review["kind"] != "set_changed":
        raise SourceError("Entry not found")
    item_key = review["item_key"]
    bound = get(item_key)
    changes = review["data"].get("changes", [])
    result = {"written": 0}
    if take and changes:
        assignments = [Assignment(change["slot"], ImageRef.from_dict(change["new"])) for change in changes]
        outcomes = await apply.apply(item_key, assignments, "set_update", "set")
        result["written"] = sum(1 for o in outcomes if o.ok)
    if bound:
        snapshot = bound["snapshot"]
        for change in changes:
            snapshot[change["slot"]] = ImageRef.from_dict(change["new"]).version()
        db.get().update("bindings", "item_key", item_key, {"snapshot": db.dumps(snapshot)})
    reviews.resolve(review_id)
    return result
