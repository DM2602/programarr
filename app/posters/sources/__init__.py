"""Poster sources and a single place to download any image reference."""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from ... import cache, config, http, settings
from ...plex import Plex, PlexError
from . import fanart, mediux, tmdb, tpdb, tvmaze
from .base import SOURCE_NAMES, ImageRef, PosterSet, SourceError  # noqa: F401

SET_SOURCES = ("mediux", "tpdb")
ONLINE_SOURCES = ("tmdb", "fanart", "tvmaze")


def source_enabled(name: str) -> bool:
    if name == "mediux":
        return mediux.configured()
    if name == "tpdb":
        return True
    if name == "tmdb":
        return tmdb.configured()
    if name == "fanart":
        return fanart.configured()
    if name == "tvmaze":
        return tvmaze.configured()
    return True


async def plex_images(rating_key: str, kind: str, season: int | None) -> list[ImageRef]:
    plex = Plex.from_settings()
    posters = await plex.posters(rating_key)
    refs = []
    for poster in posters:
        provider = poster["provider"] or ""
        key = poster["rating_key"] or poster["key"]
        if poster["rating_key"].startswith("upload://"):
            label = "Uploaded to Plex"
        elif provider == "local" or "localmedia" in poster["rating_key"]:
            label = "File in folder"
        elif provider:
            label = provider.replace("com.plexapp.agents.", "").replace("tv.plex.agents.", "")
        else:
            label = "Plex"
        refs.append(
            ImageRef(
                source="plex",
                image_id=key,
                kind=kind,
                season=season,
                url=poster["key"],
                thumb_url=poster["thumb"],
                label=label + (", active" if poster["selected"] else ""),
                extra={"selected": poster["selected"], "provider": provider, "local": label == "File in folder"},
            )
        )
    refs.sort(key=lambda ref: (not ref.extra["selected"], ref.extra["local"]))
    return refs


async def online_images(item: dict, kind: str, season: int | None) -> dict[str, list[ImageRef] | str]:
    """Images from TMDB, fanart.tv and TVmaze; a string value is an error message."""
    result: dict[str, list[ImageRef] | str] = {}

    async def run(name, module):
        if not source_enabled(name):
            return
        try:
            result[name] = await module.images(item, kind, season)
        except SourceError as exc:
            result[name] = str(exc)

    await asyncio.gather(run("tmdb", tmdb), run("fanart", fanart), run("tvmaze", tvmaze))
    return result


def upload_path(image_id: str) -> Path:
    safe = "".join(ch for ch in image_id if ch.isalnum() or ch in "-_.")
    return config.DATA_DIR / "uploads" / safe


def store_upload(data: bytes) -> str:
    image_id = f"{uuid.uuid4().hex}.bin"
    path = upload_path(image_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return image_id


async def fetch(ref: ImageRef, thumb: bool = False) -> tuple[bytes, str]:
    """Download the image (or its preview) behind a reference."""
    try:
        if ref.source == "mediux":
            return await mediux.fetch(ref, thumb)
        if ref.source == "tpdb":
            return await tpdb.fetch(ref, thumb)
        if ref.source == "tmdb":
            return await tmdb.fetch(ref, thumb)
        if ref.source == "fanart":
            return await fanart.fetch(ref, thumb)
        if ref.source == "tvmaze":
            return await tvmaze.fetch(ref, thumb)
        if ref.source == "plex":
            plex = Plex.from_settings()
            path = ref.thumb_url if thumb and ref.thumb_url else ref.url
            if thumb and path.startswith("/"):
                try:
                    return await plex.thumb(path)
                except PlexError:
                    pass
            return await plex.image(path)
        if ref.source == "url":
            stored = upload_path(ref.image_id) if ref.image_id else None
            if stored is not None and stored.exists():
                return stored.read_bytes(), ""
            return await http.fetch_public(ref.url, allow_private=bool(settings.get("allow_private_urls")))
        if ref.source == "upload":
            path = upload_path(ref.image_id)
            if not path.exists():
                raise SourceError("The uploaded file is gone, upload it again")
            return path.read_bytes(), ""
        if ref.source in ("archive", "backup"):
            path = Path(ref.url)
            allowed = (config.DATA_DIR / ref.source).resolve()
            if not path.resolve().is_relative_to(allowed) or not path.exists():
                raise SourceError("Archived file not found" if ref.source == "archive" else "Backup copy not found")
            return path.read_bytes(), ""
    except PlexError as exc:
        raise SourceError(str(exc)) from exc
    except http.FetchError as exc:
        raise SourceError(str(exc)) from exc
    raise SourceError(f"Unknown source: {ref.source}")


async def fetch_thumb_cached(ref: ImageRef) -> bytes:
    key = f"thumb:{ref.source}:{ref.image_id}:{ref.modified or ''}"
    data = cache.image_get(key)
    if data is None:
        data, _ = await fetch(ref, thumb=True)
        cache.image_put(key, data)
    return data
