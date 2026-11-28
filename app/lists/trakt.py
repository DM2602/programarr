"""Trakt: charts (trending, popular, anticipated, most watched, box office) and public user lists."""
from __future__ import annotations

import re
from typing import ClassVar

import httpx

from .. import http, settings
from .base import Adapter, Fetched, Field, RawEntry, SourceError, int_or_none

CHARTS = {
    "trending": "Trending",
    "popular": "Popular",
    "anticipated": "Anticipated",
    "watched": "Most watched",
    "played": "Most played",
    "collected": "Most collected",
    "boxoffice": "Box office (movies)",
    "list": "User list",
}
PERIODS = {"daily": "Day", "weekly": "Week", "monthly": "Month", "yearly": "Year", "all": "All time"}
_LIST_URL = re.compile(r"trakt\.tv/users/([^/?#]+)/lists/([^/?#]+)", re.IGNORECASE)


def configured() -> bool:
    return bool(settings.get("trakt_client_id"))


async def get(path: str, params: dict | None = None, client_id: str | None = None):
    client_id = client_id or settings.get("trakt_client_id")
    if not client_id:
        raise SourceError("No Trakt client ID set (Settings › Lists)")
    base = (settings.get("trakt_api") or "https://api.trakt.tv").rstrip("/")
    headers = {"trakt-api-key": client_id, "trakt-api-version": "2", "Content-Type": "application/json"}
    async with http.client(timeout=30.0, headers=headers) as client:
        try:
            response = await client.get(f"{base}{path}", params=params or {})
        except httpx.HTTPError as exc:
            raise SourceError(f"Cannot reach Trakt: {exc.__class__.__name__}") from exc
    if response.status_code in (401, 403):
        raise SourceError("Trakt rejects the client ID")
    if response.status_code == 404:
        raise SourceError("Trakt: not found (private list or wrong address?)")
    if response.status_code == 429:
        raise SourceError("Trakt asks to slow down, try again later")
    if response.status_code >= 400:
        raise SourceError(f"Trakt returned {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise SourceError("Trakt sent no JSON") from exc


def list_ref(value: str) -> tuple[str, str]:
    """User and list slug from a Trakt list address or "user/list"."""
    text = (value or "").strip()
    found = _LIST_URL.search(text)
    if found:
        return found.group(1), found.group(2)
    parts = [part for part in text.strip("/").split("/") if part]
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) == 4 and parts[0] == "users" and parts[2] == "lists":
        return parts[1], parts[3]
    raise SourceError("List: paste the address, e.g. https://trakt.tv/users/name/lists/list-name")


def entry_of(row: dict, position: int, wanted: str | None) -> RawEntry | None:
    """Chart or list row -> raw entry; rows are either bare titles or wrap one under "movie"/"show"."""
    if row.get("type") in ("season", "episode", "person"):
        return None
    kind, data = None, row
    for name, mapped in (("movie", "movie"), ("show", "show")):
        if isinstance(row.get(name), dict):
            kind, data = mapped, row[name]
            break
    if kind is None:
        kind = wanted
    if kind is None or (wanted and kind != wanted) or not isinstance(data, dict):
        return None
    ids = data.get("ids") or {}
    return RawEntry(
        position=position,
        title=data.get("title") or "",
        year=int_or_none(data.get("year")),
        kind=kind,
        ids={name: ids.get(name) for name in ("tmdb", "imdb", "tvdb") if ids.get(name)},
        url=f"https://trakt.tv/{'movies' if kind == 'movie' else 'shows'}/{ids['slug']}" if ids.get("slug") else None,
        extra={"trakt": ids.get("trakt")},
    )


class Trakt(Adapter):
    key = "trakt"
    label = "Trakt"
    fields: ClassVar[list[Field]] = [
        Field("chart", "List", "select", options=list(CHARTS.items()), default="trending"),
        Field("type", "Type", "select", options=[("movies", "Movies"), ("shows", "Shows"), ("both", "Both")],
              default="movies"),
        Field("period", "Period", "select", options=list(PERIODS.items()), default="weekly",
              when={"chart": ["watched", "played", "collected"]}),
        Field("list", "List address", "text", default="", required=False,
              placeholder="https://trakt.tv/users/name/lists/list-name", when={"chart": ["list"]}),
        Field("limit", "Titles", "number", default=20, min=1, max=100),
    ]

    def missing(self) -> str | None:
        return None if configured() else "Set a Trakt client ID under Settings › Lists"

    def clean(self, params: dict) -> dict:
        clean = super().clean(params)
        if clean["chart"] == "list":
            user, slug = list_ref(clean["list"])
            clean["list"] = f"{user}/{slug}"
        else:
            clean["list"] = ""
        if clean["chart"] not in ("watched", "played", "collected"):
            clean["period"] = "weekly"
        if clean["chart"] == "boxoffice":
            clean["type"] = "movies"
        if clean["chart"] != "list" and clean["type"] == "both":
            raise SourceError("Type: charts are per type, choose Movies or Shows")
        return clean

    def describe(self, params: dict) -> str:
        if params["chart"] == "list":
            return f"Trakt list {params['list']}"
        kind = "movies" if params["type"] == "movies" else "shows"
        if params["chart"] in ("watched", "played", "collected"):
            return f"Trakt {CHARTS[params['chart']].lower()} {kind} – {PERIODS[params['period']].lower()}"
        if params["chart"] == "boxoffice":
            return "Trakt box office"
        return f"Trakt {CHARTS[params['chart']].lower()} {kind}"

    async def fetch(self, params: dict) -> Fetched:
        limit = params["limit"]
        if params["chart"] == "list":
            user, slug = params["list"].split("/", 1)
            wanted = {"movies": "movie", "shows": "show"}.get(params["type"])
            path = f"/users/{user}/lists/{slug}/items" + (f"/{wanted}" if wanted else "")
            rows = await get(path, {"limit": limit, "page": 1})
        else:
            wanted = "movie" if params["type"] == "movies" else "show"
            path = f"/{params['type']}/{params['chart']}"
            if params["chart"] in ("watched", "played", "collected"):
                path += f"/{params['period']}"
            rows = await get(path, {"limit": limit} if params["chart"] != "boxoffice" else None)
        entries: list[RawEntry] = []
        for row in rows if isinstance(rows, list) else []:
            entry = entry_of(row, len(entries) + 1, wanted)
            if entry is not None:
                entries.append(entry)
            if len(entries) >= limit:
                break
        return Fetched(entries)
