"""TMDB as the reference for titles: search, details with external IDs, lookups and charts.

Uses the same key as the poster module (Settings › Sources), a v3 key or a v4 read token.
"""
from __future__ import annotations

import asyncio
import re

import httpx

from .. import cache, http, settings


class TmdbError(Exception):
    """TMDB could not answer; the message is meant for the UI."""


_limit = asyncio.Semaphore(6)
KINDS = {"movie": "movie", "show": "tv"}
_POSTER_PATH = re.compile(r"^/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)$")


def poster_url(path: str | None) -> str | None:
    """Our image route for a TMDB poster path."""
    if path and _POSTER_PATH.match(path):
        return f"/img/tmdb{path}"
    return None


def configured() -> bool:
    return bool(settings.get("tmdb_key"))


def _auth(key: str) -> tuple[dict, dict]:
    if key.startswith("eyJ"):
        return {"Authorization": f"Bearer {key}"}, {}
    return {}, {"api_key": key}


async def get(path: str, params: dict | None = None) -> dict | None:
    """GET a TMDB v3 path; None on 404. Retries once when TMDB asks to slow down."""
    key = settings.get("tmdb_key")
    if not key:
        raise TmdbError("No TMDB key set (Settings › Sources)")
    headers, auth = _auth(key)
    base = settings.get("tmdb_api").rstrip("/")
    query = {**auth, **{k: v for k, v in (params or {}).items() if v not in (None, "")}}
    async with _limit:
        for attempt in range(3):
            async with http.client(timeout=30.0, headers={**headers, "Accept": "application/json"}) as client:
                try:
                    response = await client.get(f"{base}{path}", params=query)
                except httpx.HTTPError as exc:
                    raise TmdbError(f"Cannot reach TMDB: {exc.__class__.__name__}") from exc
            if response.status_code == 429 and attempt < 2:
                await asyncio.sleep(min(10.0, float(response.headers.get("retry-after") or 2)))
                continue
            break
    if response.status_code == 404:
        return None
    if response.status_code == 401:
        raise TmdbError("TMDB rejects the key")
    if response.status_code >= 400:
        raise TmdbError(f"TMDB answered {response.status_code}")
    return response.json()


def year_of(date: str | None) -> int | None:
    if date and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def brief(kind: str, data: dict) -> dict:
    """Search or chart result in one shape for both kinds."""
    date = data.get("release_date") if kind == "movie" else data.get("first_air_date")
    return {
        "kind": kind,
        "tmdb_id": int(data["id"]),
        "title": data.get("title") or data.get("name") or "",
        "original_title": data.get("original_title") or data.get("original_name") or "",
        "year": year_of(date),
        "popularity": float(data.get("popularity") or 0),
        "votes": int(data.get("vote_count") or 0),
        "poster_path": data.get("poster_path"),
    }


async def search(kind: str, query: str, year: int | None = None) -> list[dict]:
    params = {"query": query, "include_adult": "false", "language": "en-US"}
    if year:
        params["year" if kind == "movie" else "first_air_date_year"] = year
    data = await get(f"/search/{KINDS[kind]}", params) or {}
    return [brief(kind, row) for row in data.get("results") or [] if row.get("id")]


async def details(kind: str, tmdb_id: int) -> dict | None:
    return await get(f"/{KINDS[kind]}/{int(tmdb_id)}", {"append_to_response": "external_ids", "language": "en-US"})


async def find(external_id: str, source: str) -> list[tuple[str, int]]:
    """IMDb or TVDB id -> [(kind, tmdb_id)]."""
    data = await get(f"/find/{external_id}", {"external_source": source}) or {}
    found = [("movie", int(row["id"])) for row in data.get("movie_results") or []]
    found += [("show", int(row["id"])) for row in data.get("tv_results") or []]
    return found


async def page(path: str, kind: str, page_number: int = 1, params: dict | None = None) -> tuple[list[dict], int]:
    """One page of a chart or list: (results, total_pages)."""
    data = await get(path, {"page": page_number, "language": "en-US", **(params or {})}) or {}
    rows = []
    for row in data.get("results") or data.get("items") or []:
        row_kind = kind
        if kind == "any":
            row_kind = "show" if row.get("media_type") == "tv" else "movie" if row.get("media_type") == "movie" else ""
        if row_kind in ("movie", "show") and row.get("id"):
            rows.append(brief(row_kind, row))
    return rows, int(data.get("total_pages") or 1)


async def alternative_titles(kind: str, tmdb_id: int) -> list[str]:
    key = f"tmdb:alt:{kind}:{int(tmdb_id)}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    data = await get(f"/{KINDS[kind]}/{int(tmdb_id)}/alternative_titles") or {}
    titles = [row.get("title") for row in data.get("titles") or data.get("results") or [] if row.get("title")]
    cache.put(key, titles, 30 * 86400)
    return titles
