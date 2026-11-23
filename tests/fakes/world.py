"""All simulated services together, wired into Programarr's settings."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Response

from app import db, settings
from app.posters import library

from .images import poster_for
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
        self.flare = FakeFlareSolverr(self.tpdb.app)
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
        }
        values.update(overrides)
        settings.update(values)

    async def enable_libraries(self) -> None:
        await library.refresh_libraries()
        db.get().execute("UPDATE libraries SET enabled=1")
