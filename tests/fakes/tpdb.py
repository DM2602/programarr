"""A small ThePosterDB with the page markup the real site uses (search, item, set, login, assets)."""
from __future__ import annotations

import html
import secrets

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from .images import poster_for

PAGE_SIZE = 24


class FakeTpdb:
    def __init__(self, base: str = "https://tpdb.test", email: str = "dm@example.com", password: str = "geheim"):
        self.base = base
        self.email, self.password = email, password
        self.items: dict[str, dict] = {}
        self.posters: dict[str, dict] = {}
        self.sets: dict[str, dict] = {}
        self.sessions: set[str] = set()
        self.challenge = False
        self.page_requests: list[str] = []
        self.logins = 0
        self.next_id = 7000
        self.app = self._make_app()

    def _id(self) -> str:
        self.next_id += 1
        return str(self.next_id)

    # -- data ------------------------------------------------------------------
    def add_item(self, title: str, year: int | None, kind: str) -> str:
        item_id = self._id()
        self.items[item_id] = {"title": f"{title} ({year if year else 'N/A'})", "type": kind, "posters": []}
        return item_id

    def add_poster(self, item_id: str, label: str, uploader: str, set_id: str | None, kind: str = "show") -> str:
        poster_id = self._id()
        self.posters[poster_id] = {"item": item_id, "title": label, "uploader": uploader, "set": set_id,
                                   "type": kind}
        self.items[item_id]["posters"].append(poster_id)
        if set_id:
            self.sets[set_id]["posters"].append(poster_id)
        return poster_id

    def add_show_set(self, item_id: str, uploader: str, seasons: list[int], show_poster: bool = True,
                     specials: bool = False) -> str:
        set_id = self._id()
        item = self.items[item_id]
        self.sets[set_id] = {"title": item["title"], "uploader": uploader, "posters": []}
        if show_poster:
            self.add_poster(item_id, item["title"], uploader, set_id)
        for n in seasons:
            self.add_poster(item_id, f"{item['title']} - Season {n}", uploader, set_id)
        if specials:
            self.add_poster(item_id, f"{item['title']} - Specials", uploader, set_id)
        return set_id

    def add_movie_set(self, item_id: str, uploader: str) -> str:
        set_id = self._id()
        item = self.items[item_id]
        self.sets[set_id] = {"title": item["title"], "uploader": uploader, "posters": []}
        self.add_poster(item_id, item["title"], uploader, set_id, kind="movie")
        return set_id

    def image_name(self, poster_id: str) -> str:
        return f"tpdb-{poster_id}"

    # -- markup ----------------------------------------------------------------
    def card(self, poster_id: str, logged_in: bool) -> str:
        poster = self.posters[poster_id]
        item = self.items[poster["item"]]
        set_link = ""
        if poster["set"]:
            count = len(self.sets[poster["set"]]["posters"])
            set_link = f'<a class="badge set_poster_count" href="{self.base}/set/{poster["set"]}">{count}</a>'
        download = f"{self.base}/api/assets/{poster_id}/download" if logged_in else f"{self.base}/login"
        type_title = "Movie" if item["type"] == "movie" else "Show"
        return f'''<div class="col-6 col-lg-2 p-1"><div class="hovereffect rounded-poster">
  <picture><source srcset="{self.base}/prod/public/images/posters/optimized/x/{poster_id}.webp" type="image/webp"/>
  <img class="w-100 rounded-poster tpdb-poster" src="/images/defaults/missing_poster.jpg"/></picture>
  <div class="overlay rounded-poster" data-poster-id="{poster_id}" data-poster-type="{poster['type']}">
    <div class="row m-0 h-100 p-3"><div class="col-11 p-0 pr-1 poster-title-correction">
      <p class="p-0 mb-1 text-break">{html.escape(poster['title'])}</p>
      <p class="uploaded-by text-white d-inline-block text-truncate w-100">by <a href="{self.base}/user/{poster['uploader']}">{poster['uploader']}</a></p></div>
    <div class="col-1 p-0">{set_link}</div>
    <div class="col-12 p-0"><a class="text-white" data-toggle="tooltip" href="{self.base}/posters/{poster['item']}" title="{type_title}"></a>
      <a class="bg-transparent border-0 text-white" href="{download}"></a></div></div>
  </div></div></div>'''

    def page(self, title: str, body: str) -> str:
        return f"<!doctype html><html><head><title>{html.escape(title)}</title></head><body>" \
               f'<nav><a href="{self.base}/user/someone">Profil</a></nav>{body}</body></html>'

    # -- app -------------------------------------------------------------------
    def _make_app(self) -> FastAPI:
        app = FastAPI()
        fake = self

        def logged_in(request: Request) -> bool:
            return request.cookies.get("theposterdb_session") in fake.sessions

        @app.middleware("http")
        async def challenge(request: Request, call_next):
            path = request.url.path
            if not path.startswith("/prod/"):
                fake.page_requests.append(path)
            if fake.challenge and request.cookies.get("cf_clearance") != "ok" and not path.startswith("/prod/"):
                return HTMLResponse("<html><head><title>Just a moment...</title></head><body>cf-chl</body></html>",
                                    status_code=503)
            return await call_next(request)

        @app.get("/search")
        async def search(request: Request):
            term = request.query_params.get("term", "").lower()
            section = request.query_params.get("section", "movies")
            kind = "movie" if section == "movies" else "show"
            links = []
            for item_id, item in fake.items.items():
                if item["type"] == kind and term in item["title"].lower():
                    links.append(f'<a class="btn btn-dark-lighter flex-grow-1 text-truncate py-2 text-left position-relative" '
                                 f'href="{fake.base}/posters/{item_id}"><span class="text-truncate">'
                                 f'{html.escape(item["title"])}</span></a>')
            return HTMLResponse(fake.page("Search | TPDb", '<div class="row">' + "".join(links) + "</div>"))

        @app.get("/posters/{item_id}")
        async def item_page(item_id: str, request: Request):
            item = fake.items.get(item_id)
            if not item:
                return HTMLResponse("not found", status_code=404)
            page = int(request.query_params.get("page", "1"))
            posters = item["posters"][(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
            cards = "".join(fake.card(pid, logged_in(request)) for pid in posters)
            pager = ""
            if page * PAGE_SIZE < len(item["posters"]):
                pager = f'<ul class="pagination"><li><a class="page-link" rel="next" href="{fake.base}/posters/{item_id}?page={page + 1}">Next »</a></li></ul>'
            body = f'<h1>{html.escape(item["title"])}</h1><div class="row d-flex flex-wrap m-0 w-100 mx-n1 mt-n1">{cards}</div>{pager}'
            return HTMLResponse(fake.page(f"{item['title']} Posters | TPDb", body))

        @app.get("/set/{set_id}")
        async def set_page(set_id: str, request: Request):
            poster_set = fake.sets.get(set_id)
            if not poster_set:
                return HTMLResponse("not found", status_code=404)
            cards = "".join(fake.card(pid, logged_in(request)) for pid in poster_set["posters"])
            title = f"{poster_set['title']} Set By {poster_set['uploader']} | TPDb"
            return HTMLResponse(fake.page(title, f'<div class="row d-flex flex-wrap m-0 w-100 mx-n1 mt-n1">{cards}</div>'))

        @app.get("/login")
        async def login_form():
            body = ('<form method="POST" action="/login"><input type="hidden" name="_token" value="csrf123">'
                    '<input name="login"><input name="password" type="password"></form>')
            response = HTMLResponse(fake.page("Login | TPDb", body))
            response.set_cookie("XSRF-TOKEN", "csrf123")
            return response

        @app.post("/login")
        async def login(request: Request):
            form = await request.form()
            token, user, password = form.get("_token"), form.get("login"), form.get("password")
            if token != "csrf123" or user != fake.email or password != fake.password:
                return RedirectResponse(f"{fake.base}/login", status_code=302)
            fake.logins += 1
            session = secrets.token_hex(8)
            fake.sessions.add(session)
            response = RedirectResponse(f"{fake.base}/", status_code=302)
            response.set_cookie("theposterdb_session", session)
            return response

        @app.get("/")
        async def home():
            return HTMLResponse(fake.page("TPDb", "<h1>Home</h1>"))

        def asset_response(poster_id: str, request: Request) -> Response:
            if poster_id not in fake.posters:
                return Response(status_code=404)
            if not logged_in(request):
                return RedirectResponse(f"{fake.base}/login", status_code=302)
            return Response(poster_for(fake.image_name(poster_id), fake.posters[poster_id]["title"][-8:]),
                            media_type="image/jpeg")

        @app.get("/api/assets/{poster_id}")
        async def asset(poster_id: str, request: Request):
            return asset_response(poster_id, request)

        @app.get("/api/assets/{poster_id}/download")
        async def download(poster_id: str, request: Request):
            return asset_response(poster_id, request)

        @app.get("/prod/public/images/posters/optimized/x/{name}")
        async def preview(name: str):
            poster_id = name.split(".")[0]
            if poster_id not in fake.posters:
                return Response(status_code=404)
            return Response(poster_for(fake.image_name(poster_id), fake.posters[poster_id]["title"][-8:], thumb=True,
                                       fmt="WEBP"), media_type="image/webp")

        return app
