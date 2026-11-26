"""Radarr and Sonarr stand-ins: status, lookups, profiles, root folders, tags, exclusions, adding titles."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class FakeArr:
    def __init__(self, service: str, key: str):
        self.service = service  # radarr | sonarr
        self.key = key
        self.titles: list[dict] = []
        self.profiles = [{"id": 1, "name": "HD-1080p"}, {"id": 4, "name": "Ultra-HD"}]
        self.root_folders = [{"id": 1, "path": "/data/movies" if service == "radarr" else "/data/tv",
                              "freeSpace": 4_000_000_000_000}]
        self.tags: list[dict] = [{"id": 1, "label": "kids"}]
        self.exclusions: list[dict] = []  # {"id", "external", "title", "year"}: TMDB ids (Radarr), TVDB ids (Sonarr)
        self.paged = True  # False: an older version without /paged
        self.down = False  # True: answers 503, as when the service is unreachable
        self.known: dict[int, dict] = {}  # lookup results by TMDB id (Radarr) or TVDB id (Sonarr)
        self.lookups: list[str] = []  # Sonarr lookup terms asked for
        self.added: list[dict] = []  # POST bodies
        self.edits: list[dict] = []  # PUT .../editor bodies
        self.writes = 0
        self.fail_add: str | None = None
        self.app = self._make_app()

    def exclude(self, external: int, title: str = "", year: int | None = None) -> None:
        number = max([row["id"] for row in self.exclusions] or [0]) + 1
        self.exclusions.append({"id": number, "external": external, "title": title or f"Excluded {external}",
                                "year": year})

    @property
    def excluded(self) -> list[int]:
        return [row["external"] for row in self.exclusions]

    def add_movie(self, tmdb: int, title: str, slug: str, has_file: bool = True, monitored: bool = True,
                  tags: list[int] = ()) -> None:
        self.titles.append({"id": len(self.titles) + 1, "tmdbId": tmdb, "title": title, "titleSlug": slug,
                            "hasFile": has_file, "monitored": monitored, "tags": list(tags),
                            "qualityProfileId": 1})

    def add_series(self, tvdb: int, title: str, slug: str, episodes: int = 10, files: int = 10,
                   seasons: list[dict] = (), tags: list[int] = ()) -> None:
        self.titles.append({"id": len(self.titles) + 1, "tvdbId": tvdb, "title": title, "titleSlug": slug,
                            "monitored": True, "statistics": {"episodeCount": episodes, "episodeFileCount": files},
                            "seasons": list(seasons), "tags": list(tags), "qualityProfileId": 1})

    def know(self, external: int, title: str, year: int, tmdb: int | None = None, imdb: str | None = None) -> None:
        """A title the service's metadata lookup finds; for Sonarr also by TMDB or IMDb ID, as TVDB links them."""
        slug = f"{title.lower().replace(' ', '-')}-{external}"
        row = {"title": title, "year": year, "titleSlug": slug, "images": [], "added": "0001-01-01T00:00:00Z"}
        if self.service == "radarr":
            row["tmdbId"] = external
        else:
            row.update(tvdbId=external, tmdbId=tmdb, imdbId=imdb, seasons=[{"seasonNumber": 1, "monitored": False}])
        self.known[external] = row

    def tag_labels(self, row: dict) -> list[str]:
        names = {tag["id"]: tag["label"] for tag in self.tags}
        return sorted(names[tag] for tag in row.get("tags", []))

    def _make_app(self) -> FastAPI:
        app = FastAPI()
        fake = self
        id_key = "tmdbId" if self.service == "radarr" else "tvdbId"

        @app.middleware("http")
        async def auth(request: Request, call_next):
            if request.headers.get("x-api-key") != fake.key:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return await call_next(request)

        @app.get("/api/v3/system/status")
        async def status():
            name = "Radarr" if fake.service == "radarr" else "Sonarr"
            return {"appName": name, "version": "5.14.0" if fake.service == "radarr" else "4.0.11"}

        @app.get("/api/v3/qualityprofile")
        async def profiles():
            return fake.profiles

        @app.get("/api/v3/rootfolder")
        async def root_folders():
            return fake.root_folders

        @app.get("/api/v3/tag")
        async def tags():
            return fake.tags

        @app.post("/api/v3/tag")
        async def new_tag(request: Request):
            body = await request.json()
            fake.writes += 1
            row = {"id": max([tag["id"] for tag in fake.tags] or [0]) + 1, "label": body["label"]}
            fake.tags.append(row)
            return row

        path = "/api/v3/exclusions" if self.service == "radarr" else "/api/v3/importlistexclusion"

        def shown(row: dict) -> dict:
            if fake.service == "radarr":
                return {"id": row["id"], "tmdbId": row["external"], "movieTitle": row["title"],
                        "movieYear": row["year"] or 0}
            return {"id": row["id"], "tvdbId": row["external"], "title": row["title"]}

        @app.get(path)
        async def exclusions():
            if fake.down:
                return JSONResponse({"message": "Unavailable"}, status_code=503)
            return [shown(row) for row in fake.exclusions]

        @app.get(path + "/paged")
        async def exclusions_paged(request: Request):
            if not fake.paged:
                return JSONResponse({"message": "NotFound"}, status_code=404)
            if fake.down:
                return JSONResponse({"message": "Unavailable"}, status_code=503)
            page = int(request.query_params.get("page", 1))
            size = int(request.query_params.get("pageSize", 10))
            rows = fake.exclusions[(page - 1) * size: page * size]
            return {"page": page, "pageSize": size, "totalRecords": len(fake.exclusions),
                    "records": [shown(row) for row in rows]}

        @app.post(path)
        async def new_exclusion(request: Request):
            if fake.down:
                return JSONResponse({"message": "Unavailable"}, status_code=503)
            body = await request.json()
            external = body.get("tmdbId") if fake.service == "radarr" else body.get("tvdbId")
            if any(row["external"] == external for row in fake.exclusions):
                return JSONResponse([{"errorMessage": "This exclusion has already been added"}], status_code=400)
            fake.writes += 1
            fake.exclude(int(external), body.get("movieTitle") or body.get("title") or "", body.get("movieYear"))
            return shown(fake.exclusions[-1])

        @app.delete(path + "/{number}")
        async def drop_exclusion(number: int):
            if fake.down:
                return JSONResponse({"message": "Unavailable"}, status_code=503)
            before = len(fake.exclusions)
            fake.exclusions = [row for row in fake.exclusions if row["id"] != number]
            if len(fake.exclusions) == before:
                return JSONResponse({"message": "NotFound"}, status_code=404)
            fake.writes += 1
            return {}

        def matching(request: Request) -> list[dict]:
            wanted = request.query_params.get(id_key)
            return [row for row in fake.titles if not wanted or str(row.get(id_key)) == wanted]

        def add(body: dict) -> JSONResponse | dict:
            missing = [key for key in ("title", id_key, "qualityProfileId", "rootFolderPath") if not body.get(key)]
            if missing:
                return JSONResponse([{"propertyName": key, "errorMessage": f"'{key}' must not be empty."}
                                     for key in missing], status_code=400)
            if any(row.get(id_key) == body[id_key] for row in fake.titles):
                what = "movie" if fake.service == "radarr" else "series"
                return JSONResponse([{"propertyName": id_key, "errorMessage": f"This {what} has already been added"}],
                                    status_code=400)
            if fake.fail_add:
                return JSONResponse([{"errorMessage": fake.fail_add}], status_code=400)
            fake.writes += 1
            fake.added.append(body)
            row = {**body, "id": len(fake.titles) + 1}
            fake.titles.append(row)
            return row

        def edit(body: dict, ids_key: str) -> dict:
            fake.writes += 1
            fake.edits.append(body)
            for row in fake.titles:
                if row["id"] in body.get(ids_key, []) and body.get("applyTags") == "add":
                    row["tags"] = sorted(set(row.get("tags", [])) | set(body.get("tags", [])))
            return {}

        if self.service == "radarr":
            @app.get("/api/v3/movie")
            async def movies(request: Request):
                return matching(request)

            @app.get("/api/v3/movie/lookup/tmdb")
            async def lookup_movie(request: Request):
                tmdb = int(request.query_params.get("tmdbId", 0))
                found = fake.known.get(tmdb)
                if found is None:
                    return JSONResponse({"message": "NotFound"}, status_code=404)
                return found

            @app.post("/api/v3/movie")
            async def add_movie(request: Request):
                return add(await request.json())

            @app.put("/api/v3/movie/editor")
            async def edit_movies(request: Request):
                return edit(await request.json(), "movieIds")
        else:
            @app.get("/api/v3/series")
            async def series(request: Request):
                return matching(request)

            @app.get("/api/v3/series/lookup")
            async def lookup_series(request: Request):
                term = request.query_params.get("term", "")
                fake.lookups.append(term)
                prefix, _, value = term.partition(":")
                if prefix == "tvdb":
                    return [fake.known[int(value)]] if int(value) in fake.known else []
                field = {"tmdb": "tmdbId", "imdb": "imdbId"}.get(prefix)
                return [row for row in fake.known.values() if field and str(row.get(field)) == value]

            @app.post("/api/v3/series")
            async def add_series(request: Request):
                return add(await request.json())

            @app.put("/api/v3/series/editor")
            async def edit_series(request: Request):
                return edit(await request.json(), "seriesIds")

        return app
