"""New titles: take a set automatically when it is safe, otherwise ask under Review."""
from __future__ import annotations

from . import backup, binding, catalog, detect, reviews
from .. import db, notify, settings
from .sources.base import SOURCE_NAMES, SourceError
from ..util import event, season_label


def _sibling_set(item: dict, ranked: list[catalog.Ranked]) -> catalog.Ranked | None:
    """For a film in a collection: the set the other films of the collection use."""
    if item["type"] != "movie" or not item.get("collection_id"):
        return None
    rows = db.get().query(
        "SELECT b.set_key, b.source FROM bindings b JOIN items i ON i.key=b.item_key "
        "WHERE i.collection_id=? AND i.key<>? AND i.gone=0",
        (item["collection_id"], item["key"]),
    )
    keys = [(row["source"], row["set_key"]) for row in rows]
    for source, set_key in keys:
        for entry in ranked:
            if entry.set.source == source and entry.set.set_key == set_key and entry.has_main and not entry.hidden:
                return entry
    return None


def choose(item: dict, ranked: list[catalog.Ranked], tpdb_sure: bool) -> tuple[catalog.Ranked | None, str]:
    """The set to take automatically, or None with the reason why not."""
    if settings.get("auto_collection_consistency"):
        sibling = _sibling_set(item, ranked)
        if sibling:
            return sibling, "same set as the other movies of the collection"
    if not ranked:
        return None, "No sets found"
    preferred = [r for r in ranked if r.rank is not None and not r.hidden and r.has_main]
    if not preferred:
        return None, "No set from a preferred designer"
    require_complete = bool(settings.get("auto_require_complete")) and item["type"] == "show"
    for entry in preferred:
        if entry.set.source == "tpdb" and not tpdb_sure:
            continue
        if require_complete and not entry.complete:
            continue
        return entry, f"preferred designer {entry.set.designer}"
    first = preferred[0]
    if first.set.source == "tpdb" and not tpdb_sure:
        return None, "ThePosterDB match is not certain (title or year differ)"
    if require_complete and not first.complete:
        missing = ", ".join(season_label(n) for n in first.missing) or "show poster"
        return None, f"Set by {first.set.designer} is incomplete (missing: {missing})"
    return None, "No matching set"


async def handle_new_item(item_key: str) -> str:
    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    if not item or item.get("gone"):
        return "missing"
    if binding.get(item_key):
        return "bound"
    # A title Plex added again (deleted and downloaded anew) gets its posters back from the backup.
    if await backup.restore(item_key):
        try:
            await detect.relink(item_key)
        except SourceError:
            pass
        return "restored"
    try:
        ranked, found = await catalog.gather_ranked(item, complete_top=2)
    except SourceError as exc:
        reviews.add(item_key, "new_item", f"Sources not reachable: {exc}", data={"candidates": []})
        return "error"
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,)) or item
    candidates = [entry.public() for entry in ranked[:8]]
    if not settings.get("auto_new"):
        reason = "Automation is off"
        choice = None
    else:
        choice, reason = choose(item, ranked, found.tpdb_sure)
    if choice:
        result = await binding.bind(item_key, choice.set, origin="auto", replace_all=False)
        if result["written"]:
            waiting = result["waiting"]
            extra = f" {len(waiting)} season(s) waiting for the set." if waiting else ""
            event("info", "auto", f"{item['title']}: took the set by {choice.set.designer} automatically ({reason})",
                  item_key)
            await notify.send(
                "auto",
                f"New: {catalog.title_of(item)}",
                f"Posters from the {SOURCE_NAMES.get(choice.set.source, choice.set.source)} set \"{choice.set.title}\" by "
                f"{choice.set.designer} ({reason}).{extra}",
                image=result["image"],
                item_key=item_key,
            )
            return "applied"
        reason = "Automatic apply failed: " + "; ".join(
            o["message"] for o in result["outcomes"] if not o["ok"]
        )[:300]
    errors = found.errors
    if errors and not ranked:
        reason += " – " + "; ".join(f"{name}: {message}" for name, message in errors.items())
    reviews.add(item_key, "new_item", reason, data={"candidates": candidates, "errors": errors})
    event("info", "auto", f"{item['title']}: {reason}, added to Review", item_key)
    await notify.send("review", f"Review: {catalog.title_of(item)}", reason, item_key=item_key)
    return "review"
