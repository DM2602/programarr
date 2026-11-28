"""fanart.tv posters (own API key). Language 00 means textless."""
from __future__ import annotations

import httpx

from ... import cache, http, settings
from .base import ImageRef, SourceError


def configured() -> bool:
    return bool(settings.get("fanart_key"))


async def _get(path: str, key: str | None = None) -> dict | None:
    key = key or settings.get("fanart_key")
    if not key:
        raise SourceError("No fanart.tv key set")
    params = {"api_key": key}
    if settings.get("fanart_client_key"):
        params["client_key"] = settings.get("fanart_client_key")
    base = settings.get("fanart_api").rstrip("/")
    async with http.client(timeout=30.0, headers={"Accept": "application/json"}) as client:
        try:
            response = await client.get(f"{base}{path}", params=params)
        except httpx.HTTPError as exc:
            raise SourceError(f"Cannot reach fanart.tv: {exc.__class__.__name__}") from exc
    if response.status_code == 404:
        return None
    if response.status_code in (401, 403):
        raise SourceError("fanart.tv rejects the key")
    if response.status_code >= 400:
        raise SourceError(f"fanart.tv returned {response.status_code}")
    try:
        return response.json()
    except ValueError:
        return None


async def test(key: str | None = None) -> str:
    # The Matrix: a movie fanart.tv certainly knows.
    data = await _get("/movies/603", key=key)
    if data is None:
        raise SourceError("Unexpected response from fanart.tv")
    return "Key is valid"


def _preview(url: str) -> str:
    return url.replace("/fanart/", "/preview/", 1) if "/fanart/" in url else url


async def images(item: dict, kind: str, season: int | None) -> list[ImageRef]:
    if not configured():
        return []
    if kind == "movie":
        ident = item.get("tmdb_id") or item.get("imdb_id")
        path, field = f"/movies/{ident}", "movieposter"
    else:
        ident = item.get("tvdb_id")
        path, field = f"/tv/{ident}", "tvposter" if kind == "show" else "seasonposter"
    if not ident:
        return []
    key = f"fanart:{path}"
    data = cache.get(key)
    if data is None:
        data = await _get(path) or {}
        cache.put(key, data, 24 * 3600)
    refs = []
    for entry in data.get(field) or []:
        if kind == "season" and str(entry.get("season")) != str(season):
            continue
        url = entry.get("url")
        if not url:
            continue
        lang = entry.get("lang") or ""
        label_lang = "Textless" if lang in ("00", "") else lang.upper()
        refs.append(
            ImageRef(
                source="fanart",
                image_id=str(entry.get("id")),
                kind=kind,
                season=season,
                url=url,
                thumb_url=_preview(url),
                language="textless" if lang in ("00", "") else lang,
                likes=int(entry.get("likes") or 0),
                label=f"{label_lang}, {entry.get('likes') or 0} Likes",
            )
        )
    refs.sort(key=lambda ref: -(ref.likes or 0))
    return refs


async def fetch(ref: ImageRef, thumb: bool = False) -> tuple[bytes, str]:
    url = ref.thumb_url if thumb and ref.thumb_url else ref.url
    async with http.client(timeout=60.0) as client:
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            raise SourceError(f"Cannot load fanart.tv image: {exc.__class__.__name__}") from exc
    if response.status_code >= 400:
        raise SourceError(f"Cannot load fanart.tv image ({response.status_code})")
    return response.content, response.headers.get("content-type", "")
