"""Posters for collections: a copy under /data/collections, uploaded to Plex (collections have no folder).

ThePosterDB lists collection posters under its own search section; an upload or a link works as well.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .. import config, http, imaging, settings
from ..posters.sources import base as source_base
from ..posters.sources import tpdb

FOLDER = "collections"


def _folder() -> Path:
    return config.DATA_DIR / FOLDER


def path_for(collection_id: int) -> Path:
    return _folder() / f"{collection_id}.jpg"


def content_type(name: str) -> str:
    return "image/png" if name.lower().endswith(".png") else "image/jpeg"


def read(collection: dict) -> bytes | None:
    if not collection.get("poster"):
        return None
    path = _folder() / collection["poster"]
    try:
        return path.read_bytes()
    except OSError:
        return None


def save(collection_id: int, data: bytes) -> str:
    """Store the poster as JPEG or PNG; returns the file name."""
    prepared = imaging.prepare(data)
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{collection_id}.*"):
        old.unlink(missing_ok=True)
    name = f"{collection_id}{prepared.ext}"
    (folder / name).write_bytes(prepared.data)
    return name


def remove(collection: dict) -> None:
    for old in _folder().glob(f"{collection['id']}.*"):
        old.unlink(missing_ok=True)


async def download(url: str) -> bytes:
    data, _ = await http.fetch_public(url, allow_private=bool(settings.get("allow_private_urls")))
    return data


async def tpdb_search(query: str) -> list[dict]:
    """Collection pages on ThePosterDB for a name."""
    return await tpdb.search_section(query, "collections")


async def tpdb_posters(page_url: str) -> list[source_base.ImageRef]:
    """The collection posters on one ThePosterDB page."""
    cards = await tpdb.item_cards(page_url)
    typed = [card for card in cards if card.poster_type in ("collection", "category")]
    chosen = typed or [card for card in cards if card.season is None]
    return [tpdb.collection_ref(card) for card in chosen]


async def fetch(ref: source_base.ImageRef) -> bytes:
    data, _ = await tpdb.fetch(ref)
    return await asyncio.to_thread(lambda: imaging.prepare(data).data)
