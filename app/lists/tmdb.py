"""TMDB charts and public TMDB lists."""
from __future__ import annotations

import re
from typing import ClassVar

from ..media import tmdb
from .base import Adapter, Fetched, Field, RawEntry, SourceError

CHARTS = {
    "trending-movie-day": ("Trending movies today", "/trending/movie/day", "movie"),
    "trending-movie-week": ("Trending movies this week", "/trending/movie/week", "movie"),
    "trending-show-day": ("Trending shows today", "/trending/tv/day", "show"),
    "trending-show-week": ("Trending shows this week", "/trending/tv/week", "show"),
    "popular-movie": ("Popular movies", "/movie/popular", "movie"),
    "top-rated-movie": ("Top rated movies", "/movie/top_rated", "movie"),
    "now-playing-movie": ("Movies in cinemas", "/movie/now_playing", "movie"),
    "upcoming-movie": ("Upcoming movies", "/movie/upcoming", "movie"),
    "popular-show": ("Popular shows", "/tv/popular", "show"),
    "top-rated-show": ("Top rated shows", "/tv/top_rated", "show"),
    "airing-today-show": ("Shows airing today", "/tv/airing_today", "show"),
    "on-the-air-show": ("Shows on the air", "/tv/on_the_air", "show"),
    "list": ("TMDB list", "", "any"),
}
_LIST = re.compile(r"(?:themoviedb\.org/list/)?(\d+)")


class Tmdb(Adapter):
    key = "tmdb"
    label = "TMDB"
    fields: ClassVar[list[Field]] = [
        Field("chart", "List", "select", options=[(key, value[0]) for key, value in CHARTS.items()],
              default="trending-movie-week"),
        Field("list", "List address or ID", "text", default="", required=False,
              placeholder="https://www.themoviedb.org/list/1234", when={"chart": ["list"]}),
        Field("limit", "Titles", "number", default=20, min=1, max=200),
    ]

    def missing(self) -> str | None:
        return None if tmdb.configured() else "Set a TMDB key under Settings › Sources"

    def clean(self, params: dict) -> dict:
        clean = super().clean(params)
        if clean["chart"] == "list":
            found = _LIST.search(clean["list"])
            if not found:
                raise SourceError("List: paste the address or the number")
            clean["list"] = found.group(1)
        else:
            clean["list"] = ""
        return clean

    def describe(self, params: dict) -> str:
        if params["chart"] == "list":
            return f"TMDB list {params['list']}"
        return f"TMDB: {CHARTS[params['chart']][0]}"

    async def fetch(self, params: dict) -> Fetched:
        _, path, kind = CHARTS[params["chart"]]
        if params["chart"] == "list":
            path = f"/list/{params['list']}"
        rows: list[dict] = []
        page_number, pages = 1, 1
        try:
            while len(rows) < params["limit"] and page_number <= min(pages, 20):
                found, pages = await tmdb.page(path, kind, page_number)
                if not found and page_number > 1:
                    break
                rows += found
                page_number += 1
        except tmdb.TmdbError as exc:
            raise SourceError(str(exc)) from exc
        if not rows and params["chart"] == "list":
            raise SourceError("TMDB list is empty or private")
        entries = [
            RawEntry(position=index + 1, title=row["title"], year=row["year"], kind=row["kind"],
                     ids={"tmdb": row["tmdb_id"]},
                     url=f"https://www.themoviedb.org/{'movie' if row['kind'] == 'movie' else 'tv'}/{row['tmdb_id']}")
            for index, row in enumerate(rows[: params["limit"]])
        ]
        return Fetched(entries)
