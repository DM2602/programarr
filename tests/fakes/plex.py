"""A small Plex Media Server: libraries, items, seasons, posters, locks, refresh and collections.

Refresh reads poster files from the media folder with Plex' own naming rules
(written independently of Litfass' rules), so the tests check that Litfass names
files the way Plex expects.
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
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
        self.collections: dict[str, dict] = {}
        self.collection_calls: list[str] = []  # writes to collections, e.g. "move 1001 after 1002"
        self.transcode_composites = True  # False: the photo transcoder fails for composites
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
        before = self._shown(obj)
        obj["upload"] = data
        obj["selected"] = "upload"
        if lock:
            obj["locked"].add("thumb")
        self._changed(obj, before)

    def choose(self, key: str, which: str = "agent") -> None:
        """Someone picks a poster in Plex (agent, upload or local); Plex locks the field."""
        obj = self.items.get(key) or self.seasons[key]
        before = self._shown(obj)
        obj["selected"] = which
        obj["locked"].add("thumb")
        self._changed(obj, before)

    def _shown(self, obj: dict) -> tuple:
        if obj["selected"] == "local":
            return "local", (obj["local"] or {}).get("sha")
        if obj["selected"] == "upload":
            return "upload", hashlib.sha1(obj.get("upload") or b"").hexdigest()
        return "agent", obj["agent_poster"]

    def _changed(self, obj: dict, before: tuple) -> None:
        """Plex gives the poster a new address when it changes, and the item a new updatedAt."""
        if self._shown(obj) != before:
            obj["thumb_ts"] = obj.get("thumb_ts", 1) + 1
            if "updatedAt" in obj:
                obj["updatedAt"] = max(int(time.time()), obj["updatedAt"] + 1)

    def remove_item(self, key: str) -> None:
        self.items.pop(key, None)
        for collection in self.collections.values():
            if key in collection["items"]:
                collection["items"].remove(key)

    def add_collection(self, section: str, title: str, item_keys: list[str], smart: bool = False,
                       labels: tuple[str, ...] = (), automatic: bool = False) -> str:
        """A collection made elsewhere: by Kometa or by hand, or by Plex itself from TMDB's movie collections
        (automatic: a plex://collection/ guid and the poster from TMDB)."""
        key = self._key()
        guid = (f"plex://collection/{hashlib.md5(title.encode()).hexdigest()[:24]}" if automatic
                else f"collection://{uuid.uuid4()}")
        self.collections[key] = {"ratingKey": key, "section": section, "title": title, "items": list(item_keys),
                                 "smart": smart, "labels": list(labels), "sort": 0, "hub": None,
                                 "poster": poster_for(f"tmdb-{title}") if automatic else None, "guid": guid,
                                 "updatedAt": int(time.time())}
        return key

    def collection_titles(self, key: str) -> list[str]:
        return [self.items[item]["title"] for item in self.collections[key]["items"] if item in self.items]

    def find_collection(self, title: str) -> dict | None:
        return next((c for c in self.collections.values() if c["title"] == title), None)

    def _collection_json(self, collection: dict) -> dict:
        section = self.sections[collection["section"]]
        thumb = (f"/library/metadata/{collection['ratingKey']}/thumb/{collection['updatedAt']}" if collection["poster"]
                 else f"/library/collections/{collection['ratingKey']}/composite/{collection['updatedAt']}")
        return {
            "ratingKey": collection["ratingKey"], "key": f"/library/collections/{collection['ratingKey']}/children",
            "guid": collection["guid"],
            "type": "collection", "subtype": section["type"], "title": collection["title"],
            "librarySectionID": int(collection["section"]), "childCount": str(len(collection["items"])),
            "smart": "1" if collection["smart"] else "0", "thumb": thumb, "updatedAt": collection["updatedAt"],
            "collectionSort": str(collection["sort"]),
            "Label": [{"tag": label} for label in collection["labels"]],
        }

    def _uri_keys(self, uri: str) -> list[str] | None:
        match = re.fullmatch(r"server://(?P<machine>[^/]+)/com\.plexapp\.plugins\.library/library/metadata/(?P<keys>[\d,]+)",
                             uri or "")
        if not match or match.group("machine") != "abc123":
            return None
        return match.group("keys").split(",")

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
        before = self._shown(obj)
        path = self.local_asset(key)
        if path is None:
            obj["local"] = None
            if obj["selected"] == "local" and "thumb" not in obj["locked"]:
                obj["selected"] = "upload" if obj.get("upload") else "agent"
            self._changed(obj, before)
            return
        digest = hashlib.sha1(path.read_bytes()).hexdigest()
        obj["local"] = {"sha": digest, "path": str(path)}
        if "thumb" not in obj["locked"]:
            obj["selected"] = "local"
        self._changed(obj, before)

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
        obj = self.items.get(key) or self.seasons.get(key) or {}
        return f"/library/metadata/{key}/thumb/{obj.get('thumb_ts', 1)}"

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
            if request.query_params.get("type") == "18":
                collection = plex.collections.get(key)
                if collection is None or collection["section"] != section:
                    return Response(status_code=404)
                if "title.value" in request.query_params:
                    collection["title"] = request.query_params["title.value"]
                    plex.collection_calls.append(f"title {key}")
                return Response(status_code=200)
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
            if key in plex.collections:
                return container(Metadata=[plex._collection_json(plex.collections[key])])
            return Response(status_code=404)

        @app.post("/library/metadata/{key}/posters")
        async def upload_poster(key: str, request: Request):
            body = await request.body()
            if key in plex.collections:
                plex.collections[key]["poster"] = body
                plex.collections[key]["updatedAt"] += 1
                plex.collection_calls.append(f"poster {key}")
                return Response(status_code=200)
            if key in plex.items or key in plex.seasons:
                plex.upload(key, body)
                return Response(status_code=200)
            return Response(status_code=404)

        # -- collections ----------------------------------------------------------
        @app.get("/library/sections/{section}/collections")
        async def section_collections(section: str):
            if section not in plex.sections:
                return Response(status_code=404)
            rows = [plex._collection_json(c) for c in plex.collections.values() if c["section"] == section]
            return container(size=len(rows), Metadata=rows)

        @app.post("/library/collections")
        async def create_collection(request: Request):
            params = request.query_params
            section = params.get("sectionId", "")
            keys = plex._uri_keys(params.get("uri", ""))
            if section not in plex.sections or not keys or not params.get("title") or params.get("smart") != "0":
                return Response(status_code=400)
            wanted = 1 if plex.sections[section]["type"] == "movie" else 2
            if params.get("type") != str(wanted):
                return Response(status_code=400)
            key = plex.add_collection(section, params["title"], [k for k in keys if k in plex.items])
            plex.collection_calls.append(f"create {key}")
            return container(size=1, Metadata=[plex._collection_json(plex.collections[key])])

        @app.get("/library/collections/{key}/children")
        async def collection_children(key: str):
            collection = plex.collections.get(key)
            if collection is None:
                return Response(status_code=404)
            rows = []
            for item_key in collection["items"]:
                item = plex.items.get(item_key)
                if item:
                    rows.append(plex._movie_json(item) if item["type"] == "movie" else plex._show_json(item))
            return container(size=len(rows), Metadata=rows)

        @app.put("/library/collections/{key}/items")
        async def collection_add(key: str, request: Request):
            collection = plex.collections.get(key)
            keys = plex._uri_keys(request.query_params.get("uri", ""))
            if collection is None or not keys:
                return Response(status_code=404 if collection is None else 400)
            for item_key in keys:
                if item_key in plex.items and item_key not in collection["items"]:
                    collection["items"].append(item_key)
            plex.collection_calls.append(f"add {key} {','.join(keys)}")
            return Response(status_code=200)

        @app.delete("/library/collections/{key}/items/{item_key}")
        async def collection_remove(key: str, item_key: str):
            collection = plex.collections.get(key)
            if collection is None or item_key not in collection["items"]:
                return Response(status_code=404)
            collection["items"].remove(item_key)
            plex.collection_calls.append(f"remove {key} {item_key}")
            if not collection["items"]:
                del plex.collections[key]  # Plex keeps no empty collections
            return Response(status_code=200)

        @app.put("/library/collections/{key}/items/{item_key}/move")
        async def collection_move(key: str, item_key: str, request: Request):
            collection = plex.collections.get(key)
            after = request.query_params.get("after")
            if collection is None or item_key not in collection["items"] or after and after not in collection["items"]:
                return Response(status_code=404)
            collection["items"].remove(item_key)
            collection["items"].insert(collection["items"].index(after) + 1 if after else 0, item_key)
            plex.collection_calls.append(f"move {item_key} after {after or '-'}")
            return Response(status_code=200)

        @app.put("/library/collections/{key}/prefs")
        async def collection_prefs(key: str, request: Request):
            collection = plex.collections.get(key)
            if collection is None:
                return Response(status_code=404)
            if "collectionSort" in request.query_params:
                collection["sort"] = int(request.query_params["collectionSort"])
                plex.collection_calls.append(f"sort {key} {collection['sort']}")
            return Response(status_code=200)

        @app.delete("/library/collections/{key}")
        async def collection_delete(key: str):
            if plex.collections.pop(key, None) is None:
                return Response(status_code=404)
            plex.collection_calls.append(f"delete {key}")
            return Response(status_code=200)

        @app.get("/library/collections/{key}/composite/{stamp}")
        async def composite(key: str, stamp: str):
            return Response(poster_for(f"plex-collection-{key}"), media_type="image/jpeg")

        def hub_flags(params) -> dict:
            return {name: params.get(name) == "1"
                    for name in ("promotedToRecommended", "promotedToOwnHome", "promotedToSharedHome")}

        @app.get("/hubs/sections/{section}/manage")
        async def manage(section: str, request: Request):
            collection = plex.collections.get(request.query_params.get("metadataItemId", ""))
            if collection is None or collection["hub"] is None:
                return container(size=0)
            identifier = f"custom.collection.{section}.{collection['ratingKey']}"
            return container(size=1, Hub=[{"identifier": identifier, "title": collection["title"],
                                           **{name: int(value) for name, value in collection["hub"].items()}}])

        @app.post("/hubs/sections/{section}/manage")
        async def manage_create(section: str, request: Request):
            collection = plex.collections.get(request.query_params.get("metadataItemId", ""))
            if collection is None or collection["section"] != section or collection["hub"] is not None:
                return Response(status_code=400)
            collection["hub"] = hub_flags(request.query_params)
            plex.collection_calls.append(f"hub {collection['ratingKey']}")
            return Response(status_code=200)

        @app.put("/hubs/sections/{section}/manage/{identifier}")
        async def manage_update(section: str, identifier: str, request: Request):
            key = identifier.rsplit(".", 1)[-1]
            collection = plex.collections.get(key)
            if collection is None or collection["hub"] is None:
                return Response(status_code=404)
            collection["hub"] = hub_flags(request.query_params)
            plex.collection_calls.append(f"hub {key}")
            return Response(status_code=200)

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
            before = plex._shown(obj)
            obj["selected"] = "local" if "localmedia" in url else ("upload" if "upload" in url else "agent")
            obj["locked"].add("thumb")
            plex._changed(obj, before)
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
            if "/composite/" in url and not plex.transcode_composites:
                return Response(status_code=500)
            match = re.match(r"/library/metadata/(\d+)/file", url)
            if match:
                obj = plex.items.get(match.group(1)) or plex.seasons.get(match.group(1))
                if obj and obj["local"]:
                    return Response(Path(obj["local"]["path"]).read_bytes(), media_type="image/jpeg")
            return Response(poster_for(f"transcode-{url}", thumb=True), media_type="image/jpeg")

        return app
