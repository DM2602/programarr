"""Plex Media Server client (JSON API)."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

from . import http, settings
from .config import APP_NAME, VERSION

TYPE_MOVIE = 1
TYPE_SHOW = 2
TYPE_SEASON = 3
TYPE_EPISODE = 4

_LEGACY = re.compile(r"com\.plexapp\.agents\.(?P<agent>[a-z]+)://(?P<id>[^/?]+)")


class PlexError(Exception):
    """Plex could not be reached or refused the request; the message is for the UI."""


class Plex:
    def __init__(self, url: str, token: str):
        self.url = (url or "").strip().rstrip("/")
        self.token = (token or "").strip()

    @classmethod
    def from_settings(cls) -> "Plex":
        return cls(settings.get("plex_url"), settings.get("plex_token"))

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)

    def _client(self, timeout: float = 60.0) -> httpx.AsyncClient:
        return http.client(
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "X-Plex-Token": self.token,
                "X-Plex-Product": APP_NAME,
                "X-Plex-Version": VERSION,
                "X-Plex-Client-Identifier": "programarr",
            },
        )

    async def _request(self, method: str, path: str, params: dict | None = None, timeout: float = 60.0) -> httpx.Response:
        if not self.configured:
            raise PlexError("Plex is not set up yet (Settings › Plex)")
        try:
            async with self._client(timeout) as client:
                response = await client.request(method, f"{self.url}{path}", params=params)
        except httpx.TimeoutException as exc:
            raise PlexError(f"Plex does not answer ({self.url})") from exc
        except httpx.HTTPError as exc:
            raise PlexError(f"Cannot reach Plex ({self.url}): {exc.__class__.__name__}") from exc
        if response.status_code == 401:
            raise PlexError("Plex rejects the token (401)")
        if response.status_code == 404:
            raise PlexError(f"Plex does not know {path} (404)")
        if response.status_code >= 400:
            raise PlexError(f"Plex answered {response.status_code} for {path}")
        return response

    async def _get(self, path: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        response = await self._request("GET", path, params, timeout)
        try:
            return response.json().get("MediaContainer", {})
        except ValueError as exc:
            raise PlexError(f"Unexpected answer from Plex for {path}") from exc

    # -- reading ---------------------------------------------------------
    async def identity(self) -> dict:
        data = await self._get("/", timeout=15.0)
        return {
            "name": data.get("friendlyName", ""),
            "version": data.get("version", ""),
            "machine": data.get("machineIdentifier", ""),
        }

    async def sections(self) -> list[dict]:
        data = await self._get("/library/sections")
        result = []
        for entry in data.get("Directory", []):
            if entry.get("type") not in ("movie", "show"):
                continue
            result.append(
                {
                    "key": str(entry.get("key")),
                    "title": entry.get("title", ""),
                    "type": entry.get("type"),
                    "agent": entry.get("agent", ""),
                    "locations": [loc.get("path", "") for loc in entry.get("Location", [])],
                }
            )
        return result

    async def section_items(self, section_key: str, plex_type: int) -> list[dict]:
        items: list[dict] = []
        start, size = 0, 500
        while True:
            data = await self._get(
                f"/library/sections/{section_key}/all",
                {
                    "type": plex_type,
                    "includeGuids": 1,
                    "X-Plex-Container-Start": start,
                    "X-Plex-Container-Size": size,
                },
                timeout=120.0,
            )
            batch = data.get("Metadata", [])
            items.extend(batch)
            total = int(data.get("totalSize", data.get("size", len(items))) or 0)
            start += len(batch)
            if not batch or start >= total:
                break
        return items

    async def metadata(self, rating_key: str) -> dict | None:
        data = await self._get(f"/library/metadata/{rating_key}", {"includeGuids": 1})
        meta = data.get("Metadata", [])
        return meta[0] if meta else None

    async def all_leaves(self, show_key: str) -> list[dict]:
        data = await self._get(f"/library/metadata/{show_key}/allLeaves", timeout=120.0)
        return data.get("Metadata", [])

    async def children(self, rating_key: str) -> list[dict]:
        data = await self._get(f"/library/metadata/{rating_key}/children")
        return data.get("Metadata", [])

    async def posters(self, rating_key: str) -> list[dict]:
        data = await self._get(f"/library/metadata/{rating_key}/posters")
        result = []
        for entry in data.get("Metadata", []) or data.get("Photo", []):
            selected = entry.get("selected")
            result.append(
                {
                    "key": entry.get("key", ""),
                    "rating_key": entry.get("ratingKey", ""),
                    "thumb": entry.get("thumb", "") or entry.get("key", ""),
                    "provider": entry.get("provider", ""),
                    "selected": selected in (True, 1, "1", "true"),
                }
            )
        return result

    async def image(self, path: str) -> tuple[bytes, str]:
        """Download an image Plex refers to (server path or remote agent URL)."""
        if path.startswith("/"):
            response = await self._request("GET", path, timeout=60.0)
            return response.content, response.headers.get("content-type", "")
        parts = urlsplit(path)
        if parts.scheme not in ("http", "https"):
            raise PlexError("Unknown image address from Plex")
        async with http.client(timeout=60.0, headers={"User-Agent": http.BROWSER_UA}) as client:
            try:
                response = await client.get(path)
            except httpx.HTTPError as exc:
                raise PlexError(f"Cannot load image: {exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            raise PlexError(f"Cannot load image ({response.status_code})")
        return response.content, response.headers.get("content-type", "")

    async def thumb(self, path: str, width: int = 360, height: int = 540) -> tuple[bytes, str]:
        """A small version through Plex' photo transcoder."""
        response = await self._request(
            "GET",
            "/photo/:/transcode",
            {"url": path, "width": width, "height": height, "minSize": 1, "upscale": 0},
            timeout=40.0,
        )
        return response.content, response.headers.get("content-type", "")

    # -- writing ---------------------------------------------------------
    async def refresh(self, rating_key: str) -> None:
        await self._request("PUT", f"/library/metadata/{rating_key}/refresh", {"force": 0})

    async def unlock(self, section_key: str, plex_type: int, rating_key: str, field: str = "thumb") -> None:
        await self._request(
            "PUT",
            f"/library/sections/{section_key}/all",
            {"type": plex_type, "id": rating_key, f"{field}.locked": 0},
        )

    async def select_poster(self, rating_key: str, poster_key: str) -> None:
        await self._request("PUT", f"/library/metadata/{rating_key}/poster", {"url": poster_key})


def parse_guids(meta: dict) -> dict:
    ids = {"tmdb": None, "tvdb": None, "imdb": None}
    for guid in meta.get("Guid", []) or []:
        value = str(guid.get("id", ""))
        if "://" not in value:
            continue
        scheme, ident = value.split("://", 1)
        if scheme in ids and ident and not ids[scheme]:
            ids[scheme] = ident
    legacy = _LEGACY.search(str(meta.get("guid", "")))
    if legacy:
        agent, ident = legacy.group("agent"), legacy.group("id")
        if agent == "themoviedb" and not ids["tmdb"]:
            ids["tmdb"] = ident
        elif agent == "thetvdb" and not ids["tvdb"]:
            ids["tvdb"] = ident
        elif agent == "imdb" and not ids["imdb"]:
            ids["imdb"] = ident
    return ids


def locked_fields(meta: dict) -> list[str]:
    return sorted(
        str(field.get("name"))
        for field in meta.get("Field", []) or []
        if field.get("locked") in (True, 1, "1", "true")
    )


def media_files(meta: dict) -> list[str]:
    files = []
    for media in meta.get("Media", []) or []:
        for part in media.get("Part", []) or []:
            if part.get("file"):
                files.append(part["file"])
    return files


def year_of(meta: dict) -> int | None:
    if meta.get("year"):
        try:
            return int(meta["year"])
        except (TypeError, ValueError):
            return None
    date = str(meta.get("originallyAvailableAt", ""))
    return int(date[:4]) if date[:4].isdigit() else None
