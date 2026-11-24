"""Radarr and Sonarr: where a title stands there, and the link to it. Phase 3 adds them as flow outputs."""
from __future__ import annotations

from urllib.parse import quote

import httpx

from . import http, settings

SERVICES = {
    "radarr": {"label": "Radarr", "kind": "movie"},
    "sonarr": {"label": "Sonarr", "kind": "show"},
}


class ArrError(Exception):
    """Radarr or Sonarr could not answer; the message is meant for the UI."""


def for_kind(kind: str) -> str:
    return "radarr" if kind == "movie" else "sonarr"


def configured(service: str) -> bool:
    return bool(settings.get(f"{service}_url") and settings.get(f"{service}_key"))


def link_base(service: str) -> str:
    """Address for links in the browser: the external one if set, else the one Programarr uses."""
    return (settings.get(f"{service}_external_url") or settings.get(f"{service}_url") or "").rstrip("/")


async def get(service: str, path: str, params: dict | None = None, url: str | None = None, key: str | None = None):
    label = SERVICES[service]["label"]
    base = (url or settings.get(f"{service}_url") or "").rstrip("/")
    key = key or settings.get(f"{service}_key")
    if not base or not key:
        raise ArrError(f"{label}: address and API key are needed")
    async with http.client(timeout=15.0, headers={"X-Api-Key": key, "Accept": "application/json"}) as client:
        try:
            response = await client.get(f"{base}/api/v3{path}", params=params or {})
        except httpx.HTTPError as exc:
            raise ArrError(f"Cannot reach {label} ({base}): {exc.__class__.__name__}") from exc
    if response.status_code == 401:
        raise ArrError(f"{label} rejects the API key")
    if response.status_code >= 400:
        raise ArrError(f"{label} answered {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise ArrError(f"{label} sent no JSON (is this the {label} address?)") from exc


async def test(service: str, url: str | None = None, key: str | None = None) -> str:
    data = await get(service, "/system/status", url=url, key=key)
    name = data.get("appName") or SERVICES[service]["label"]
    return f"Connected to {name} {data.get('version', '')}".strip()


async def lookup(kind: str, tmdb_id: str | int | None, tvdb_id: str | int | None, imdb_id: str | None,
                 title: str = "") -> dict | None:
    """Where the title stands in Radarr (movies) or Sonarr (shows); None when that service is not set up."""
    service = for_kind(kind)
    if not configured(service):
        return None
    label = SERVICES[service]["label"]
    result = {"service": service, "label": label, "present": False, "monitored": None, "downloaded": None,
              "url": None, "error": None}
    base = link_base(service)
    try:
        if service == "radarr" and tmdb_id:
            rows = await get(service, "/movie", {"tmdbId": tmdb_id})
        elif service == "sonarr" and tvdb_id:
            rows = await get(service, "/series", {"tvdbId": tvdb_id})
        else:
            rows = []
    except ArrError as exc:
        result["error"] = str(exc)
        return result
    found = next((row for row in rows if isinstance(row, dict)), None) if isinstance(rows, list) else None
    if found and found.get("titleSlug"):
        result.update(present=True, monitored=bool(found.get("monitored")),
                      url=f"{base}/{'movie' if service == 'radarr' else 'series'}/{found['titleSlug']}")
        if service == "radarr":
            result["downloaded"] = bool(found.get("hasFile"))
        else:
            stats = found.get("statistics") or {}
            total = stats.get("episodeCount") or 0
            result["downloaded"] = bool(total) and (stats.get("episodeFileCount") or 0) >= total
        return result
    if service == "radarr":
        term = f"tmdb:{tmdb_id}" if tmdb_id else f"imdb:{imdb_id}" if imdb_id else title
    else:
        term = f"tvdb:{tvdb_id}" if tvdb_id else title
    result["url"] = f"{base}/add/new?term={quote(str(term))}" if term else None
    return result
