"""All simulated services together, wired into Programarr's settings."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Response

from app import db, settings
from app.posters import library

from .images import poster_for
from .lists import FakeFlixPatrol, FakeMdblist, FakeTrakt, fill_tmdb
from .mediux import FakeMediux
from .others import FakeDiscord, FakeFanart, FakeFlareSolverr, FakeTmdb, FakeTvmaze, Router
from .plex import FakePlex
from .tpdb import FakeTpdb

DISCORD_URL = "https://discord.test/api/webhooks/1/abc"


def _agent_images() -> FastAPI:
    app = FastAPI()

    @app.get("/agent/{name}")
    async def agent(name: str):
        return Response(poster_for(f"agent-{name}", "AGENT"), media_type="image/jpeg")

    return app


class World:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.media = self.root / "media"
        self.plex = FakePlex(self.media)
        self.mediux = FakeMediux()
        self.tpdb = FakeTpdb()
        self.tmdb = FakeTmdb()
        self.fanart = FakeFanart()
        self.tvmaze = FakeTvmaze()
        self.discord = FakeDiscord()
        self.flixpatrol = FakeFlixPatrol()
        self.mdblist = FakeMdblist()
        self.trakt = FakeTrakt()
        self.flare = FakeFlareSolverr({"tpdb.test": self.tpdb.app, "flixpatrol.test": self.flixpatrol.app})
        self.apps = {
            "plex.test": self.plex.app,
            "mediux.test": self.mediux.app,
            "tpdb.test": self.tpdb.app,
            "tmdb.test": self.tmdb.app,
            "tmdb-img.test": self.tmdb.images_app,
            "tmdb-images.test": _agent_images(),
            "fanart.test": self.fanart.app,
            "fanart-assets.test": self.fanart.assets_app,
            "tvmaze.test": self.tvmaze.app,
            "discord.test": self.discord.app,
            "flaresolverr.test": self.flare.app,
            "flixpatrol.test": self.flixpatrol.app,
            "mdblist.test": self.mdblist.app,
            "trakt.test": self.trakt.app,
        }
        self.router = Router(self.apps)

    def configure(self, **overrides) -> None:
        values = {
            "setup_done": True,
            "plex_url": "https://plex.test",
            "plex_token": self.plex.token,
            "path_maps": [{"plex": "/data/media", "local": str(self.media)}],
            "mediux_token": self.mediux.token,
            "mediux_api": "https://mediux.test",
            "mediux_site": "https://mediux-site.test",
            "tpdb_base": "https://tpdb.test",
            "tpdb_email": self.tpdb.email,
            "tpdb_password": self.tpdb.password,
            "tpdb_delay": 0,
            "tmdb_key": self.tmdb.key,
            "tmdb_api": "https://tmdb.test/3",
            "tmdb_image": "https://tmdb-img.test/t/p",
            "fanart_key": self.fanart.key,
            "fanart_api": "https://fanart.test/v3",
            "tvmaze_api": "https://tvmaze.test",
            "discord_webhook": DISCORD_URL,
            "designers": [{"source": "any", "name": "willtong93"}, {"source": "tpdb", "name": "DIIIVOY"}],
            "use_mediux_follows": False,
            "automation": True,
            "flixpatrol_base": "https://flixpatrol.test",
            "flixpatrol_delay": 0,
            "mdblist_api": "https://mdblist.test",
            "mdblist_key": self.mdblist.key,
            "trakt_api": "https://trakt.test",
            "trakt_client_id": self.trakt.client_id,
        }
        values.update(overrides)
        settings.update(values)

    def fill_lists(self) -> None:
        """TMDB titles for the Netflix Switzerland TOP 10, ratings on MDBList, a Trakt chart and lists."""
        fill_tmdb(self.tmdb)
        self.mdblist.ratings.update({
            "movie:1001": {"imdb": (6.4, 5300), "tomatoes": 71, "popcorn": 64},
            "movie:360920": {"imdb": (6.3, 140000), "tomatoes": 60, "popcorn": 61},
            "show:2003": {"imdb": (7.2, 41000), "tomatoes": 88},
            "show:96677": {"imdb": (7.5, 160000), "tomatoes": 95, "popcorn": 89},
        })
        self.mdblist.lists["dominik/netflix-ch"] = {
            "movies": [
                {"id": 360920, "rank": 1, "title": "The Grinch", "imdb_id": "tt2709692", "mediatype": "movie",
                 "release_year": 2018},
                # MDBList's TMDB id is wrong here; the IMDb id decides.
                {"id": 1002, "rank": 3, "title": "Guarding Stars", "imdb_id": "tt9000001", "mediatype": "movie",
                 "release_year": 2026},
            ],
            "shows": [
                {"id": 96677, "rank": 2, "title": "Lupin", "imdb_id": "tt2531336", "tvdb_id": 383365,
                 "mediatype": "show", "release_year": 2021},
            ],
        }
        movie = lambda tmdb, title, year, imdb=None: {"title": title, "year": year,  # noqa: E731
                                                      "ids": {"trakt": tmdb + 5, "slug": title.lower().replace(" ", "-"),
                                                              "imdb": imdb, "tmdb": tmdb}}
        self.trakt.trending = [
            {"watchers": 50, "movie": movie(1001, "Guarding Stars", 2026, "tt9000001")},
            {"watchers": 40, "movie": movie(360920, "The Grinch", 2018, "tt2709692")},
            {"watchers": 30, "movie": movie(1003, "Heartland", 2026)},
        ]
        self.trakt.lists["dominik/christmas"] = [
            {"rank": 1, "type": "movie", "movie": movie(360920, "The Grinch", 2018, "tt2709692")},
            {"rank": 2, "type": "season", "season": {"number": 1}, "show": {"title": "Lupin", "year": 2021,
                                                                            "ids": {"tmdb": 96677}}},
            {"rank": 3, "type": "show", "show": {"title": "Lupin", "year": 2021,
                                                 "ids": {"trakt": 9, "slug": "lupin", "tmdb": 96677, "tvdb": 383365}}},
        ]
        self.tmdb.charts["/trending/movie/week"] = ["movie:1001", "movie:1002", "movie:360920"]
        self.tmdb.charts["/tv/popular"] = ["tv:2003", "tv:96677"]
        self.tmdb.lists["8001"] = ["movie:360920", "tv:2005"]

    async def enable_libraries(self) -> None:
        await library.refresh_libraries()
        db.get().execute("UPDATE libraries SET enabled=1")
