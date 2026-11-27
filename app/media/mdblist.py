"""MDBList: ratings for the media cache, and the user's lists. Free accounts allow 1000 requests a day."""
from __future__ import annotations

import asyncio
import logging

import httpx

from .. import cache, db, http, settings

log = logging.getLogger("programarr.mdblist")

BATCH = 100
_limit = asyncio.Semaphore(2)


class MdblistError(Exception):
    """MDBList could not answer; the message is meant for the UI."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def configured() -> bool:
    return bool(settings.get("mdblist_key"))


async def request(method: str, path: str, params: dict | None = None, body: dict | None = None,
                  key: str | None = None):
    key = key or settings.get("mdblist_key")
    if not key:
        raise MdblistError("No MDBList API key set (Settings › Lists)")
    base = (settings.get("mdblist_api") or "https://api.mdblist.com").rstrip("/")
    query = {"apikey": key, **{k: v for k, v in (params or {}).items() if v not in (None, "")}}
    async with _limit, http.client(timeout=40.0, headers={"Accept": "application/json"}) as client:
        try:
            response = await client.request(method, f"{base}{path}", params=query, json=body)
        except httpx.HTTPError as exc:
            raise MdblistError(f"Cannot reach MDBList: {exc.__class__.__name__}") from exc
    if response.status_code in (401, 403):
        message = _message(response) or "MDBList rejects the key"
        raise MdblistError(message, response.status_code)
    if response.status_code == 429:
        raise MdblistError("MDBList request limit reached for today", 429)
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise MdblistError(_message(response) or f"MDBList answered {response.status_code}", response.status_code)
    try:
        return response.json()
    except ValueError as exc:
        raise MdblistError("MDBList sent no JSON") from exc


def _message(response: httpx.Response) -> str | None:
    try:
        data = response.json()
    except ValueError:
        return None
    if isinstance(data, dict):
        text = data.get("error") or data.get("message") or data.get("detail")
        return str(text)[:200] if text else None
    return None


async def account(key: str | None = None) -> dict:
    """User name and today's request count."""
    data = await request("GET", "/user", key=key) or {}
    return {
        "user": data.get("username") or data.get("name") or data.get("user_name"),
        "limit": data.get("api_requests"),
        "used": data.get("api_requests_count"),
        "patron": data.get("patron_status") or data.get("patreon_status"),
    }


LIST_NAME_SECONDS = 7 * 86400


def cached_list_name(ref: str) -> str | None:
    """The list's title on MDBList, by 'user/slug' or number, if it was read before."""
    return cache.get(f"mdblist:list:{ref}")


def remember_list_name(row: dict, *refs: str) -> None:
    name = " ".join(str(row.get("name") or "").split())
    if not name:
        return
    user = row.get("user_name") or row.get("username")
    keys = {ref for ref in refs if ref}
    if user and row.get("slug"):
        keys.add(f"{user}/{row['slug']}")
    if row.get("id"):
        keys.add(str(row["id"]))
    for key in keys:
        cache.put(f"mdblist:list:{key}", name, LIST_NAME_SECONDS)


async def list_name(ref: str) -> str | None:
    """The list's title; one request, then a week from the cache. None if MDBList does not say."""
    cached = cached_list_name(ref)
    if cached:
        return cached
    try:
        data = await request("GET", f"/lists/{ref}")
    except MdblistError:
        return None
    row = data[0] if isinstance(data, list) and data else data if isinstance(data, dict) else None
    if not isinstance(row, dict):
        return None
    remember_list_name(row, ref)
    return cached_list_name(ref)


async def my_lists() -> list[dict]:
    data = await request("GET", "/lists/user") or []
    rows = data if isinstance(data, list) else data.get("lists") or []
    for row in rows:
        if isinstance(row, dict):
            remember_list_name(row)
    return [
        {
            "id": row.get("id"),
            "name": row.get("name") or "",
            "slug": row.get("slug") or "",
            "user": row.get("user_name") or row.get("username") or "",
            "items": row.get("items"),
            "dynamic": bool(row.get("dynamic")),
            "mediatype": row.get("mediatype") or "",
        }
        for row in rows
        if isinstance(row, dict) and row.get("id")
    ]


# -- ratings ---------------------------------------------------------------------------------------

def _number(value, cast=float):
    try:
        return cast(value) if value not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None


def parse_ratings(item: dict) -> dict:
    """IMDb rating and votes, Rotten Tomatoes critics and audience from one MDBList media answer."""
    values = {"imdb_rating": None, "imdb_votes": None, "rt_critics": None, "rt_audience": None}
    for rating in item.get("ratings") or []:
        if not isinstance(rating, dict):
            continue
        source = str(rating.get("source") or "").lower()
        value = rating.get("value")
        if source == "imdb":
            values["imdb_rating"] = _number(value)
            values["imdb_votes"] = _number(rating.get("votes"), int)
        elif source == "tomatoes":
            values["rt_critics"] = _number(value if value is not None else rating.get("score"), lambda v: round(float(v)))
        elif source in ("popcorn", "tomatoesaudience"):
            values["rt_audience"] = _number(value if value is not None else rating.get("score"), lambda v: round(float(v)))
    return values


def _tvdb_id(item: dict) -> int | None:
    ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
    for value in (ids.get("tvdb"), ids.get("tvdbid"), item.get("tvdbid"), item.get("tvdb_id")):
        try:
            if value and int(value) > 0:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _tmdb_id(item: dict) -> int | None:
    ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
    for value in (ids.get("tmdb"), ids.get("tmdbid"), item.get("tmdbid"), item.get("tmdb_id")):
        try:
            if value:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


async def _batch(kind: str, tmdb_ids: list[int]) -> list[dict]:
    """Media answers for many TMDB IDs; falls back to one request per title when batches are refused."""
    mediatype = "movie" if kind == "movie" else "show"
    if not cache.get("mdblist:single"):
        data = None
        try:
            data = await request("POST", f"/tmdb/{mediatype}", body={"ids": tmdb_ids})
        except MdblistError as exc:
            if exc.status not in (400, 403, 405, 413):
                raise
            if len(tmdb_ids) > 1 and exc.status in (400, 413):
                half = len(tmdb_ids) // 2
                return await _batch(kind, tmdb_ids[:half]) + await _batch(kind, tmdb_ids[half:])
            log.info("MDBList batch refused (%s), asking per title", exc)
        if isinstance(data, dict):
            data = data.get("items") or data.get("results") or data.get("data") or []
        if isinstance(data, list):
            rows = [row for row in data if isinstance(row, dict)]
            if not rows or any(_tmdb_id(row) for row in rows):
                return rows  # none of these titles on MDBList, or answers we can read
        cache.put("mdblist:single", True, 86400)
    rows = []
    for tmdb_id in tmdb_ids:
        data = await request("GET", f"/tmdb/{mediatype}/{tmdb_id}")
        if isinstance(data, dict):
            ids = data.get("ids") if isinstance(data.get("ids"), dict) else {}
            rows.append({**data, "ids": {**ids, "tmdb": ids.get("tmdb") or tmdb_id}})
    return rows


async def refresh_ratings(media_ids: list[int], max_age_days: int | None = None) -> int:
    """Ratings for these titles where missing or older than the setting; returns how many were asked for."""
    if not media_ids or not configured():
        return 0
    days = max_age_days if max_age_days is not None else int(settings.get("ratings_days") or 7)
    limit = db.now() - days * 86400
    marks = ",".join("?" for _ in media_ids)
    rows = db.get().query(
        f"SELECT id, kind, tmdb_id, tvdb_id FROM media_cache WHERE id IN ({marks}) AND tmdb_id IS NOT NULL "
        "AND (ratings_at IS NULL OR ratings_at < ?)",
        [*media_ids, limit],
    )
    asked = 0
    for kind in ("movie", "show"):
        wanted = [row for row in rows if row["kind"] == kind]
        for start in range(0, len(wanted), BATCH):
            chunk = wanted[start:start + BATCH]
            answers = await _batch(kind, [row["tmdb_id"] for row in chunk])
            by_tmdb = {tmdb_id: item for item in answers if (tmdb_id := _tmdb_id(item))}
            database = db.get()
            with database.transaction():
                for row in chunk:
                    item = by_tmdb.get(row["tmdb_id"]) or {}
                    values = parse_ratings(item)
                    values["ratings_at"] = db.now()
                    if kind == "show" and not row["tvdb_id"] and (tvdb := _tvdb_id(item)):
                        values["tvdb_id"] = tvdb  # TMDB has no TVDB link for it, MDBList does
                    database.update("media_cache", "id", row["id"], values)
            asked += len(chunk)
    return asked
