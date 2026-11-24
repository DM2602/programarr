"""A small Plex Media Server: libraries, items, seasons, posters, locks and refresh.

Refresh reads poster files from the media folder with Plex' own naming rules
(written independently of Litfass' rules), so the tests check that Litfass names
files the way Plex expects.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .images import poster_for

EXTS = (".jpg", ".jpeg", ".png", ".tbn")


class FakePlex:
    def __init__(self, media_root: Path, plex_root: str = "/data/media", token: str = "plex-token"):
        self.media_root = Path(media_root)
        self.plex_root = plex_root.rstrip("/")
        self.token = token
        self.sections = {
            "1": {"title": "Filme", "type": "movie", "path": f"{self.plex_root}/movies", "agent": "tv.plex.agents.movie"},
            "2": {"title": "Serien", "type": "show", "path": f"{self.plex_root}/tv", "agent": "tv.plex.agents.series"},
        }
        self.items: dict[str, dict] = {}
        self.seasons: dict[str, dict] = {}
        self.episodes: dict[str, dict] = {}
        self.next_key = 1000
        self.refreshes: list[str] = []
        self.unlocks: list[str] = []
        self.selects: list[tuple[str, str]] = []
        self.up = True
        self.read_local_on_refresh = True
        (self.media_root / "movies").mkdir(parents=True, exist_ok=True)
        (self.media_root / "tv").mkdir(parents=True, exist_ok=True)
        self.app = self._make_app()

    # -- building the library -------------------------------------------------
    def _key(self) -> str:
        self.next_key += 1
        return str(self.next_key)

    def local(self, plex_path: str) -> Path:
        return self.media_root / plex_path[len(self.plex_root) + 1:]

    def add_movie(self, title: str, year: int, tmdb: str | None, imdb: str | None = None,
                  folder: str | None = None, file_name: str | None = None, collection: str | None = None) -> str:
        key = self._key()
        folder = folder or f"{title} ({year})"
        file_name = file_name or f"{title} ({year}).mkv"
        plex_file = f"{self.plex_root}/movies/{folder}/{file_name}"
        path = self.local(plex_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")
        guids = [{"id": f"tmdb://{tmdb}"}] if tmdb else []
        if imdb:
            guids.append({"id": f"imdb://{imdb}"})
        now = int(time.time())
        self.items[key] = {
            "ratingKey": key, "type": "movie", "title": title, "year": year, "section": "1",
            "guids": guids, "file": plex_file, "addedAt": now, "updatedAt": now, "locked": set(),
            "agent_poster": f"https://tmdb-images.test/agent/{key}.jpg", "selected": "agent", "local": None,
        }
        return key

    def add_show(self, title: str, year: int, tmdb: str | None, tvdb: str | None = None,
                 seasons: list[int] = (1, 2), season_dir: str = "Season {n:02d}", folder: str | None = None) -> str:
        key = self._key()
        location = f"{self.plex_root}/tv/{folder or f'{title} ({year})'}"
        self.local(location).mkdir(parents=True, exist_ok=True)
        guids = [{"id": f"tmdb://{tmdb}"}] if tmdb else []
        if tvdb:
            guids.append({"id": f"tvdb://{tvdb}"})
        now = int(time.time())
        self.items[key] = {
            "ratingKey": key, "type": "show", "title": title, "year": year, "section": "2", "guids": guids,
            "location": location, "addedAt": now, "updatedAt": now, "locked": set(), "season_dir": season_dir,
            "agent_poster": f"https://tmdb-images.test/agent/{key}.jpg", "selected": "agent", "local": None,
        }
        for number in seasons:
            self.add_season(key, number)
        return key

    def add_season(self, show_key: str, number: int, episodes: int = 2) -> str:
        show = self.items[show_key]
        key = self._key()
        folder_name = show["season_dir"].format(n=number)  # specials in "Season 00", like Sonarr can do
        folder = f"{show['location']}/{folder_name}"
        self.local(folder).mkdir(parents=True, exist_ok=True)
        now = int(time.time())
        self.seasons[key] = {
            "ratingKey": key, "show": show_key, "index": number, "addedAt": now, "locked": set(),
            "agent_poster": f"https://tmdb-images.test/agent/{key}.jpg", "selected": "agent", "local": None,
        }
        for episode in range(1, episodes + 1):
            ep_key = self._key()
            file = f"{folder}/{show['title']} - S{number:02d}E{episode:02d}.mkv"
            self.local(file).write_bytes(b"video")
            self.episodes[ep_key] = {"ratingKey": ep_key, "show": show_key, "season": key, "parentIndex": number,
                                     "index": episode, "file": file}
        show["updatedAt"] = now
        return key

    def upload(self, key: str, data: bytes, lock: bool = True) -> None:
        """A poster uploaded into Plex (as Posteria or Marquee did), selected and locked."""
        obj = self.items.get(key) or self.seasons[key]
        obj["upload"] = data
        obj["selected"] = "upload"
        if lock:
            obj["locked"].add("thumb")

    def remove_item(self, key: str) -> None:
        self.items.pop(key, None)

    def season_key(self, show_key: str, number: int) -> str | None:
        for key, season in self.seasons.items():
            if season["show"] == show_key and season["index"] == number:
                return key
        return None

    # -- Plex' local asset rules (independent of Litfass) ---------------------
    def _first(self, folder: Path, names: list[str]) -> Path | None:
        if not folder.is_dir():
            return None
        files = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
        for name in names:
            for ext in EXTS:
                if name + ext in files:
                    return files[name + ext]
        return None

    def local_asset(self, key: str) -> Path | None:
        if key in self.items:
            item = self.items[key]
            if item["type"] == "movie":
                path = self.local(item["file"])
                stem = path.stem.lower()
                return self._first(path.parent, ["poster", "cover", "default", "folder", "movie", stem])
            return self._first(self.local(item["location"]), ["poster", "show", "folder"])
        season = self.seasons[key]
        show = self.items[season["show"]]
        n = season["index"]
        root = self.local(show["location"])
        names = ["season-specials-poster", "season00-poster", "season00"] if n == 0 else \
            [f"season{n:02d}-poster", f"season{n}-poster", f"season{n:02d}", f"season{n}"]
        found = self._first(root, names)
        if found:
            return found
        episode = next((e for e in self.episodes.values() if e["season"] == key), None)
        if episode:
            folder = self.local(episode["file"]).parent
            if folder != root:
                inner = ["season-specials-poster", "season00"] if n == 0 else [f"season{n:02d}", f"season{n}"]
                return self._first(folder, inner)
        return None

    def _read(self, key: str) -> None:
        obj = self.items.get(key) or self.seasons[key]
        path = self.local_asset(key)
        if path is None:
            obj["local"] = None
            if obj["selected"] == "local" and "thumb" not in obj["locked"]:
                obj["selected"] = "upload" if obj.get("upload") else "agent"
            return
        digest = hashlib.sha1(path.read_bytes()).hexdigest()
        obj["local"] = {"sha": digest, "path": str(path)}
        if "thumb" not in obj["locked"]:
            obj["selected"] = "local"

    def refresh(self, key: str) -> None:
        self.refreshes.append(key)
        if not self.read_local_on_refresh:
            return
        if key in self.items:
            self._read(key)
            for season_key, season in self.seasons.items():
                if season["show"] == key:
                    self._read(season_key)
        elif key in self.seasons:
            self._read(key)

    def selected_is_local(self, key: str) -> bool:
        obj = self.items.get(key) or self.seasons[key]
        return obj["selected"] == "local" and obj["local"] is not None

    # -- JSON shapes -----------------------------------------------------------------
    def _fields(self, obj: dict) -> list[dict]:
        return [{"name": name, "locked": True} for name in sorted(obj["locked"])]

    def _thumb(self, key: str) -> str:
        return f"/library/metadata/{key}/thumb/1"

    def _movie_json(self, item: dict, details: bool = False) -> dict:
        data = {
            "ratingKey": item["ratingKey"], "key": f"/library/metadata/{item['ratingKey']}", "type": "movie",
            "title": item["title"], "year": item["year"], "addedAt": item["addedAt"], "updatedAt": item["updatedAt"],
            "thumb": self._thumb(item["ratingKey"]), "Guid": item["guids"],
            "guid": f"plex://movie/{item['ratingKey']}",
            "Media": [{"Part": [{"file": item["file"]}]}],
        }
        if details:
            data["Field"] = self._fields(item)
        return data

    def _show_json(self, item: dict, details: bool = False) -> dict:
        data = {
            "ratingKey": item["ratingKey"], "key": f"/library/metadata/{item['ratingKey']}/children", "type": "show",
            "title": item["title"], "year": item["year"], "addedAt": item["addedAt"], "updatedAt": item["updatedAt"],
            "thumb": self._thumb(item["ratingKey"]), "Guid": item["guids"],
            "guid": f"plex://show/{item['ratingKey']}",
        }
        if details:
            data["Location"] = [{"path": item["location"]}]
            data["Field"] = self._fields(item)
        return data

    def _season_json(self, key: str, details: bool = False) -> dict:
        season = self.seasons[key]
        data = {
            "ratingKey": key, "type": "season", "index": season["index"], "parentRatingKey": season["show"],
            "title": "Specials" if season["index"] == 0 else f"Staffel {season['index']}",
            "addedAt": season["addedAt"], "thumb": self._thumb(key),
        }
        if details:
            data["Field"] = self._fields(season)
        return data

    def _posters_json(self, key: str) -> list[dict]:
        obj = self.items.get(key) or self.seasons[key]
        result = [{
            "key": obj["agent_poster"], "ratingKey": obj["agent_poster"], "thumb": obj["agent_poster"],
            "provider": "tv.plex.agents.movie", "selected": obj["selected"] == "agent",
        }]
        if obj.get("upload"):
            upload_key = f"upload://posters/{hashlib.sha1(obj['upload']).hexdigest()}"
            path = f"/library/metadata/{key}/file?url={quote(upload_key, safe='')}"
            result.append({"key": path, "ratingKey": upload_key, "thumb": path, "selected": obj["selected"] == "upload"})
        if obj["local"]:
            meta = f"metadata://posters/com.plexapp.agents.localmedia_{obj['local']['sha']}"
            path = f"/library/metadata/{key}/file?url={quote(meta, safe='')}"
            result.append({"key": path, "ratingKey": meta, "thumb": path, "provider": "local",
                           "selected": obj["selected"] == "local"})
        return result

    # -- app ---------------------------------------------------------------------
    def _make_app(self) -> FastAPI:
        app = FastAPI()
        plex = self

        @app.middleware("http")
        async def auth(request: Request, call_next):
            if not plex.up:
                return Response(status_code=503)
            token = request.headers.get("x-plex-token") or request.query_params.get("X-Plex-Token")
            if token != plex.token:
                return Response(status_code=401)
            return await call_next(request)

        def container(**values):
            return JSONResponse({"MediaContainer": values})

        @app.get("/")
        async def root():
            return container(friendlyName="Testserver", version="1.42.0", machineIdentifier="abc123")

        @app.get("/library/sections")
        async def sections():
            return container(Directory=[
                {"key": key, "title": s["title"], "type": s["type"], "agent": s["agent"],
                 "Location": [{"path": s["path"]}]}
                for key, s in plex.sections.items()
            ])

        @app.get("/library/sections/{section}/all")
        async def section_all(section: str, request: Request):
            kind = int(request.query_params.get("type", "0"))
            start = int(request.query_params.get("X-Plex-Container-Start", "0"))
            size = int(request.query_params.get("X-Plex-Container-Size", "50"))
            if kind == 1:
                rows = [plex._movie_json(i) for i in plex.items.values() if i["section"] == section and i["type"] == "movie"]
            elif kind == 2:
                rows = [plex._show_json(i) for i in plex.items.values() if i["section"] == section and i["type"] == "show"]
            elif kind == 3:
                rows = [plex._season_json(k) for k, s in plex.seasons.items()
                        if s["show"] in plex.items and plex.items[s["show"]]["section"] == section]
            else:
                rows = []
            return container(size=len(rows[start:start + size]), totalSize=len(rows), Metadata=rows[start:start + size])

        @app.put("/library/sections/{section}/all")
        async def section_edit(section: str, request: Request):
            key = request.query_params.get("id")
            obj = plex.items.get(key) or plex.seasons.get(key)
            if obj is None:
                return Response(status_code=404)
            for param, value in request.query_params.items():
                if param.endswith(".locked"):
                    field = param[: -len(".locked")]
                    if value == "0":
                        obj["locked"].discard(field)
                        plex.unlocks.append(key)
                    else:
                        obj["locked"].add(field)
            return Response(status_code=200)

        @app.get("/library/metadata/{key}")
        async def metadata(key: str):
            if key in plex.items:
                item = plex.items[key]
                body = plex._movie_json(item, True) if item["type"] == "movie" else plex._show_json(item, True)
                return container(Metadata=[body])
            if key in plex.seasons:
                return container(Metadata=[plex._season_json(key, True)])
            return Response(status_code=404)

        @app.get("/library/metadata/{key}/allLeaves")
        async def leaves(key: str):
            rows = [{"ratingKey": e["ratingKey"], "type": "episode", "parentIndex": e["parentIndex"],
                     "index": e["index"], "Media": [{"Part": [{"file": e["file"]}]}]}
                    for e in plex.episodes.values() if e["show"] == key]
            return container(Metadata=rows)

        @app.get("/library/metadata/{key}/children")
        async def children(key: str):
            rows = [plex._season_json(k) for k, s in plex.seasons.items() if s["show"] == key]
            return container(Metadata=rows)

        @app.get("/library/metadata/{key}/posters")
        async def posters(key: str):
            if key not in plex.items and key not in plex.seasons:
                return Response(status_code=404)
            return container(Metadata=plex._posters_json(key))

        @app.put("/library/metadata/{key}/refresh")
        async def refresh(key: str):
            plex.refresh(key)
            return Response(status_code=200)

        @app.put("/library/metadata/{key}/poster")
        async def select(key: str, request: Request):
            obj = plex.items.get(key) or plex.seasons.get(key)
            url = request.query_params.get("url", "")
            plex.selects.append((key, url))
            obj["selected"] = "local" if "localmedia" in url else ("upload" if "upload" in url else "agent")
            obj["locked"].add("thumb")
            return Response(status_code=200)

        @app.get("/library/metadata/{key}/file")
        async def file(key: str, request: Request):
            obj = plex.items.get(key) or plex.seasons.get(key)
            if obj and obj.get("upload") and "upload" in request.query_params.get("url", ""):
                return Response(obj["upload"], media_type="image/jpeg")
            if not obj or not obj["local"]:
                return Response(status_code=404)
            return Response(Path(obj["local"]["path"]).read_bytes(), media_type="image/jpeg")

        @app.get("/library/metadata/{key}/thumb/{stamp}")
        async def thumb(key: str, stamp: str):
            return Response(poster_for(f"plex-agent-{key}", thumb=True), media_type="image/jpeg")

        @app.get("/photo/:/transcode")
        async def transcode(request: Request):
            url = request.query_params.get("url", "")
            match = re.match(r"/library/metadata/(\d+)/file", url)
            if match:
                obj = plex.items.get(match.group(1)) or plex.seasons.get(match.group(1))
                if obj and obj["local"]:
                    return Response(Path(obj["local"]["path"]).read_bytes(), media_type="image/jpeg")
            return Response(poster_for(f"transcode-{url}", thumb=True), media_type="image/jpeg")

        return app
