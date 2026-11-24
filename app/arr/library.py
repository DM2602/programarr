"""What flows need from Radarr and Sonarr: profiles, root folders, tags and exclusions; adding titles and tags.

Titles that are already there keep their profile, folder and monitoring. They only get the missing tags.
"""
from __future__ import annotations

import re
import time

from .client import ArrError, get, label, request

OPTIONS_SECONDS = 60
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
    return result


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


async def exclusions(service: str) -> set[int]:
    """TMDB ids (Radarr) or TVDB ids (Sonarr) on the import list exclusions: list imports skip them."""
    if service == "radarr":
        rows = await get(service, "/exclusions")
        return {int(row["tmdbId"]) for row in rows or [] if isinstance(row, dict) and row.get("tmdbId")}
    rows = await get(service, "/importlistexclusion")
    return {int(row["tvdbId"]) for row in rows or [] if isinstance(row, dict) and row.get("tvdbId")}


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


async def add_tags(service: str, arr_id: int, tag_ids: list[int]) -> None:
    """Adds tags to one title and leaves everything else as it is."""
    if service == "radarr":
        body = {"movieIds": [arr_id], "tags": tag_ids, "applyTags": "add"}
        await request(service, "PUT", "/movie/editor", body=body)
    else:
        body = {"seriesIds": [arr_id], "tags": tag_ids, "applyTags": "add"}
        await request(service, "PUT", "/series/editor", body=body)
