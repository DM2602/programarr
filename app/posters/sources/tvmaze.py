"""TVmaze posters for shows and seasons (no key needed)."""
from __future__ import annotations

import httpx

from ... import cache, http, settings
from .base import ImageRef, SourceError


def configured() -> bool:
    return bool(settings.get("tvmaze_enabled"))


async def _get(path: str, params: dict | None = None):
    base = settings.get("tvmaze_api").rstrip("/")
    async with http.client(timeout=30.0, headers={"Accept": "application/json"}) as client:
        try:
            response = await client.get(f"{base}{path}", params=params)
        except httpx.HTTPError as exc:
            raise SourceError(f"Cannot reach TVmaze: {exc.__class__.__name__}") from exc
    if response.status_code == 404:
        return None
    if response.status_code == 429:
        raise SourceError("TVmaze is rate limiting (429), try again later")
    if response.status_code >= 400:
        raise SourceError(f"TVmaze answered {response.status_code}")
    return response.json()


async def show_id(item: dict) -> str | None:
    key = f"tvmaze:id:{item.get('tvdb_id')}:{item.get('imdb_id')}"
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    found = None
    for param, value in (("thetvdb", item.get("tvdb_id")), ("imdb", item.get("imdb_id"))):
        if not value:
            continue
        data = await _get("/lookup/shows", {param: value})
        if data and data.get("id"):
            found = str(data["id"])
            break
    cache.put(key, found or "", 7 * 86400)
    return found


async def images(item: dict, kind: str, season: int | None) -> list[ImageRef]:
    if not configured() or kind == "movie":
        return []
    ident = await show_id(item)
    if not ident:
        return []
    refs = []
    if kind == "show":
        data = cache.get(f"tvmaze:images:{ident}")
        if data is None:
            data = await _get(f"/shows/{ident}/images") or []
            cache.put(f"tvmaze:images:{ident}", data, 24 * 3600)
        for entry in data:
            if entry.get("type") != "poster":
                continue
            res = entry.get("resolutions") or {}
            original = (res.get("original") or {}).get("url")
            medium = (res.get("medium") or {}).get("url") or original
            if original:
                refs.append(ImageRef("tvmaze", str(entry.get("id")), "show", None, original, medium,
                                     width=(res.get("original") or {}).get("width"),
                                     height=(res.get("original") or {}).get("height"),
                                     label="Main poster" if entry.get("main") else "Poster"))
    else:
        data = cache.get(f"tvmaze:seasons:{ident}")
        if data is None:
            data = await _get(f"/shows/{ident}/seasons") or []
            cache.put(f"tvmaze:seasons:{ident}", data, 24 * 3600)
        for entry in data:
            if entry.get("number") != season or not entry.get("image"):
                continue
            image = entry["image"]
            if image.get("original"):
                refs.append(ImageRef("tvmaze", f"season-{entry.get('id')}", "season", season, image["original"],
                                     image.get("medium") or image["original"], label=f"Season {season}"))
    return refs


async def fetch(ref: ImageRef, thumb: bool = False) -> tuple[bytes, str]:
    url = ref.thumb_url if thumb and ref.thumb_url else ref.url
    async with http.client(timeout=60.0) as client:
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            raise SourceError(f"Cannot load TVmaze image: {exc.__class__.__name__}") from exc
    if response.status_code >= 400:
        raise SourceError(f"Cannot load TVmaze image ({response.status_code})")
    return response.content, response.headers.get("content-type", "")
