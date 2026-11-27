"""View data for the poster pages: labels, cards, slots and review choices as plain dicts."""
from __future__ import annotations

import time
from pathlib import Path

from . import binding, catalog, library, refsign, reviews, status
from .. import db, settings
from .sources.base import SOURCE_NAMES, ImageRef, PosterSet, parse_slot_id, slot_id
from ..util import season_label

PLEX_STATES = {
    "ok": ("ok", "Plex shows the file"),
    "pending": ("warn", "Plex hasn't picked up the file yet"),
    "not_local": ("bad", "Plex shows a different poster"),
    "no_file": ("none", "Plex shows its own poster"),
    "unknown": ("none", "Not checked yet"),
    None: ("none", "Not checked yet"),
}

MODES = {
    "set": "linked set",
    "manual": "manual",
    "extern": "file in folder",
    "detected": "matched",
}

ORIGINS = {
    "manual": "Manual",
    "auto": "New title",
    "season": "New season",
    "set_update": "Set update",
    "restore": "Restored",
    "detect": "Matched",
    "adopt": "Imported from Plex",
    "plex": "Selected in Plex",
    "plex_file": "Plex poster kept",
    "backup": "Restored from backup",
    "system": "Programarr",
}

ACTIONS = {
    "write": "Poster set",
    "remove": "File removed",
    "unlock": "Unlocked in Plex",
}

# Library filters: key -> (label, SQL condition on the LIBRARY_SQL aliases).
STATUS_FILTERS = {
    "all": ("All", ""),
    "linked": ("Linked", " AND b.item_key IS NOT NULL"),
    "unlinked": ("Not linked", " AND b.item_key IS NULL AND NOT " + status.DONE_SQL),
    "pending": ("Pending", " AND EXISTS (SELECT 1 FROM slots w WHERE w.item_key=i.key AND w.waiting=1)"),
    "review": ("Review", " AND EXISTS (SELECT 1 FROM reviews r WHERE r.item_key=i.key AND r.status='open')"),
    "problem": ("Problem", " AND EXISTS (SELECT 1 FROM slots p WHERE p.item_key=i.key AND p.plex_state='not_local')"),
    "no_file": ("No file", " AND (ms.file IS NULL)"),
    "complete": ("Complete", " AND " + status.DONE_SQL),
}

LIBRARY_SQL = """
SELECT i.key, i.title, i.year, i.type, i.library_key, i.sort_title, i.search_title, i.original_title,
       i.is_new, i.first_seen, i.added_at, i.show_status,
       b.source AS b_source, b.designer AS b_designer, b.set_title AS b_title,
       ms.file_sha1 AS main_sha, ms.file AS main_file, ms.plex_state AS main_state,
       (SELECT COUNT(*) FROM slots w WHERE w.item_key=i.key AND w.waiting=1) AS waiting,
       (SELECT COUNT(*) FROM slots p WHERE p.item_key=i.key AND p.plex_state='not_local') AS problems,
       (SELECT COUNT(*) FROM reviews r WHERE r.item_key=i.key AND r.status='open') AS open_reviews,
       (SELECT COUNT(*) FROM slots f WHERE f.item_key=i.key AND f.file IS NOT NULL) AS files,
       """ + status.DONE_SQL + """ AS done
FROM items i
LEFT JOIN bindings b ON b.item_key=i.key
LEFT JOIN slots ms ON ms.id = (CASE WHEN i.type='movie' THEN 'm:' ELSE 's:' END) || i.key
WHERE i.gone=0
"""

NEW_FOR = 7 * 86400  # a title counts as new for a week after the sync found it


def source_name(source: str | None) -> str:
    return SOURCE_NAMES.get(source or "", source or "")


def main_slot(item_key: str, item_type: str | None) -> str:
    return f"m:{item_key}" if item_type == "movie" else f"s:{item_key}"


def slot_thumb(slot_id: str, version: str | None) -> str:
    return f"/img/slot/{slot_id}?v={(version or 'x')[:12]}"


def counts() -> dict:
    database = db.get()
    open_reviews = reviews.count_open()
    return {
        "review": sum(open_reviews.values()),
        "review_kinds": open_reviews,
        "pending": database.scalar(
            "SELECT COUNT(*) FROM slots s JOIN items i ON i.key=s.item_key WHERE s.waiting=1 AND i.gone=0", (), 0),
        "movies": database.scalar("SELECT COUNT(*) FROM items WHERE type='movie' AND gone=0", (), 0),
        "shows": database.scalar("SELECT COUNT(*) FROM items WHERE type='show' AND gone=0", (), 0),
        "linked": database.scalar(
            "SELECT COUNT(*) FROM bindings b JOIN items i ON i.key=b.item_key WHERE i.gone=0", (), 0),
        "jobs": database.scalar("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')", (), 0),
    }


def problems() -> int:
    return db.get().scalar(
        "SELECT COUNT(*) FROM slots s JOIN items i ON i.key=s.item_key WHERE s.plex_state='not_local' AND i.gone=0",
        (), 0)


def unlinked_with_files(item_type: str = "show") -> int:
    """Titles with poster files but no set: candidates for Match existing."""
    done = f"AND NOT {status.DONE_SQL}" if item_type == "show" else ""
    return db.get().scalar(
        "SELECT COUNT(DISTINCT i.key) FROM items i JOIN slots s ON s.item_key=i.key "
        "LEFT JOIN bindings b ON b.item_key=i.key WHERE i.gone=0 AND s.file IS NOT NULL AND b.item_key IS NULL "
        f"AND i.type=? {done}", (item_type,), 0)


def complete_count() -> int:
    return db.get().scalar(f"SELECT COUNT(*) FROM items i WHERE i.gone=0 AND {status.DONE_SQL}", (), 0)


def slot_label(slot: dict) -> str:
    if slot["kind"] == "season":
        return season_label(slot["season"])
    return "Poster" if slot["kind"] == "movie" else "Show"


def slot_title(item: dict, slot_id: str) -> str:
    kind, _, season = parse_slot_id(slot_id)
    return f"{item['title']}, {season_label(season)}" if kind == "season" else item["title"]


def image_caption(image: dict) -> str:
    if image.get("kind") == "season":
        number = image.get("season")
        return "Specials" if number == 0 else f"S{number}"
    return "Show" if image.get("kind") == "show" else "Movie"


def slot_view(slot: dict, show_folder: Path | None = None) -> dict:
    state, state_text = PLEX_STATES.get(slot.get("plex_state"), PLEX_STATES[None])
    fresh = (slot.get("applied_at") or 0) > time.time() - 600
    if slot.get("plex_state") == "pending" and fresh:
        state, state_text = "none", "Plex is reading the file"
    if not slot.get("file"):
        state, state_text = PLEX_STATES["no_file"]
    in_subfolder = bool(slot.get("file") and show_folder and Path(slot["file"]).parent != show_folder)
    return {
        "id": slot["id"],
        "kind": slot["kind"],
        "season": slot.get("season"),
        "label": slot_label(slot),
        "file": slot.get("file"),
        "file_name": Path(slot["file"]).name if slot.get("file") else None,
        "folder_name": Path(slot["file"]).parent.name if in_subfolder else None,
        "thumb": slot_thumb(slot["id"], slot.get("file_sha1") or slot.get("plex_state")),
        "source": slot.get("source"),
        "source_label": source_name(slot.get("source")) if slot.get("source") else None,
        "designer": slot.get("designer"),
        "mode": slot.get("mode"),
        "mode_label": MODES.get(slot.get("mode") or "", ""),
        "plex_state": slot.get("plex_state"),
        "state": state,
        "state_text": state_text,
        "needs_force": bool(slot.get("file")) and (slot.get("plex_state") == "not_local"
                                                   or (slot.get("plex_state") == "pending" and not fresh)),
        "applied_at": slot.get("applied_at"),
        "pending": bool(slot.get("waiting")),
        "pending_since": slot.get("waiting_since"),
        "dismissed": bool(slot.get("wait_given_up")),
        "gone": False,
    }


def item_slots(item: dict) -> list[dict]:
    database = db.get()
    rows = database.query("SELECT * FROM slots WHERE item_key=?", (item["key"],))
    alive = {row["number"] for row in database.query(
        "SELECT number FROM seasons WHERE show_key=? AND gone=0", (item["key"],))}
    show_folder = None
    if item["type"] == "show" and item.get("folder"):
        show_folder = library.mapper().to_local(item["folder"])
    result = []
    for row in rows:
        gone = row["kind"] == "season" and row["season"] not in alive
        if gone and not row["file"]:
            continue
        view = slot_view(row, show_folder)
        view["gone"] = gone
        result.append(view)
    result.sort(key=lambda s: (s["kind"] == "season", s["season"] if s["season"] is not None else -1))
    return result


def external_links(item: dict) -> list[dict]:
    links = []
    kind = "movie" if item["type"] == "movie" else "tv"
    if item.get("tmdb_id"):
        links.append({"label": "TMDB", "url": f"https://www.themoviedb.org/{kind}/{item['tmdb_id']}"})
        site = settings.get("mediux_site").rstrip("/")
        links.append({"label": "MediUX", "url": f"{site}/{'movies' if kind == 'movie' else 'shows'}/{item['tmdb_id']}"})
    if item.get("tpdb_url"):
        links.append({"label": "ThePosterDB", "url": item["tpdb_url"]})
    if item.get("imdb_id"):
        links.append({"label": "IMDb", "url": f"https://www.imdb.com/title/{item['imdb_id']}/"})
    if item.get("tvdb_id") and item["type"] == "show":
        links.append({"label": "TheTVDB", "url": f"https://thetvdb.com/dereferrer/series/{item['tvdb_id']}"})
    return links


def card_view(row: dict, now: int | None = None) -> dict:
    """Library grid card: poster of the main slot plus small status hints."""
    now = now or int(time.time())
    if row.get("problems"):
        flag = "problem"
    elif row.get("open_reviews"):
        flag = "review"
    elif row.get("waiting"):
        flag = "pending"
    elif row.get("is_new") and row.get("first_seen") and row["first_seen"] > now - NEW_FOR:
        flag = "new"
    else:
        flag = None
    return {
        "key": row["key"],
        "title": row["title"],
        "year": row.get("year"),
        "type": row["type"],
        "library_key": row.get("library_key"),
        "thumb": slot_thumb(main_slot(row["key"], row["type"]), row.get("main_sha")),
        "has_file": bool(row.get("main_file")),
        "flag": flag,
        "complete": bool(row.get("done")),
        "designer": row.get("b_designer"),
        "source_label": source_name(row["b_source"]) if row.get("b_source") else None,
        "files": row.get("files") or 0,
    }


def binding_view(item_key: str) -> dict | None:
    bound = binding.get(item_key)
    if not bound:
        return None
    return {
        "source": bound["source"],
        "source_label": source_name(bound["source"]),
        "set_key": bound["set_key"],
        "set_title": bound.get("set_title") or "",
        "designer": bound.get("designer") or "",
        "set_url": bound.get("set_url"),
        "bound_at": bound.get("bound_at"),
        "checked_at": bound.get("checked_at"),
        "state": bound.get("state") or "ok",
        "note": bound.get("note"),
    }


def history_view(row: dict, item: dict | None = None) -> dict:
    item = item or row
    if item.get("title") and row.get("slot_id"):
        title = slot_title(item, row["slot_id"])
    else:
        title = item.get("title") or "Unknown title"
    return {
        "id": row["id"],
        "at": row["at"],
        "item_key": row.get("item_key"),
        "slot_id": row.get("slot_id"),
        "title": title,
        "action": row["action"],
        "action_label": ACTIONS.get(row["action"], row["action"]),
        "origin": row.get("origin"),
        "origin_label": ORIGINS.get(row.get("origin") or "", row.get("origin") or ""),
        "source_label": source_name(row["source"]) if row.get("source") else None,
        "designer": row.get("designer"),
        "note": row.get("note"),
        "archived": f"/img/archive/{row['id']}" if row.get("archived") else None,
    }


def recent_cards(limit: int = 14) -> list[dict]:
    """Recently written posters, one card per title and batch (a set writes many files at once)."""
    database = db.get()
    rows = database.query(
        "SELECT h.*, i.title, i.type FROM history h JOIN items i ON i.key=h.item_key "
        # Plex posters kept as files and copies from the backup are not new posters.
        "WHERE h.action='write' AND COALESCE(h.origin, '') NOT IN ('plex_file', 'backup') ORDER BY h.id DESC LIMIT 200")
    groups: list[dict] = []
    for row in rows:
        last = groups[-1] if groups else None
        if last and last["item_key"] == row["item_key"] and last["origin"] == row["origin"] \
                and abs(last["first_at"] - row["at"]) <= 180:
            last["slots"].append(row["slot_id"])
            last["first_at"] = row["at"]
            if not row["slot_id"].startswith("S:"):
                last["main"] = row
            continue
        if len(groups) >= limit:
            break
        groups.append({**row, "slots": [row["slot_id"]], "first_at": row["at"],
                       "main": row if not row["slot_id"].startswith("S:") else None})
    cards = []
    for group in groups:
        shown = group["main"] or group
        seasons = sum(1 for slot in group["slots"] if slot.startswith("S:"))
        if group["main"] and seasons:
            title, what = group["title"], f"Show and {seasons} season{'s' if seasons != 1 else ''}"
        elif seasons > 1:
            title, what = group["title"], f"{seasons} seasons"
        else:
            title, what = slot_title(shown, shown["slot_id"]), None
        sha = database.scalar("SELECT file_sha1 FROM slots WHERE id=?", (shown["slot_id"],), "x")
        cards.append({
            "item_key": group["item_key"],
            "title": title,
            "what": what,
            "type": group["type"],
            "source_label": source_name(shown["source"]) if shown.get("source") else None,
            "designer": shown.get("designer"),
            "origin_label": ORIGINS.get(group["origin"] or "", group["origin"] or ""),
            "at": group["at"],
            "thumb": slot_thumb(shown["slot_id"], sha),
        })
    return cards


def _set_view(poster_set: PosterSet, **extra) -> dict:
    return {
        "token": refsign.sign_set(poster_set),
        "source": poster_set.source,
        "source_label": source_name(poster_set.source),
        "set_key": poster_set.set_key,
        "title": poster_set.title,
        "designer": poster_set.designer,
        "url": poster_set.url or None,
        "loaded": poster_set.complete,
        "images": [refsign.image_dict(img) for img in poster_set.images],
        **extra,
    }


def set_view(entry: catalog.Ranked, bound: dict | None = None) -> dict:
    """One set in the picker, with coverage of the title's seasons."""
    poster_set = entry.set
    return _set_view(
        poster_set,
        rank=entry.rank,
        covers=entry.covers,
        missing=entry.missing,
        has_main=entry.has_main,
        complete=entry.complete,
        hidden=entry.hidden,
        bound=bool(bound and bound["set_key"] == poster_set.set_key and bound["source"] == poster_set.source),
    )


def _pair_order(slot: str) -> tuple:
    return (slot.startswith("S:"), int(slot.split(":")[2]) if slot.startswith("S:") else 0)


def current_posters(review: dict, seasons: list[int]) -> list[dict]:
    """What the slots under Review show now (the file in the folder, else Plex' poster), to keep."""
    item_key, kind = review["item_key"], review["kind"]
    if review.get("type") == "movie":
        wanted = [slot_id("movie", item_key)]
    elif kind == "new_season" and review.get("season") is not None:
        wanted = [slot_id("season", item_key, review["season"])]
    else:
        wanted = [slot_id("show", item_key)] + [slot_id("season", item_key, number) for number in seasons]
    marks = ",".join("?" for _ in wanted)
    rows = {row["id"]: row for row in db.get().query(f"SELECT * FROM slots WHERE id IN ({marks})", tuple(wanted))}
    posters = []
    for slot in wanted:
        row = rows.get(slot)
        if not row:
            continue
        thumb = f"/img/slot/{slot}?v=r{review['id']}{(row['file_sha1'] or '')[:8]}"
        label = {"movie": "Poster", "show": "Show"}.get(row["kind"]) or (
            "Specials" if row["season"] == 0 else f"S{row['season']}")
        posters.append({"slot": slot, "label": label, "thumb": thumb, "large": f"{thumb}&size=large", "file": bool(row["file"])})
    return posters


def review_view(review: dict) -> dict:
    """One Review entry with its choices; image and set tokens are signed here."""
    data = review.get("data") or {}
    kind = review["kind"]
    view = {
        "id": review["id"],
        "kind": kind,
        "kind_label": reviews.KINDS.get(kind, kind),
        "item_key": review["item_key"],
        "title": review.get("title") or "Unknown title",
        "year": review.get("year"),
        "type": review.get("type"),
        "season": review.get("season"),
        "reason": review.get("reason") or "",
        "created_at": review.get("created_at"),
        "thumb": f"/img/slot/{main_slot(review['item_key'], review.get('type'))}?v=r{review['id']}",
        "candidates": [],
        "seasons": [],
        "errors": {},
        "pairs": [],
        "changes": [],
        "set": None,
        "confidence": None,
        "via": None,
        "slot": None,
    }
    if kind in ("new_item", "new_season"):
        seasons = catalog.present_seasons(review["item_key"]) if review.get("type") == "show" else []
        view["seasons"] = seasons
        view["current"] = current_posters(review, seasons)
        view["errors"] = {source_name(name): message for name, message in (data.get("errors") or {}).items()}
        for candidate in data.get("candidates", [])[:5]:
            poster_set = PosterSet.from_dict(candidate["set"])
            images = poster_set.images
            if kind == "new_season" and review.get("season") is not None:
                images = sorted(images, key=lambda i: (i.kind != "season" or i.season != review["season"]))
            shown = PosterSet(**{**poster_set.__dict__, "images": images[:12]})
            view["candidates"].append(_set_view(
                shown,
                token=refsign.sign_set(poster_set),
                rank=candidate.get("rank"),
                covers=candidate.get("covers") or [],
                missing=candidate.get("missing") or [],
                has_main=candidate.get("has_main", True),
                complete=bool(candidate.get("complete")),
                hidden=bool(candidate.get("hidden")),
                bound=False,
                more=max(0, len(images) - 12),
            ))
    elif kind == "detect":
        poster_set = PosterSet.from_dict(data["set"])
        view["set"] = {"title": poster_set.title, "designer": poster_set.designer,
                       "source_label": source_name(poster_set.source), "url": poster_set.url or None}
        for slot, image in sorted(data.get("matched", {}).items(), key=lambda kv: _pair_order(kv[0])):
            ref = ImageRef.from_dict(image)
            view["pairs"].append({"slot": slot, "label": image_caption(image),
                                  "current": f"/img/slot/{slot}?v=d{review['id']}",
                                  "other": f"/img/ref/{refsign.sign_ref(ref)}"})
        view["confidence"] = data.get("confidence")
        view["via"] = data.get("via")
    elif kind == "set_changed":
        for change in data.get("changes", []):
            ref = ImageRef.from_dict(change["new"])
            view["changes"].append({"slot": change["slot"], "label": image_caption(change["new"]),
                                    "current": f"/img/slot/{change['slot']}?v=c{review['id']}",
                                    "other": f"/img/ref/{refsign.sign_ref(ref)}"})
        changed = data.get("set") or {}
        if changed:
            view["set"] = {"title": changed.get("title") or "", "designer": None,
                           "source_label": source_name(changed.get("source")), "url": None}
    elif kind == "plex":
        view["slot"] = data.get("slot")
    return view


def setup_steps() -> dict:
    libs = library.libraries()
    return {
        "plex": bool(settings.get("plex_url") and settings.get("plex_token")),
        "libraries": any(lib["enabled"] for lib in libs),
        "sources": bool(settings.get("mediux_token") or settings.get("tpdb_email")),
        "designers": bool(settings.get("designers")) or bool(settings.get("use_mediux_follows")
                                                           and settings.get("mediux_token")),
    }
