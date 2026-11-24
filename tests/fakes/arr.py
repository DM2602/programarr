"""Radarr and Sonarr stand-ins: system status and the lookups by TMDB or TVDB ID."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class FakeArr:
    def __init__(self, service: str, key: str):
        self.service = service  # radarr | sonarr
        self.key = key
        self.titles: list[dict] = []
        self.app = self._make_app()

    def add_movie(self, tmdb: int, title: str, slug: str, has_file: bool = True, monitored: bool = True) -> None:
        self.titles.append({"id": len(self.titles) + 1, "tmdbId": tmdb, "title": title, "titleSlug": slug,
                            "hasFile": has_file, "monitored": monitored})

    def add_series(self, tvdb: int, title: str, slug: str, episodes: int = 10, files: int = 10) -> None:
        self.titles.append({"id": len(self.titles) + 1, "tvdbId": tvdb, "title": title, "titleSlug": slug,
                            "monitored": True, "statistics": {"episodeCount": episodes, "episodeFileCount": files}})

    def _make_app(self) -> FastAPI:
        app = FastAPI()
        fake = self

        @app.middleware("http")
        async def auth(request: Request, call_next):
            if request.headers.get("x-api-key") != fake.key:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return await call_next(request)

        @app.get("/api/v3/system/status")
        async def status():
            name = "Radarr" if fake.service == "radarr" else "Sonarr"
            return {"appName": name, "version": "5.14.0" if fake.service == "radarr" else "4.0.11"}

        @app.get("/api/v3/movie")
        async def movies(request: Request):
            tmdb = request.query_params.get("tmdbId")
            return [row for row in fake.titles if not tmdb or str(row.get("tmdbId")) == tmdb]

        @app.get("/api/v3/series")
        async def series(request: Request):
            tvdb = request.query_params.get("tvdbId")
            return [row for row in fake.titles if not tvdb or str(row.get("tvdbId")) == tvdb]

        return app
