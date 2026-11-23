"""TMDB images (own API key: v3 key or v4 read token)."""
from __future__ import annotations

import httpx

from ... import cache, http, settings
from .base import ImageRef, SourceError


def configured() -> bool:
    return bool(settings.get("tmdb_key"))


def _auth(key: str) -> tuple[dict, dict]:
    if key.startswith("eyJ"):
        return {"Authorization": f"Bearer {key}"}, {}
    return {}, {"api_key": key}


async def _get(path: str, params: dict | None = None, key: str | None = None) -> dict | None:
    key = key or settings.get("tmdb_key")
    if not key:
        raise SourceError("No TMDB key set")
    headers, auth_params = _auth(key)
    base = settings.get("tmdb_api").rstrip("/")
    async with http.client(timeout=30.0, headers={**headers, "Accept": "application/json"}) as client:
        try:
            response = await client.get(f"{base}{path}", params={**auth_params, **(params or {})})
        except httpx.HTTPError as exc:
            raise SourceError(f"Cannot reach TMDB: {exc.__class__.__name__}") from exc
    if response.status_code == 404:
        return None
    if response.status_code == 401:
        raise SourceError("TMDB rejects the key")
    if response.status_code >= 400:
        raise SourceError(f"TMDB answered {response.status_code}")
    return response.json()


async def test(key: str | None = None) -> str:
    data = await _get("/configuration", key=key)
    if data is None:
        raise SourceError("Unexpected answer from TMDB")
    return "Key is valid"


async def resolve_id(item: dict) -> str | None:
    if item.get("tmdb_id"):
        return str(item["tmdb_id"])
    for source, value in (("imdb_id", item.get("imdb_id")), ("tvdb_id", item.get("tvdb_id"))):
        if not value:
            continue
        data = await _get(f"/find/{value}", {"external_source": source})
        if not data:
            continue
        results = data.get("movie_results" if item["type"] == "movie" else "tv_results") or []
        if results:
            return str(results[0]["id"])
    return None


async def english_title(item: dict) -> tuple[str | None, int | None]:
    tmdb_id = await resolve_id(item)
    if not tmdb_id:
        return None, None
    path = f"/movie/{tmdb_id}" if item["type"] == "movie" else f"/tv/{tmdb_id}"
    data = await _get(path, {"language": "en-US"})
    if not data:
        return None, None
    title = data.get("title") or data.get("name")
    date = data.get("release_date") or data.get("first_air_date") or ""
    return title, int(date[:4]) if date[:4].isdigit() else None


async def images(item: dict, kind: str, season: int | None) -> list[ImageRef]:
    if not configured():
        return []
    tmdb_id = await resolve_id(item)
    if not tmdb_id:
        return []
    if kind == "movie":
        path = f"/movie/{tmdb_id}/images"
    elif kind == "show":
        path = f"/tv/{tmdb_id}/images"
    else:
        path = f"/tv/{tmdb_id}/season/{season}/images"
    languages = settings.get("tmdb_languages") or "de,en,null"
    key = f"tmdb:{path}:{languages}"
    data = cache.get(key)
    if data is None:
        data = await _get(path, {"include_image_language": languages}) or {}
        cache.put(key, data, 24 * 3600)
    order = [lang.strip() for lang in languages.split(",")]
    image_base = settings.get("tmdb_image").rstrip("/")
    refs = []
    for poster in data.get("posters") or []:
        file_path = poster.get("file_path")
        if not file_path:
            continue
        lang = poster.get("iso_639_1")
        refs.append(
            ImageRef(
                source="tmdb",
                image_id=file_path,
                kind=kind,
                season=season,
                url=f"{image_base}/original{file_path}",
                thumb_url=f"{image_base}/w342{file_path}",
                language=lang or "textless",
                width=poster.get("width"),
                height=poster.get("height"),
                likes=poster.get("vote_count"),
                label=f"{lang.upper() if lang else 'Textless'}, {poster.get('width')} × {poster.get('height')}",
                extra={"vote": poster.get("vote_average"), "rank": order.index(lang or "null") if (lang or "null") in order else 99},
            )
        )
    refs.sort(key=lambda ref: (ref.extra["rank"], -(ref.extra.get("vote") or 0), -(ref.likes or 0)))
    return refs


async def fetch(ref: ImageRef, thumb: bool = False) -> tuple[bytes, str]:
    url = ref.thumb_url if thumb and ref.thumb_url else ref.url
    async with http.client(timeout=60.0) as client:
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            raise SourceError(f"Cannot load TMDB image: {exc.__class__.__name__}") from exc
    if response.status_code >= 400:
        raise SourceError(f"Cannot load TMDB image ({response.status_code})")
    return response.content, response.headers.get("content-type", "")
