"""A small MediUX: GraphQL answers shaped like the real API (as AURA reads it)."""
from __future__ import annotations

import re
import time

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .images import poster_for


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class FakeMediux:
    def __init__(self, token: str = "mediux-token"):
        self.token = token
        self.username = "dominik"
        self.shows: dict[str, dict] = {}
        self.movies: dict[str, dict] = {}
        self.collection_sets: dict[str, dict] = {}
        self.follows = ["willtong93"]
        self.hides = ["hiddenguy"]
        self.images: dict[str, str] = {}  # image id -> label on the picture
        self.requests: list[str] = []
        self.next_id = 500
        self.app = self._make_app()

    def _id(self) -> str:
        self.next_id += 1
        return str(self.next_id)

    def image(self, label: str) -> dict:
        image_id = f"img{self._id()}"
        self.images[image_id] = label
        return {"id": image_id, "modified_on": "2026-01-10T10:00:00", "filesize": 1000, "language": None}

    # -- data ----------------------------------------------------------------
    def add_show_set(self, tmdb: str, title: str, year: int, designer: str, seasons: list[int],
                     show_poster: bool = True, popularity: int = 10, tvdb: str | None = None) -> dict:
        show = self.shows.setdefault(tmdb, {"id": tmdb, "title": title, "first_air_date": f"{year}-01-01",
                                            "tvdb_id": tvdb, "show_sets": []})
        set_id = self._id()
        raw = {
            "id": set_id, "set_title": title, "user_created": {"username": designer},
            "date_created": "2026-01-01T00:00:00", "date_updated": "2026-01-10T10:00:00", "popularity": popularity,
            "show_poster": [self.image(f"{designer}-{tmdb}-show")] if show_poster else [],
            "season_posters": [{**self.image(f"{designer}-{tmdb}-s{n}"), "season": {"season_number": n}} for n in seasons],
        }
        show["show_sets"].append(raw)
        return raw

    def add_season_to_set(self, raw: dict, number: int) -> dict:
        entry = {**self.image(f"{raw['user_created']['username']}-x-s{number}"), "season": {"season_number": number}}
        raw["season_posters"].append(entry)
        raw["date_updated"] = _stamp()
        return entry

    def change_image(self, entry: dict, label: str) -> None:
        self.images[entry["id"]] = label
        entry["modified_on"] = "2026-03-01T12:00:00"

    def add_movie_set(self, tmdb: str, title: str, year: int, designer: str, popularity: int = 5,
                      collection: dict | None = None) -> dict:
        movie = self.movies.setdefault(tmdb, {"id": tmdb, "title": title, "release_date": f"{year}-05-01",
                                              "movie_sets": [], "collection_id": collection})
        set_id = self._id()
        raw = {
            "id": set_id, "set_title": title, "user_created": {"username": designer},
            "date_created": "2026-01-01T00:00:00", "date_updated": "2026-01-10T10:00:00", "popularity": popularity,
            "movie_poster": [self.image(f"{designer}-{tmdb}-movie")],
        }
        movie["movie_sets"].append(raw)
        return raw

    def add_collection_set(self, collection_id: str, name: str, designer: str, movies: list[tuple[str, str, int]]) -> str:
        set_id = self._id()
        set_meta = {"id": set_id, "set_title": name, "user_created": {"username": designer},
                    "date_created": "2026-01-01T00:00:00", "date_updated": "2026-01-10T10:00:00", "popularity": 3}
        collection = {"id": collection_id, "collection_name": name, "movies": []}
        for tmdb, title, year in movies:
            entry = {**self.image(f"{designer}-{tmdb}-coll"), "collection_set": set_meta}
            collection["movies"].append({"id": tmdb, "title": title, "release_date": f"{year}-01-01",
                                         "posters": [entry]})
            movie = self.movies.setdefault(tmdb, {"id": tmdb, "title": title, "release_date": f"{year}-01-01",
                                                  "movie_sets": [], "collection_id": None})
            movie["collection_id"] = collection
        self.collection_sets[set_id] = {**set_meta, "collection_id": collection}
        return set_id

    # -- app -------------------------------------------------------------------
    def _find_show_set(self, set_id: str):
        for show in self.shows.values():
            for raw in show["show_sets"]:
                if raw["id"] == set_id:
                    return show, raw
        return None, None

    def _find_movie_set(self, set_id: str):
        for movie in self.movies.values():
            for raw in movie["movie_sets"]:
                if raw["id"] == set_id:
                    return movie, raw
        return None, None

    def _make_app(self) -> FastAPI:
        app = FastAPI()
        fake = self

        def authorized(request: Request) -> bool:
            return request.headers.get("authorization") == f"Bearer {fake.token}"

        @app.get("/users/me")
        async def me(request: Request):
            if not authorized(request):
                return JSONResponse({"errors": [{"message": "Invalid user credentials."}]}, status_code=401)
            return {"data": {"id": "u1", "username": fake.username}}

        @app.post("/graphql")
        async def graphql(request: Request):
            if not authorized(request):
                return JSONResponse({"errors": [{"message": "Invalid user credentials."}]}, status_code=401)
            body = await request.json()
            query, variables = body.get("query", ""), body.get("variables") or {}
            name = re.search(r"query\s+(\w+)", query).group(1)
            fake.requests.append(name)
            if name == "programarrShowSets":
                show = fake.shows.get(str(variables["tmdb_id"]))
                if not show:
                    return {"data": {"shows_by_id": None}, "errors": [{"message": "FORBIDDEN"}]}
                return {"data": {"shows_by_id": show}}
            if name == "programarrShowSet":
                show, raw = fake._find_show_set(str(variables["set_id"]))
                if not raw:
                    return {"data": {"show_sets_by_id": None}, "errors": [{"message": "FORBIDDEN"}]}
                return {"data": {"show_sets_by_id": {**raw, "show_id": {k: show[k] for k in ("id", "title", "first_air_date")}}}}
            if name == "programarrMovieSets":
                movie = fake.movies.get(str(variables["tmdb_id"]))
                if not movie:
                    return {"data": {"movies_by_id": None}, "errors": [{"message": "FORBIDDEN"}]}
                return {"data": {"movies_by_id": movie}}
            if name == "programarrMovieSet":
                movie, raw = fake._find_movie_set(str(variables["set_id"]))
                if not raw:
                    return {"data": {"movie_sets_by_id": None}}
                return {"data": {"movie_sets_by_id": {**raw, "movie_id": {k: movie[k] for k in ("id", "title", "release_date")}}}}
            if name == "programarrCollectionSet":
                raw = fake.collection_sets.get(str(variables["set_id"]))
                return {"data": {"collection_sets_by_id": raw}}
            if name == "programarrTvdb":
                hits = [{"id": tmdb, "tvdb_id": s.get("tvdb_id")} for tmdb, s in fake.shows.items()
                        if s.get("tvdb_id") == variables.get("tvdb_id")]
                return {"data": {"shows": hits}}
            if name == "programarrFollows":
                return {"data": {
                    "user_follows": [{"followee_id": {"username": n}} for n in fake.follows],
                    "user_hides": [{"hiding_id": {"username": n}} for n in fake.hides],
                }}
            return JSONResponse({"errors": [{"message": f"unknown query {name}"}]}, status_code=400)

        @app.get("/assets/{image_id}")
        async def asset(image_id: str, request: Request):
            if not authorized(request):
                return Response(status_code=401)
            label = fake.images.get(image_id)
            if label is None:
                return Response(status_code=404)
            thumb = request.query_params.get("key") == "thumb"
            return Response(poster_for(label, label.rsplit("-", 1)[-1].upper(), thumb=thumb), media_type="image/jpeg")

        return app
