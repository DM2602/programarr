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
TYPE_COLLECTION = 18

# Collection order in Plex' advanced settings
COLLECTION_SORT = {"release": 0, "alpha": 1, "custom": 2}

_machines: dict[str, str] = {}  # server address -> machine identifier

_LEGACY = re.compile(r"com\.plexapp\.agents\.(?P<agent>[a-z]+)://(?P<id>[^/?]+)")


class PlexError(Exception):
    """Plex could not be reached or refused the request; the message is for the UI."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


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

    async def _request(self, method: str, path: str, params: dict | None = None, timeout: float = 60.0,
                       content: bytes | None = None, content_type: str | None = None) -> httpx.Response:
        if not self.configured:
            raise PlexError("Plex is not set up yet (Settings › Plex)")
        headers = {"Content-Type": content_type} if content_type else None
        try:
            async with self._client(timeout) as client:
                response = await client.request(method, f"{self.url}{path}", params=params, content=content,
                                                headers=headers)
        except httpx.TimeoutException as exc:
            raise PlexError(f"Plex is not responding ({self.url})") from exc
        except httpx.HTTPError as exc:
            raise PlexError(f"Cannot reach Plex ({self.url}): {exc.__class__.__name__}") from exc
        if response.status_code == 401:
            raise PlexError("Plex rejects the token (401)")
        if response.status_code == 404:
            raise PlexError(f"Plex cannot find {path} (404)", 404)
        if response.status_code >= 400:
            raise PlexError(f"Plex returned {response.status_code} for {path}", response.status_code)
        return response

    async def _get(self, path: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        response = await self._request("GET", path, params, timeout)
        try:
            return response.json().get("MediaContainer", {})
        except ValueError as exc:
            raise PlexError(f"Unexpected response from Plex for {path}") from exc

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

    async def upload_poster(self, rating_key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        """Upload an image as the poster; Plex selects it."""
        await self._request("POST", f"/library/metadata/{rating_key}/posters", content=data,
                            content_type=content_type, timeout=120.0)

    # -- collections -------------------------------------------------------
    async def machine(self) -> str:
        """The server's machine identifier, needed to name items in collection requests."""
        if self.url not in _machines:
            _machines[self.url] = (await self.identity())["machine"]
        return _machines[self.url]

    async def _uri(self, item_keys: list[str]) -> str:
        return f"server://{await self.machine()}/com.plexapp.plugins.library/library/metadata/{','.join(item_keys)}"

    async def collections(self, section_key: str) -> list[dict]:
        data = await self._get(f"/library/sections/{section_key}/collections", {"includeExternalMedia": 0})
        return data.get("Metadata", []) or []

    async def collection(self, rating_key: str) -> dict | None:
        """A collection's metadata, or None if Plex no longer has it."""
        try:
            data = await self._get(f"/library/metadata/{rating_key}")
        except PlexError as exc:
            if exc.status == 404:
                return None
            raise
        meta = data.get("Metadata", [])
        return meta[0] if meta and meta[0].get("type") == "collection" else None

    async def collection_items(self, rating_key: str) -> list[dict]:
        """The items in their collection order."""
        data = await self._get(f"/library/collections/{rating_key}/children", timeout=120.0)
        return data.get("Metadata", []) or []

    async def create_collection(self, section_key: str, plex_type: int, title: str, item_keys: list[str]) -> str:
        """A regular collection with these items (Plex needs at least one); returns its rating key."""
        response = await self._request("POST", "/library/collections", {
            "type": plex_type, "title": title, "smart": 0, "sectionId": section_key,
            "uri": await self._uri(item_keys),
        })
        try:
            meta = response.json().get("MediaContainer", {}).get("Metadata", [])
        except ValueError as exc:
            raise PlexError("Unexpected response from Plex when creating a collection") from exc
        if not meta or not meta[0].get("ratingKey"):
            raise PlexError("Plex did not create the collection")
        return str(meta[0]["ratingKey"])

    async def add_to_collection(self, rating_key: str, item_keys: list[str]) -> None:
        for start in range(0, len(item_keys), 100):
            await self._request("PUT", f"/library/collections/{rating_key}/items",
                                {"uri": await self._uri(item_keys[start:start + 100])})

    async def remove_from_collection(self, rating_key: str, item_key: str) -> None:
        await self._request("DELETE", f"/library/collections/{rating_key}/items/{item_key}")

    async def move_in_collection(self, rating_key: str, item_key: str, after: str | None) -> None:
        """Move an item behind another one, or to the front."""
        await self._request("PUT", f"/library/collections/{rating_key}/items/{item_key}/move",
                            {"after": after} if after else None)

    async def collection_settings(self, rating_key: str, **values: int) -> None:
        """Advanced settings such as collectionSort (see COLLECTION_SORT)."""
        await self._request("PUT", f"/library/collections/{rating_key}/prefs", values)

    async def edit_title(self, section_key: str, rating_key: str, title: str, plex_type: int = TYPE_COLLECTION) -> None:
        await self._request("PUT", f"/library/sections/{section_key}/all", {
            "type": plex_type, "id": rating_key, "title.value": title, "title.locked": 1,
        })

    async def delete_collection(self, rating_key: str) -> None:
        await self._request("DELETE", f"/library/collections/{rating_key}")

    async def hub(self, section_key: str, rating_key: str) -> dict | None:
        """The collection's entry in Manage Recommendations (visibility), or None if it has none yet."""
        data = await self._get(f"/hubs/sections/{section_key}/manage", {"metadataItemId": rating_key})
        hubs = data.get("Hub", []) or []
        return hubs[0] if hubs else None

    async def set_visibility(self, section_key: str, rating_key: str, recommended: bool, home: bool,
                             shared: bool) -> None:
        """Show the collection in Recommended, on the own Home and on friends' Home."""
        flags = {"promotedToRecommended": int(recommended), "promotedToOwnHome": int(home),
                 "promotedToSharedHome": int(shared)}
        hub = await self.hub(section_key, rating_key)
        if hub is None:
            if not any(flags.values()):
                return
            await self._request("POST", f"/hubs/sections/{section_key}/manage",
                                {"metadataItemId": rating_key, **flags})
            return
        identifier = hub.get("identifier") or f"custom.collection.{section_key}.{rating_key}"
        await self._request("PUT", f"/hubs/sections/{section_key}/manage/{identifier}", flags)


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
