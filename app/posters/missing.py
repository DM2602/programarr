"""Seasons TMDB or Sonarr know that Plex does not have yet, shown greyed out on the title page."""
from __future__ import annotations

from .. import arr, cache, db
from ..media import tmdb

SEASONS_HOURS = 12


async def _tmdb_seasons(item: dict) -> dict[int, dict]:
    if not tmdb.configured():
        return {}
    tmdb_id = item.get("tmdb_id")
    try:
        if not tmdb_id and item.get("tvdb_id"):
            found = [tid for kind, tid in await tmdb.find(str(item["tvdb_id"]), "tvdb_id") if kind == "show"]
            tmdb_id = found[0] if found else None
        if not tmdb_id:
            return {}
        key = f"tmdb:tvseasons:{tmdb_id}"
        rows = cache.get(key)
        if rows is None:
            data = await tmdb.details("show", int(tmdb_id)) or {}
            rows = [
                {"number": int(s["season_number"]), "episodes": s.get("episode_count"), "air_date": s.get("air_date"),
                 "poster": tmdb.poster_url(s.get("poster_path"))}
                for s in data.get("seasons") or [] if s.get("season_number") is not None
            ]
            cache.put(key, rows, SEASONS_HOURS * 3600)
    except tmdb.TmdbError:
        return {}
    return {row["number"]: dict(row) for row in rows}


async def seasons(item: dict) -> dict:
    """{"seasons": [...], "sonarr_url": ...}: seasons missing in Plex, with their state in Sonarr."""
    if item["type"] != "show":
        return {"seasons": [], "sonarr_url": None}
    have = {row["number"] for row in db.get().query(
        "SELECT number FROM seasons WHERE show_key=? AND gone=0", (item["key"],))}
    found = await _tmdb_seasons(item)
    sonarr_url = None
    series_state = None  # None: Sonarr not asked; "absent": Sonarr does not have the show
    if arr.configured("sonarr") and item.get("tvdb_id"):
        try:
            rows = await arr.get("sonarr", "/series", {"tvdbId": item["tvdb_id"]})
        except arr.ArrError:
            rows = None
        series = next((row for row in rows or [] if isinstance(row, dict)), None) if isinstance(rows, list) else None
        if rows is not None and series is None:
            series_state = "absent"
        if series:
            if series.get("titleSlug"):
                sonarr_url = f"{arr.link_base('sonarr')}/series/{series['titleSlug']}"
            for season in series.get("seasons") or []:
                number = season.get("seasonNumber")
                if number is None:
                    continue
                stats = season.get("statistics") or {}
                entry = found.setdefault(int(number), {"number": int(number), "episodes": None, "air_date": None,
                                                       "poster": None})
                entry["sonarr"] = "monitored" if season.get("monitored") else "unmonitored"
                entry["files"] = stats.get("episodeFileCount")
                entry["episodes"] = entry.get("episodes") or stats.get("totalEpisodeCount") or None
    result = []
    for number in sorted(found):
        entry = found[number]
        if number in have:
            continue
        # Specials only when Sonarr is asked to get them; TMDB lists them for almost every show.
        if number == 0 and entry.get("sonarr") != "monitored":
            continue
        entry.setdefault("sonarr", series_state)
        entry.setdefault("files", None)
        result.append(entry)
    return {"seasons": result, "sonarr_url": sonarr_url}
