"""Finding poster sets for a title and ranking them by your preferences."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .. import db, settings
from .sources import mediux, tmdb, tpdb
from .sources.base import PosterSet, SourceError
from ..util import event, split_title_year


# -- ids and titles ------------------------------------------------------------
async def ensure_ids(item: dict) -> dict:
    """Fill in the TMDB id of shows that Plex only knows by TVDB id."""
    if item.get("tmdb_id") or item["type"] != "show" or not item.get("tvdb_id"):
        return item
    tmdb_id = None
    if mediux.configured():
        try:
            tmdb_id = await mediux.tmdb_for_tvdb(item["tvdb_id"])
        except SourceError:
            tmdb_id = None
    if not tmdb_id and tmdb.configured():
        try:
            tmdb_id = await tmdb.resolve_id(item)
        except SourceError:
            tmdb_id = None
    if tmdb_id:
        db.get().update("items", "key", item["key"], {"tmdb_id": tmdb_id})
        item = {**item, "tmdb_id": tmdb_id}
    return item


def _store_search_info(item: dict, title: str | None, date: str | None) -> dict:
    if not title:
        return item
    year = int(date[:4]) if date and str(date)[:4].isdigit() else item.get("year")
    db.get().update("items", "key", item["key"], {"search_title": title, "search_year": year})
    return {**item, "search_title": title, "search_year": year}


async def search_info(item: dict) -> dict:
    """English title and year for ThePosterDB (Plex titles may be German)."""
    if item.get("search_title"):
        return item
    if tmdb.configured():
        try:
            title, year = await tmdb.english_title(item)
            if title:
                return _store_search_info(item, title, str(year) if year else None)
        except SourceError:
            pass
    return item


# -- gathering -----------------------------------------------------------------
@dataclass
class Found:
    mediux: list[PosterSet] = field(default_factory=list)
    tpdb: list[PosterSet] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    tpdb_url: str | None = None
    tpdb_sure: bool = False

    def all(self) -> list[PosterSet]:
        return [*self.mediux, *self.tpdb]


async def mediux_sets(item: dict, fresh: bool = False) -> list[PosterSet]:
    item = await ensure_ids(item)
    if not item.get("tmdb_id"):
        raise SourceError("Plex has no TMDB id for this title")
    if item["type"] == "show":
        info, sets = await mediux.show_sets(item["tmdb_id"], fresh)
    else:
        info, sets = await mediux.movie_sets(item["tmdb_id"], fresh)
        if info.get("collection_id"):
            db.get().update("items", "key", item["key"], {"collection_id": info["collection_id"],
                                                         "collection_name": info.get("collection")})
    if not item.get("search_title"):
        _store_search_info(item, info.get("title"), info.get("date"))
    return sets


async def tpdb_sets(item: dict, fresh: bool = False) -> tuple[list[PosterSet], str | None, bool]:
    item = await search_info(item)
    page_url = item.get("tpdb_url")
    sure = bool(item.get("tpdb_sure")) if page_url else False
    if not page_url:
        title = item.get("search_title") or item["title"]
        year = item.get("search_year") or item.get("year")
        page_url, sure = await tpdb.find_item_page(title, year, item["type"])
        if not page_url and item.get("search_title") and item["title"] != item["search_title"]:
            page_url, sure = await tpdb.find_item_page(item["title"], item.get("year"), item["type"])
        if page_url:
            db.get().update("items", "key", item["key"], {"tpdb_url": page_url, "tpdb_sure": int(sure)})
    if not page_url:
        return [], None, False
    sets = await tpdb.sets_for_item(item, page_url)
    return sets, page_url, sure


async def complete_tpdb(item: dict, poster_set: PosterSet, fresh: bool = False) -> PosterSet:
    """Load the full TPDB set page (season posters are only listed there)."""
    if poster_set.source != "tpdb" or poster_set.complete:
        return poster_set
    full = await tpdb.get_set(poster_set.set_key, item, fresh)
    if full is None or not full.images:
        return poster_set
    return full


async def find_sets(item: dict, fresh: bool = False, sources: tuple[str, ...] = ("mediux", "tpdb")) -> Found:
    found = Found()
    # Fresh id/title info first, both sources use it.
    item = db.get().one("SELECT * FROM items WHERE key=?", (item["key"],)) or item

    async def run_mediux():
        try:
            found.mediux = await mediux_sets(item, fresh)
        except SourceError as exc:
            found.errors["mediux"] = str(exc)

    if "mediux" in sources and mediux.configured():
        await run_mediux()
        item = db.get().one("SELECT * FROM items WHERE key=?", (item["key"],)) or item
    if "tpdb" in sources:
        try:
            found.tpdb, found.tpdb_url, found.tpdb_sure = await tpdb_sets(item, fresh)
        except SourceError as exc:
            found.errors["tpdb"] = str(exc)
    return found


async def load_set(source: str, set_key: str, item: dict, fresh: bool = True) -> PosterSet | None:
    if source == "mediux":
        return await mediux.get_set(set_key, item.get("tmdb_id"))
    if source == "tpdb":
        return await tpdb.get_set(set_key, item, fresh)
    raise SourceError(f"Unknown source: {source}")


# -- preferences ---------------------------------------------------------------
async def refresh_follows(force: bool = False) -> dict:
    stored = settings.get("mediux_follows") or {}
    if not settings.get("use_mediux_follows") or not mediux.configured():
        return stored
    if not force and stored.get("at", 0) > db.now() - 12 * 3600:
        return stored
    try:
        data = await mediux.follows()
    except SourceError as exc:
        event("warning", "mediux", f"Cannot load followed designers: {exc}")
        return stored
    data["at"] = db.now()
    settings.set("mediux_follows", data)
    return data


def designer_rank(source: str, designer: str | None) -> int | None:
    """Position in your designer list (lower is better); None = not preferred."""
    if not designer:
        return None
    name = designer.casefold()
    explicit = settings.get("designers") or []
    for index, entry in enumerate(explicit):
        if (entry.get("name") or "").strip().casefold() == name and entry.get("source", "any") in ("any", source):
            return index
    if source == "mediux" and settings.get("use_mediux_follows"):
        follows = [n.casefold() for n in (settings.get("mediux_follows") or {}).get("follow", [])]
        if name in follows:
            return len(explicit) + follows.index(name)
    return None


def hidden(source: str, designer: str | None) -> bool:
    if source != "mediux" or not designer:
        return False
    return designer.casefold() in [n.casefold() for n in (settings.get("mediux_follows") or {}).get("hide", [])]


@dataclass
class Ranked:
    set: PosterSet
    rank: int | None
    covers: list[int]
    missing: list[int]
    has_main: bool
    complete: bool
    hidden: bool

    def public(self) -> dict:
        return {
            "set": self.set.to_dict(),
            "rank": self.rank,
            "covers": self.covers,
            "missing": self.missing,
            "has_main": self.has_main,
            "complete": self.complete,
            "hidden": self.hidden,
        }


def rank_sets(item: dict, sets: list[PosterSet], seasons: list[int]) -> list[Ranked]:
    count_specials = bool(settings.get("auto_count_specials"))
    wanted = [n for n in seasons if n > 0 or count_specials]
    order = settings.get("source_order") or ["mediux", "tpdb"]
    ranked = []
    for poster_set in sets:
        if item["type"] == "movie":
            has_main = poster_set.image_for("movie") is not None
            covers, missing = [], []
        else:
            has_main = poster_set.has_show_poster()
            have = poster_set.seasons()
            covers = [n for n in seasons if n in have]
            missing = [n for n in wanted if n not in have]
        ranked.append(
            Ranked(
                set=poster_set,
                rank=designer_rank(poster_set.source, poster_set.designer),
                covers=covers,
                missing=missing,
                has_main=has_main,
                complete=has_main and not missing,
                hidden=hidden(poster_set.source, poster_set.designer),
            )
        )

    def key(entry: Ranked):
        return (
            entry.hidden,
            entry.rank is None,
            entry.rank if entry.rank is not None else 0,
            not entry.complete,
            -len(entry.covers),
            order.index(entry.set.source) if entry.set.source in order else 9,
            -(entry.set.popularity or 0),
        )

    return sorted(ranked, key=key)


def present_seasons(item_key: str) -> list[int]:
    rows = db.get().query("SELECT DISTINCT number FROM seasons WHERE show_key=? AND gone=0 ORDER BY number",
                          (item_key,))
    return [row["number"] for row in rows]


async def gather_ranked(item: dict, fresh: bool = False, complete_preferred: bool = True,
                        complete_top: int = 0) -> tuple[list[Ranked], Found]:
    """Sets for a title, best first. TPDB sets of preferred designers are loaded in full."""
    await refresh_follows()
    found = await find_sets(item, fresh)
    seasons = present_seasons(item["key"]) if item["type"] == "show" else []
    sets = found.all()
    if item["type"] == "show":
        to_complete = []
        for index, poster_set in enumerate(found.tpdb):
            preferred = designer_rank("tpdb", poster_set.designer) is not None
            if not poster_set.complete and ((complete_preferred and preferred) or index < complete_top):
                to_complete.append(index)
        for index in to_complete:
            try:
                found.tpdb[index] = await complete_tpdb(item, found.tpdb[index])
            except SourceError as exc:
                found.errors["tpdb"] = str(exc)
                break
        sets = found.all()
    return rank_sets(item, sets, seasons), found


def title_of(item: dict) -> str:
    title, _ = split_title_year(item.get("title") or "")
    return f"{title} ({item['year']})" if item.get("year") else title


async def gather_many(items: list[dict], limit: int = 3):
    semaphore = asyncio.Semaphore(limit)

    async def one(item):
        async with semaphore:
            return await gather_ranked(item)

    return await asyncio.gather(*(one(item) for item in items), return_exceptions=True)
