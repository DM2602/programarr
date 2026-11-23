"""TMDB, fanart.tv, TVmaze, Discord and FlareSolverr stand-ins, plus the router."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .images import poster_for


class FakeTmdb:
    def __init__(self, key: str = "tmdb-key", image_base: str = "https://tmdb-img.test/t/p"):
        self.key = key
        self.image_base = image_base
        self.titles: dict[str, dict] = {}  # "movie:603" -> {"title":..., "date":...}
        self.app = self._make_app()
        self.images_app = self._make_images()

    def add(self, kind: str, tmdb: str, title: str, date: str) -> None:
        self.titles[f"{kind}:{tmdb}"] = {"title": title, "date": date}

    def _make_app(self) -> FastAPI:
        app = FastAPI()
        fake = self

        @app.middleware("http")
        async def auth(request: Request, call_next):
            ok = request.query_params.get("api_key") == fake.key or \
                request.headers.get("authorization") == f"Bearer {fake.key}"
            if not ok:
                return JSONResponse({"status_message": "Invalid API key"}, status_code=401)
            return await call_next(request)

        @app.get("/3/configuration")
        async def configuration():
            return {"images": {"base_url": fake.image_base}}

        @app.get("/3/{kind}/{tmdb}/images")
        async def images(kind: str, tmdb: str):
            return {"posters": [
                {"file_path": f"/{kind}-{tmdb}-de.jpg", "iso_639_1": "de", "vote_average": 5.2, "vote_count": 3,
                 "width": 2000, "height": 3000},
                {"file_path": f"/{kind}-{tmdb}-en.jpg", "iso_639_1": "en", "vote_average": 5.6, "vote_count": 9,
                 "width": 2000, "height": 3000},
                {"file_path": f"/{kind}-{tmdb}-xx.jpg", "iso_639_1": None, "vote_average": 5.0, "vote_count": 1,
                 "width": 1000, "height": 1500},
            ]}

        @app.get("/3/tv/{tmdb}/season/{number}/images")
        async def season_images(tmdb: str, number: int):
            return {"posters": [{"file_path": f"/tv-{tmdb}-s{number}.jpg", "iso_639_1": "en", "vote_average": 5,
                                 "vote_count": 2, "width": 1000, "height": 1500}]}

        @app.get("/3/{kind}/{tmdb}")
        async def details(kind: str, tmdb: str):
            entry = fake.titles.get(f"{kind}:{tmdb}")
            if not entry:
                return JSONResponse({"status_message": "not found"}, status_code=404)
            if kind == "movie":
                return {"id": int(tmdb), "title": entry["title"], "release_date": entry["date"]}
            return {"id": int(tmdb), "name": entry["title"], "first_air_date": entry["date"]}

        @app.get("/3/find/{external}")
        async def find(external: str):
            return {"movie_results": [], "tv_results": []}

        return app

    def _make_images(self) -> FastAPI:
        app = FastAPI()

        @app.get("/t/p/{size}/{name}")
        async def image(size: str, name: str):
            return Response(poster_for(f"tmdb-{name}", "TMDB", thumb=size != "original"), media_type="image/jpeg")

        return app


class FakeFanart:
    def __init__(self, key: str = "fanart-key", assets: str = "https://fanart-assets.test"):
        self.key = key
        self.assets = assets
        self.app = self._make_app()
        self.assets_app = self._make_assets()

    def _make_app(self) -> FastAPI:
        app = FastAPI()
        fake = self

        @app.get("/v3/movies/{ident}")
        async def movie(ident: str, request: Request):
            if request.query_params.get("api_key") != fake.key:
                return JSONResponse({"status": "error"}, status_code=401)
            return {"name": "Film", "tmdb_id": ident, "movieposter": [
                {"id": "11", "url": f"{fake.assets}/fanart/movies/{ident}/movieposter/a.jpg", "lang": "00", "likes": "4"},
                {"id": "12", "url": f"{fake.assets}/fanart/movies/{ident}/movieposter/b.jpg", "lang": "de", "likes": "1"},
            ]}

        @app.get("/v3/tv/{ident}")
        async def tv(ident: str, request: Request):
            if request.query_params.get("api_key") != fake.key:
                return JSONResponse({"status": "error"}, status_code=401)
            return {"name": "Serie", "thetvdb_id": ident,
                    "tvposter": [{"id": "21", "url": f"{fake.assets}/fanart/tv/{ident}/tvposter/a.jpg", "lang": "en", "likes": "2"}],
                    "seasonposter": [
                        {"id": "31", "url": f"{fake.assets}/fanart/tv/{ident}/seasonposter/s1.jpg", "lang": "00", "likes": "1", "season": "1"},
                        {"id": "32", "url": f"{fake.assets}/fanart/tv/{ident}/seasonposter/s2.jpg", "lang": "en", "likes": "0", "season": "2"},
                    ]}

        return app

    def _make_assets(self) -> FastAPI:
        app = FastAPI()

        @app.get("/{kind}/{rest:path}")
        async def asset(kind: str, rest: str):
            return Response(poster_for(f"fanart-{rest}", "FANART", thumb=kind == "preview"), media_type="image/jpeg")

        return app


class FakeTvmaze:
    def __init__(self, base: str = "https://tvmaze.test"):
        self.base = base
        self.shows: dict[str, str] = {}  # tvdb -> tvmaze id
        self.status: dict[str, str] = {}  # tvdb -> "Ended" / "Running"
        self.lookups = 0
        self.app = self._make_app()

    def _make_app(self) -> FastAPI:
        app = FastAPI()
        fake = self

        @app.get("/lookup/shows")
        async def lookup(request: Request):
            tvdb = request.query_params.get("thetvdb")
            fake.lookups += 1
            if tvdb in fake.shows:
                return {"id": int(fake.shows[tvdb]), "name": "Serie", "status": fake.status.get(tvdb, "Running")}
            return JSONResponse({}, status_code=404)

        @app.get("/shows/{ident}/images")
        async def images(ident: str):
            return [{"id": 91, "type": "poster", "main": True, "resolutions": {
                "original": {"url": f"{fake.base}/img/{ident}-poster.jpg", "width": 680, "height": 1000},
                "medium": {"url": f"{fake.base}/img/{ident}-poster-m.jpg", "width": 210, "height": 295}}},
                {"id": 92, "type": "background", "main": False, "resolutions": {"original": {"url": f"{fake.base}/img/bg.jpg"}}}]

        @app.get("/shows/{ident}/seasons")
        async def seasons(ident: str):
            return [{"id": 100 + n, "number": n, "image": {"medium": f"{fake.base}/img/{ident}-s{n}-m.jpg",
                                                           "original": f"{fake.base}/img/{ident}-s{n}.jpg"}}
                    for n in (1, 2)]

        @app.get("/img/{name}")
        async def image(name: str):
            return Response(poster_for(f"tvmaze-{name}", "TVMAZE", thumb=name.endswith("-m.jpg")), media_type="image/jpeg")

        return app


class FakeDiscord:
    def __init__(self):
        self.messages: list[dict] = []
        self.app = self._make_app()

    def _make_app(self) -> FastAPI:
        app = FastAPI()
        fake = self

        @app.post("/api/webhooks/{hook_id}/{token}")
        async def webhook(request: Request):
            content_type = request.headers.get("content-type", "")
            if content_type.startswith("multipart/"):
                form = await request.form()
                payload = json.loads(form["payload_json"])
                files = [name for name in form.keys() if name.startswith("files[")]
                payload["_files"] = files
            else:
                payload = await request.json()
                payload["_files"] = []
            fake.messages.append(payload)
            return Response(status_code=204)

        return app

    def titles(self) -> list[str]:
        return [m["embeds"][0]["title"] for m in self.messages]


class FakeFlareSolverr:
    """Fetches through the TPDB fake as if the browser had solved the challenge."""

    def __init__(self, tpdb_app):
        self.tpdb_app = tpdb_app
        self.calls = 0
        self.app = self._make_app()

    def _make_app(self) -> FastAPI:
        app = FastAPI()
        fake = self

        @app.post("/v1")
        async def v1(request: Request):
            body = await request.json()
            fake.calls += 1
            url = body["url"]
            cookies = {c["name"]: c["value"] for c in body.get("cookies", [])}
            cookies["cf_clearance"] = "ok"
            transport = httpx.ASGITransport(app=fake.tpdb_app)
            async with httpx.AsyncClient(transport=transport, cookies=cookies, follow_redirects=True) as client:
                response = await client.get(url)
            host = urlsplit(url).hostname
            return {"status": "ok", "message": "", "solution": {
                "url": str(response.url), "status": response.status_code, "response": response.text,
                "cookies": [{"name": "cf_clearance", "value": "ok", "domain": host, "path": "/"}],
                "userAgent": "Mozilla/5.0 (FlareSolverr)"}}

        return app


class Router(httpx.AsyncBaseTransport):
    """Send each request to the simulated service for its host."""

    def __init__(self, apps: dict[str, object]):
        self.transports = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}
        self.log: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.log.append(f"{request.method} {host}{request.url.path}")
        transport = self.transports.get(host)
        if transport is None:
            return httpx.Response(502, text=f"no fake for {host}", request=request)
        return await transport.handle_async_request(request)
