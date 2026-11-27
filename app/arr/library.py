"""What flows need from Radarr and Sonarr: profiles, root folders, tags and exclusions, adding titles,
and TVDB IDs that TMDB does not know.

Titles that already exist stay as they are: profile, folder, monitoring and tags. Tags only go on
with a new title.
"""
from __future__ import annotations

import re
import time

from .. import cache
from .client import ArrError, get, label, request

OPTIONS_SECONDS = 60
PROFILE_NAMES_SECONDS = 365 * 86400  # names for summaries, renewed whenever the options are read
TAG = re.compile(r"^[a-z0-9-]+$")
_options: dict[str, tuple[float, dict]] = {}


def reset() -> None:
    _options.clear()


def clean_tag(value: str) -> str:
    """Radarr and Sonarr keep tags lowercase with a-z, 0-9 and dashes."""
    return re.sub(r"-{2,}", "-", re.sub(r"[\s_]+", "-", str(value or "").strip().lower())).strip("-")


async def options(service: str, fresh: bool = False) -> dict:
    """Quality profiles, root folders and tags, kept for a minute while a flow is edited."""
    cached = _options.get(service)
    if cached and not fresh and time.monotonic() - cached[0] < OPTIONS_SECONDS:
        return cached[1]
    profiles = await get(service, "/qualityprofile")
    folders = await get(service, "/rootfolder")
    tags = await get(service, "/tag")
    result = {
        "profiles": [{"id": int(row["id"]), "name": str(row.get("name") or row["id"])}
                     for row in profiles or [] if isinstance(row, dict) and row.get("id") is not None],
        "root_folders": [{"path": str(row["path"]), "free_space": row.get("freeSpace")}
                         for row in folders or [] if isinstance(row, dict) and row.get("path")],
        "tags": [{"id": int(row["id"]), "label": str(row.get("label") or "")}
                 for row in tags or [] if isinstance(row, dict) and row.get("id") is not None],
    }
    _options[service] = (time.monotonic(), result)
    cache.put(f"arr:profiles:{service}", {str(row["id"]): row["name"] for row in result["profiles"]},
              PROFILE_NAMES_SECONDS)
    return result


def profile_name(service: str, profile_id) -> str | None:
    """A quality profile's name as last read from Radarr or Sonarr, without asking them."""
    names = cache.get(f"arr:profiles:{service}") or {}
    return names.get(str(profile_id)) if isinstance(names, dict) else None


async def tags(service: str, labels: list[str], create: bool) -> tuple[dict[str, int], list[str]]:
    """Tag ids by label; missing tags are created, or listed as 'to create' when create is False."""
    known = {row["label"].lower(): row["id"] for row in (await options(service, fresh=True))["tags"]}
    ids: dict[str, int] = {}
    missing: list[str] = []
    for name in labels:
        if name in known:
            ids[name] = known[name]
        elif create:
            row = await request(service, "POST", "/tag", body={"label": name})
            ids[name] = known[name] = int(row["id"])
            _options.pop(service, None)
        else:
            missing.append(name)
    return ids, missing


EXCLUSIONS = {"radarr": "/exclusions", "sonarr": "/importlistexclusion"}
PAGE_SIZE = 1000


def _exclusion(service: str, row: dict) -> tuple[int, dict] | None:
    if service == "radarr":
        external, title, year = row.get("tmdbId"), row.get("movieTitle") or row.get("title"), row.get("movieYear")
    else:
        external, title, year = row.get("tvdbId"), row.get("title"), row.get("year")
    if not external:
        return None
    return int(external), {"arr_id": row.get("id"), "title": title or "", "year": year or None}


async def exclusion_list(service: str) -> dict[int, dict]:
    """The import list exclusions: TMDB ids (Radarr) or TVDB ids (Sonarr) -> {arr_id, title, year}.

    Newer versions page them (/paged); older ones only return the whole list.
    """
    path = EXCLUSIONS[service]
    rows: list = []
    try:
        page = 1
        while True:
            data = await get(service, f"{path}/paged", {"page": page, "pageSize": PAGE_SIZE})
            records = (data or {}).get("records") or [] if isinstance(data, dict) else []
            rows += records
            total = int((data or {}).get("totalRecords") or 0) if isinstance(data, dict) else 0
            if not records or len(rows) >= total or page >= 100:
                break
            page += 1
    except ArrError as exc:
        if exc.status not in (404, 405):
            raise
        data = await get(service, path)
        rows = data if isinstance(data, list) else []
    result: dict[int, dict] = {}
    for row in rows:
        if isinstance(row, dict) and (found := _exclusion(service, row)):
            result[found[0]] = found[1]
    return result


async def exclusions(service: str) -> set[int]:
    """TMDB ids (Radarr) or TVDB ids (Sonarr) on the import list exclusions: list imports skip them."""
    return set(await exclusion_list(service))


async def add_exclusion(service: str, external_id: int, title: str, year: int | None) -> int | None:
    """Puts a title on the exclusion list there; returns the entry's id."""
    if service == "radarr":
        body = {"tmdbId": external_id, "movieTitle": title, "movieYear": year or 0}
    else:
        body = {"tvdbId": external_id, "title": title}
    row = await request(service, "POST", EXCLUSIONS[service], body=body, timeout=8.0)
    return row.get("id") if isinstance(row, dict) else None


async def remove_exclusion(service: str, arr_id: int) -> None:
    await request(service, "DELETE", f"{EXCLUSIONS[service]}/{arr_id}", timeout=8.0)


async def find(service: str, media: dict) -> dict | None:
    """The movie or series as Radarr or Sonarr has it, or None."""
    if service == "radarr":
        rows = await get(service, "/movie", {"tmdbId": media["tmdb_id"]})
    else:
        rows = await get(service, "/series", {"tvdbId": media["tvdb_id"]})
    for row in rows or []:
        if isinstance(row, dict) and row.get("id"):
            return row
    return None


async def add_movie(media: dict, config: dict, tag_ids: list[int]) -> dict:
    found = await get("radarr", "/movie/lookup/tmdb", {"tmdbId": media["tmdb_id"]})
    body = dict(found) if isinstance(found, dict) else {}
    body.pop("id", None)
    body.update({
        "title": body.get("title") or media["title"],
        "tmdbId": int(media["tmdb_id"]),
        "year": body.get("year") or media.get("year") or 0,
        "titleSlug": body.get("titleSlug") or str(media["tmdb_id"]),
        "qualityProfileId": int(config["quality_profile"]),
        "rootFolderPath": config["root_folder"],
        "monitored": bool(config["monitored"]),
        "minimumAvailability": config["minimum_availability"],
        "tags": tag_ids,
        "addOptions": {"searchForMovie": bool(config["search"]),
                       "monitor": "movieOnly" if config["monitored"] else "none"},
    })
    return await request("radarr", "POST", "/movie", body=body, timeout=60.0)


async def add_series(media: dict, config: dict, tag_ids: list[int]) -> dict:
    rows = await get("sonarr", "/series/lookup", {"term": f"tvdb:{media['tvdb_id']}"})
    found = next((row for row in rows or [] if isinstance(row, dict)
                  and str(row.get("tvdbId")) == str(media["tvdb_id"])), None)
    if found is None:
        raise ArrError(f"{label('sonarr')} does not find TVDB {media['tvdb_id']}")
    body = dict(found)
    body.pop("id", None)
    monitor = config["monitor"]
    body.update({
        "title": body.get("title") or media["title"],
        "tvdbId": int(media["tvdb_id"]),
        "qualityProfileId": int(config["quality_profile"]),
        "rootFolderPath": config["root_folder"],
        "seasonFolder": bool(config["season_folder"]),
        "seriesType": config["series_type"],
        "monitored": monitor != "none",
        "tags": tag_ids,
        "addOptions": {"monitor": monitor, "searchForMissingEpisodes": bool(config["search"]),
                       "searchForCutoffUnmetEpisodes": False},
    })
    return await request("sonarr", "POST", "/series", body=body, timeout=60.0)


async def tvdb_id(media: dict) -> int | None:
    """The TVDB ID of a show as Sonarr finds it by its TMDB or IMDb ID.

    Sonarr asks TVDB (through Skyhook), which keeps its own links to TMDB and IMDb, so this helps where
    TMDB has no TVDB link. Only an answer with the same TMDB or IMDb ID counts, never a title match.
    """
    asks = []
    if media.get("tmdb_id"):
        asks.append((f"tmdb:{media['tmdb_id']}", "tmdbId", str(media["tmdb_id"])))
    if media.get("imdb_id"):
        asks.append((f"imdb:{media['imdb_id']}", "imdbId", str(media["imdb_id"])))
    for term, field, value in asks:
        rows = await request("sonarr", "GET", "/series/lookup", {"term": term}, timeout=20.0)
        for row in rows or []:
            if isinstance(row, dict) and row.get("tvdbId") and str(row.get(field) or "") == value:
                return int(row["tvdbId"])
    return None
