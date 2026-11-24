"""Poster previews for the pages: files from the folders, Plex' own posters, source previews."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from .. import cache, config, db, http, imaging, settings
from ..media import tmdb as tmdb_media
from ..plex import Plex, PlexError
from ..posters import refsign, sources
from ..posters.sources.base import SourceError, parse_slot_id

router = APIRouter()

PLACEHOLDER = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300" width="200" height="300">
<rect width="200" height="300" fill="#7f8c86" fill-opacity="0.18"/>
<path d="M70 120h60v60H70z" fill="none" stroke="#7f8c86" stroke-width="4" stroke-opacity="0.55"/>
<path d="M78 170l16-18 12 12 10-8 14 14" fill="none" stroke="#7f8c86" stroke-width="4" stroke-opacity="0.55"/>
</svg>"""

HEADERS = {"Cache-Control": "private, max-age=86400"}


def _kind(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"\x89PNG":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def placeholder() -> Response:
    return Response(PLACEHOLDER, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


async def _file_thumb(path: Path, sha: str | None) -> Path | None:
    target = config.DATA_DIR / "cache" / "thumbs" / f"{sha or 'x'}.jpg"
    if sha and target.exists():
        return target
    try:
        data = await asyncio.to_thread(path.read_bytes)
        thumb = await asyncio.to_thread(imaging.thumbnail, data, 420)
    except (OSError, imaging.ImageError):
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_bytes, thumb)
    return target


@router.get("/img/slot/{slot_id}")
async def slot_image(slot_id: str):
    database = db.get()
    row = database.one("SELECT * FROM slots WHERE id=?", (slot_id,))
    if row and row.get("file") and Path(row["file"]).exists():
        thumb = await _file_thumb(Path(row["file"]), row.get("file_sha1"))
        if thumb:
            return FileResponse(thumb, media_type="image/jpeg", headers=HEADERS)
    # No file: show what Plex shows (from its metadata agent).
    try:
        kind, item_key, season = parse_slot_id(slot_id)
    except ValueError:
        return placeholder()
    if kind == "season":
        season_row = database.one("SELECT thumb FROM seasons WHERE show_key=? AND number=? ORDER BY gone LIMIT 1",
                                  (item_key, season))
        path = season_row["thumb"] if season_row else None
    else:
        path = database.scalar("SELECT thumb FROM items WHERE key=?", (item_key,))
    if not path:
        return placeholder()
    key = f"plexthumb:{path}"
    data = cache.image_get(key)
    if data is None:
        try:
            data, _ = await Plex.from_settings().thumb(path)
        except PlexError:
            return placeholder()
        if not data or _kind(data) == "application/octet-stream":
            return placeholder()
        cache.image_put(key, data)
    return Response(data, media_type=_kind(data), headers={**HEADERS, "X-Programarr-Fallback": "plex"})


@router.get("/img/ref/{token}")
async def ref_image(token: str):
    try:
        ref = refsign.ref(token)
        data = await sources.fetch_thumb_cached(ref)
    except SourceError:
        return placeholder()
    kind = _kind(data)
    if kind == "application/octet-stream":
        try:
            data = await asyncio.to_thread(imaging.thumbnail, data, 420)
            kind = "image/jpeg"
        except imaging.ImageError:
            return placeholder()
    return Response(data, media_type=kind, headers=HEADERS)


@router.get("/img/archive/{history_id}")
async def archive_image(history_id: int):
    row = db.get().one("SELECT archived FROM history WHERE id=?", (history_id,))
    if not row or not row["archived"]:
        return placeholder()
    path = Path(row["archived"])
    archive_root = (config.DATA_DIR / "archive").resolve()
    if not path.resolve().is_relative_to(archive_root) or not path.exists():
        return placeholder()
    thumb = await _file_thumb(path, f"archive-{history_id}")
    if not thumb:
        return placeholder()
    return FileResponse(thumb, media_type="image/jpeg", headers=HEADERS)


@router.get("/img/tmdb/{name}")
async def tmdb_image(name: str):
    """TMDB posters for list entries and search results, cached like the other previews."""
    path = f"/{name}"
    if not tmdb_media.poster_url(path):
        return placeholder()
    key = f"tmdb:w342{path}"
    data = cache.image_get(key)
    if data is None:
        base = (settings.get("tmdb_image") or "https://image.tmdb.org/t/p").rstrip("/")
        try:
            async with http.client(timeout=30.0) as client:
                response = await client.get(f"{base}/w342{path}")
        except httpx.HTTPError:
            return placeholder()
        data = response.content
        if response.status_code != 200 or _kind(data) == "application/octet-stream":
            return placeholder()
        cache.image_put(key, data)
    return Response(data, media_type=_kind(data), headers=HEADERS)
