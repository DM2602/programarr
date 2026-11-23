"""FlixPatrol, MDBList and Trakt stand-ins, plus a TMDB catalog for the Netflix Switzerland TOP 10 of 23 Nov 2026.

FlixPatrol serves the saved pages from tests/fixtures/flixpatrol; title pages without a saved copy
are generated in the same markup (kind and premiere in the info grid).
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "flixpatrol"
SAVED_TITLES = {"the-empress": "title-the-empress.html", "the-grinch": "title-the-grinch.html"}

# FlixPatrol title pages: slug -> (kind as FlixPatrol shows it, premiere MM/DD/YYYY or None)
TITLE_PAGES = {
    "guarding-stars": ("Movie", "11/14/2026"),
    "steps": ("Movie", "11/21/2026"),
    "heartland-2026": ("Movie", "11/13/2026"),
    "the-christmas-switch-2026": ("Movie", "11/12/2026"),
    "woodwalkers-2": ("Movie", "10/23/2025"),
    "never-surrender": ("Movie", "11/12/2026"),
    "russell-brand-open-secret": ("Movie", "11/15/2026"),
    "how-to-make-a-killing-2026": ("Movie", "11/09/2026"),
    "a-dogs-perfect-christmas": ("Movie", "11/10/2026"),
    "the-law-of-beasts": ("TV Show", "11/18/2026"),
    "the-body-2026": ("TV Show", "11/11/2026"),
    "black-doves": ("TV Show", "12/05/2024"),
    "lupin": ("TV Show", "01/08/2021"),
    "chiaroscuro": ("TV Show", "11/14/2026"),
    "murder-in-galicia": ("TV Show", "11/13/2026"),
    "the-altruists": ("TV Show", "11/21/2026"),
    "alicias-law": ("TV Show", "11/12/2026"),
    "power": ("TV Show", "06/07/2014"),
}

# TMDB catalog (kind, id, title, date, extras). Each title tests one rule of the matcher.
CATALOG = [
    ("movie", 1001, "Guarding Stars", "2026-11-14", {"imdb": "tt9000001", "popularity": 80.0}),   # exact
    ("movie", 1002, "Steps", "2026-11-21", {"imdb": "tt9000002", "popularity": 60.0}),
    ("movie", 1095, "Steps", "1998-03-01", {"popularity": 2.0}),                                    # older namesake
    ("movie", 1003, "Heartland", "2026-11-13", {"imdb": "tt9000003", "popularity": 45.0}),
    ("movie", 1093, "Heartland", "1979-06-01", {"popularity": 3.0}),
    ("tv", 2093, "Heartland", "2007-10-14", {"popularity": 90.0}),                                  # a show: wrong kind
    ("movie", 1004, "The Christmas Switch", "2025-12-20", {"popularity": 30.0}),                    # year ±1
    ("movie", 360920, "The Grinch", "2018-11-08", {"imdb": "tt2709692", "popularity": 70.0}),
    ("movie", 1006, "Woodwalkers 2", "2025-10-23", {"original": "Woodwalkers 2", "popularity": 22.0}),
    ("movie", 1007, "Hill 12", "2026-11-12", {"alt": ("Never Surrender",), "popularity": 18.0}),    # alternative title
    ("movie", 1008, "Russell Brand – Open Secret", "2026-11-15", {"popularity": 15.0}),             # punctuation
    ("movie", 1009, "How to Make a Killing", "2026-11-09", {"popularity": 12.0}),                   # two of them
    ("movie", 1010, "How to Make a Killing", "2026-03-02", {"popularity": 9.5}),
    ("movie", 1011, "A Perfect Dog Christmas", "2026-11-10", {"popularity": 11.0}),                 # only similar
    ("tv", 2001, "The Law of Beasts", "2026-11-18", {"tvdb": 480001, "popularity": 55.0}),
    ("tv", 2002, "The Body", "2026-11-11", {"tvdb": 480002, "popularity": 40.0}),
    ("tv", 2092, "The Body", "2015-02-01", {"popularity": 5.0}),
    ("tv", 2003, "Black Doves", "2024-12-05", {"imdb": "tt27646728", "tvdb": 450003, "popularity": 66.0}),
    ("tv", 96677, "Lupin", "2021-01-08", {"imdb": "tt2531336", "tvdb": 383365, "popularity": 50.0}),
    ("tv", 2005, "The Empress", "2022-09-29", {"original": "Die Kaiserin", "tvdb": 420005, "popularity": 35.0}),
    ("tv", 2006, "ChiaroScuro", "2026-11-14", {"popularity": 25.0}),
    ("tv", 2007, "Murder in Galicia", "2026-11-13", {"popularity": 21.0}),
    ("tv", 2009, "Alicia's Law", "2026-11-12", {"popularity": 19.0}),
    ("tv", 2010, "Power", "2014-06-07", {"imdb": "tt3281796", "tvdb": 276562, "popularity": 48.0}),
]
# The Altruists (show) is missing on purpose: not found.

EXPECTED = {
    # raw title -> (status, method, TMDB id or None)
    "Guarding Stars": ("matched", "exact", 1001),
    "Steps": ("matched", "exact", 1002),
    "Heartland": ("matched", "exact", 1003),
    "The Christmas Switch": ("matched", "year±1", 1004),
    "The Grinch": ("matched", "exact", 360920),
    "Woodwalkers 2": ("matched", "exact", 1006),
    "Never Surrender": ("matched", "alt-title", 1007),
    "Russell Brand: Open Secret": ("matched", "exact", 1008),
    "How to Make a Killing": ("uncertain", "exact", None),
    "A Dog's Perfect Christmas": ("uncertain", "similar", None),
    "The Law of Beasts": ("matched", "exact", 2001),
    "The Body": ("matched", "exact", 2002),
    "Black Doves": ("matched", "exact", 2003),
    "Lupin": ("matched", "exact", 96677),
    "The Empress": ("matched", "exact", 2005),
    "ChiaroScuro": ("matched", "exact", 2006),
    "Murder in Galicia": ("matched", "exact", 2007),
    "The Altruists": ("failed", None, None),
    "Alicia's Law": ("matched", "exact", 2009),
    "Power": ("matched", "exact", 2010),
}


def fill_tmdb(tmdb) -> None:
    for kind, tmdb_id, title, date, extra in CATALOG:
        tmdb.add_title(kind, tmdb_id, title, date, **extra)


def title_page(slug: str, kind: str, premiere: str | None) -> str:
    """Same structure as a saved FlixPatrol title page, reduced to the info grid."""
    month_day, year = (premiere[:6], premiere[6:]) if premiere else ("", "")
    premiere_block = (
        f'<div class="flex gap-x-1" title="Premiere"><div><span class="text-gray-600">{month_day}</span>{year}</div>'
        '<div class="text-gray-600 select-none">|</div></div>' if premiere else ""
    )
    name = slug.replace("-", " ").title()
    ld = json.dumps({"@context": "https://schema.org", "@type": "Movie", "name": name})
    return (
        f'<!doctype html><html><head><title>{name} | FlixPatrol</title>'
        f'<script type="application/ld+json">{ld}</script></head><body>'
        f'<div class="info-grid"><div class="info-grid-header"><h1 class="mb-4 text-h1">{name}</h1>'
        '<div class="flex flex-wrap gap-x-1 text-sm">'
        f'<div class="flex gap-x-1" title="{abs(hash(slug)) % 900000 + 1000}"><div>{kind}</div>'
        '<div class="text-gray-600 select-none">|</div></div>'
        '<div class="flex gap-x-1"><div class="flex gap-x-1 items-center"><span class="fflag ff-sm fflag-US"></span>'
        ' <span>United States</span></div></div>'
        f'{premiere_block}</div></div></div>'
        '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script></body></html>'
    )


CHALLENGE = ("<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
             "<div id='cf-chl-widget'>Checking your browser</div></body></html>")


class FakeFlixPatrol:
    def __init__(self):
        self.challenge = False  # answer with a Cloudflare check unless the clearance cookie is sent
        self.requests: list[str] = []
        self.app = self._make_app()

    def _make_app(self) -> FastAPI:
        app = FastAPI()
        fake = self

        @app.middleware("http")
        async def cloudflare(request: Request, call_next):
            fake.requests.append(request.url.path)
            if fake.challenge and request.cookies.get("cf_clearance") != "ok":
                return HTMLResponse(CHALLENGE, status_code=403)
            return await call_next(request)

        @app.get("/top10/{platform}/{region}/")
        async def top10(platform: str, region: str):
            if (platform, region) != ("netflix", "switzerland"):
                return HTMLResponse("<h1>404</h1>", status_code=404)
            return HTMLResponse((FIXTURES / "top10-netflix-switzerland.html").read_text(encoding="utf-8"))

        @app.get("/title/{slug}/")
        async def title(slug: str):
            if slug in SAVED_TITLES:
                return HTMLResponse((FIXTURES / SAVED_TITLES[slug]).read_text(encoding="utf-8"))
            if slug in TITLE_PAGES:
                return HTMLResponse(title_page(slug, *TITLE_PAGES[slug]))
            return HTMLResponse("<h1>404</h1>", status_code=404)

        return app


class FakeMdblist:
    def __init__(self, key: str = "mdb-key"):
        self.key = key
        self.batch = True  # False: batch requests are refused (403), as for accounts without it
        self.calls: list[str] = []
        self.lists: dict[str, dict] = {}  # "user/slug" -> {"movies": [...], "shows": [...]}
        self.ratings: dict[str, dict] = {}  # "movie:603" -> {"imdb": (7.5, 1200), "tomatoes": 88, "popcorn": 91}
        self.app = self._make_app()

    def _media(self, kind: str, tmdb_id: int) -> dict | None:
        values = self.ratings.get(f"{kind}:{tmdb_id}")
        if values is None:
            return None
        ratings = []
        if "imdb" in values:
            ratings.append({"source": "imdb", "value": values["imdb"][0], "score": int(values["imdb"][0] * 10),
                            "votes": values["imdb"][1]})
        if "tomatoes" in values:
            ratings.append({"source": "tomatoes", "value": values["tomatoes"], "score": values["tomatoes"], "votes": 50})
        if "popcorn" in values:
            ratings.append({"source": "popcorn", "value": values["popcorn"], "score": values["popcorn"], "votes": 900})
        ratings.append({"source": "metacritic", "value": None, "score": None, "votes": None})
        return {"title": "x", "type": kind, "ids": {"tmdb": tmdb_id, "imdb": None}, "ratings": ratings}

    def _make_app(self) -> FastAPI:
        app = FastAPI()
        fake = self

        @app.middleware("http")
        async def auth(request: Request, call_next):
            fake.calls.append(f"{request.method} {request.url.path}")
            if request.query_params.get("apikey") != fake.key:
                return JSONResponse({"error": "Invalid API key"}, status_code=403)
            return await call_next(request)

        @app.get("/user")
        async def user():
            return {"user_id": 7, "username": "dominik", "api_requests": 1000, "api_requests_count": len(fake.calls),
                    "patron_status": "free"}

        @app.get("/lists/user")
        async def my_lists():
            return [{"id": 11, "name": "Netflix CH", "slug": "netflix-ch", "user_name": "dominik", "items": 3,
                     "dynamic": False, "mediatype": "movie", "likes": 0}]

        @app.get("/lists/{user}/{slug}/items")
        async def items(user: str, slug: str, request: Request):
            data = fake.lists.get(f"{user}/{slug}")
            if data is None:
                return JSONResponse({"error": "List not found"}, status_code=404)
            limit = int(request.query_params.get("limit") or 1000)
            return {"movies": data.get("movies", [])[:limit], "shows": data.get("shows", [])[:limit]}

        @app.post("/tmdb/{kind}")
        async def batch(kind: str, request: Request):
            if not fake.batch:
                return JSONResponse({"error": "Batch requests need a supporter account"}, status_code=403)
            body = await request.json()
            return [media for tmdb_id in body.get("ids", []) if (media := fake._media(kind, int(tmdb_id)))]

        @app.get("/tmdb/{kind}/{tmdb_id}")
        async def single(kind: str, tmdb_id: int):
            media = fake._media(kind, tmdb_id)
            return media if media else JSONResponse({"error": "not found"}, status_code=404)

        return app


class FakeTrakt:
    def __init__(self, client_id: str = "trakt-client"):
        self.client_id = client_id
        self.trending: list[dict] = []
        self.lists: dict[str, list[dict]] = {}
        self.app = self._make_app()

    def _make_app(self) -> FastAPI:
        app = FastAPI()
        fake = self

        @app.middleware("http")
        async def auth(request: Request, call_next):
            if request.headers.get("trakt-api-key") != fake.client_id or request.headers.get("trakt-api-version") != "2":
                return JSONResponse({"error": "invalid api key"}, status_code=403)
            return await call_next(request)

        @app.get("/movies/trending")
        async def trending(request: Request):
            limit = int(request.query_params.get("limit") or 10)
            return fake.trending[:limit]

        @app.get("/users/{user}/lists/{slug}/items")
        async def items(user: str, slug: str, request: Request):
            return await typed_items(user, slug, "", request)

        @app.get("/users/{user}/lists/{slug}/items/{kind}")
        async def typed_items(user: str, slug: str, kind: str, request: Request):
            if f"{user}/{slug}" not in fake.lists:
                return JSONResponse({"error": "not found"}, status_code=404)
            rows = [row for row in fake.lists[f"{user}/{slug}"] if not kind or row.get("type") == kind]
            return rows[: int(request.query_params.get("limit") or 1000)]

        return app
