"""ThePosterDB: no API, so search, item and set pages are read as HTML.

Search and set pages are public. Full-size downloads need a (free) TPDB account,
so Programarr logs in with the stored credentials when TPDB asks for it. If TPDB
answers with a Cloudflare challenge, pages go through FlareSolverr when one is
configured.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from ... import cache, http, settings
from ...http import is_challenge
from ...util import norm_title, season_from_label, split_title_year
from .base import ImageRef, PosterSet, SourceError

_POSTERS_HREF = re.compile(r"/posters/(\d+)(?:[/?#]|$)")
_SET_HREF = re.compile(r"/set/(\d+)")
_lock = asyncio.Lock()
_last_request = 0.0
_user_agent: str = http.BROWSER_UA


class LoginRequired(SourceError):
    pass


@dataclass
class Card:
    poster_id: str
    title: str
    poster_type: str
    uploader: str
    set_id: str | None
    preview: str
    item_url: str | None

    @property
    def season(self) -> int | None:
        """Season of a label such as "Show (2022) - Season 2" (0 = specials), else None."""
        if " - " not in self.title:
            return None
        return season_from_label(self.title.rsplit(" - ", 1)[1])

    @property
    def base_title(self) -> tuple[str, int | None]:
        # Only a season suffix is cut off: movie titles may contain " - " themselves.
        head = self.title.rsplit(" - ", 1)[0] if self.season is not None else self.title
        return split_title_year(head)


def _base() -> str:
    return settings.get("tpdb_base").rstrip("/")


def _cookies() -> httpx.Cookies:
    jar = httpx.Cookies()
    host = urlsplit(_base()).hostname or "theposterdb.com"
    for entry in settings.get("tpdb_cookies").get("cookies", []) if isinstance(settings.get("tpdb_cookies"), dict) else []:
        try:
            jar.set(entry["name"], entry["value"], domain=entry.get("domain") or host, path=entry.get("path") or "/")
        except (KeyError, TypeError):
            continue
    return jar


def _save_cookies(jar: httpx.Cookies, user_agent: str | None = None) -> None:
    stored = []
    for cookie in jar.jar:
        stored.append({"name": cookie.name, "value": cookie.value, "domain": cookie.domain, "path": cookie.path})
    value = {"cookies": stored, "at": int(time.time())}
    previous = settings.get("tpdb_cookies")
    if user_agent:
        value["ua"] = user_agent
    elif isinstance(previous, dict) and previous.get("ua"):
        value["ua"] = previous["ua"]
    settings.set("tpdb_cookies", value)


def _ua() -> str:
    stored = settings.get("tpdb_cookies")
    if isinstance(stored, dict) and stored.get("ua"):
        return stored["ua"]
    return _user_agent


async def _polite() -> None:
    global _last_request
    delay = float(settings.get("tpdb_delay") or 0)
    wait = _last_request + delay - time.monotonic()
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request = time.monotonic()


async def _flaresolverr(url: str, method: str = "get", post_data: str | None = None) -> tuple[int, str, str]:
    endpoint = settings.get("flaresolverr_url").rstrip("/")
    if not endpoint:
        raise SourceError(
            "ThePosterDB shows a Cloudflare check. Set up FlareSolverr under Settings › Sources."
        )
    cookies = [{"name": c["name"], "value": c["value"]} for c in settings.get("tpdb_cookies").get("cookies", [])] \
        if isinstance(settings.get("tpdb_cookies"), dict) else []
    body = {"cmd": f"request.{method}", "url": url, "maxTimeout": 60000, "cookies": cookies}
    if post_data is not None:
        body["postData"] = post_data
    async with http.client(timeout=90.0) as client:
        try:
            response = await client.post(f"{endpoint}/v1", json=body)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"Cannot reach FlareSolverr ({endpoint})") from exc
    if data.get("status") != "ok":
        raise SourceError(f"FlareSolverr: {data.get('message', 'error')}")
    solution = data.get("solution") or {}
    jar = _cookies()
    for cookie in solution.get("cookies") or []:
        jar.set(cookie["name"], cookie["value"], domain=cookie.get("domain") or "", path=cookie.get("path") or "/")
    _save_cookies(jar, solution.get("userAgent"))
    return int(solution.get("status") or 200), solution.get("response") or "", solution.get("url") or url


async def get_page(url: str) -> tuple[str, str]:
    """Return (html, final_url) for a TPDB page."""
    async with _lock:
        await _polite()
        async with http.client(timeout=45.0, headers={"User-Agent": _ua(), "Accept": "text/html"},
                               cookies=_cookies()) as client:
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                raise SourceError(f"Cannot reach ThePosterDB: {exc.__class__.__name__}") from exc
            text = response.text
            if is_challenge(response.status_code, text):
                status, text, final = await _flaresolverr(url)
                if is_challenge(status, text):
                    raise SourceError("ThePosterDB blocks the request, even through FlareSolverr")
                return text, final
            if response.status_code == 404:
                raise SourceError("Page not found on ThePosterDB (404)")
            if response.status_code >= 400:
                raise SourceError(f"ThePosterDB answered {response.status_code}")
            _save_cookies(client.cookies)
            return text, str(response.url)


# -- parsing ---------------------------------------------------------------
def parse_search(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    results, seen = [], set()
    links = soup.select("a.btn-dark-lighter[href]") or soup.select("a[href]")
    for link in links:
        href = link.get("href", "")
        match = _POSTERS_HREF.search(href)
        if not match or match.group(1) in seen:
            continue
        text = link.get_text(" ", strip=True)
        if not text or len(text) > 200:
            continue
        title, year = split_title_year(text)
        seen.add(match.group(1))
        results.append({"id": match.group(1), "title": title, "year": year, "url": urljoin(_base() + "/", href)})
    return results


def _card_container(overlay):
    node = overlay
    for _ in range(6):
        parent = node.parent
        if parent is None:
            break
        classes = parent.get("class") or []
        if "hovereffect" in classes:
            return parent.parent if parent.parent is not None else parent
        node = parent
    return overlay.parent or overlay


def _preview(card) -> str:
    for source in card.select("source[srcset]"):
        value = source.get("srcset", "").split(",")[0].strip().split(" ")[0]
        if value and "missing_poster" not in value:
            return urljoin(_base() + "/", value)
    for img in card.select("img"):
        for attr in ("data-src", "data-original", "src"):
            value = img.get(attr, "")
            if value and "missing_poster" not in value and not value.startswith("data:"):
                return urljoin(_base() + "/", value)
    return ""


def parse_cards(html: str) -> list[Card]:
    soup = BeautifulSoup(html, "lxml")
    cards, seen = [], set()
    for overlay in soup.select("[data-poster-id]"):
        poster_id = str(overlay.get("data-poster-id") or "").strip()
        if not poster_id.isdigit() or poster_id in seen:
            continue
        seen.add(poster_id)
        card = _card_container(overlay)
        title_el = card.select_one(".poster-title-correction p") or card.select_one("p.text-break") or card.select_one("p")
        uploader_el = card.select_one(".uploaded-by a") or card.select_one("a[href*='/user/']")
        set_el = card.select_one("a[href*='/set/']")
        item_el = card.select_one("a[href*='/posters/']")
        set_match = _SET_HREF.search(set_el.get("href", "")) if set_el else None
        type_label = (overlay.get("data-poster-type") or "").lower()
        if not type_label:
            typed = card.select_one("a[title][href*='/posters/']")
            type_label = (typed.get("title") or "").lower() if typed else ""
        cards.append(
            Card(
                poster_id=poster_id,
                title=title_el.get_text(" ", strip=True) if title_el else "",
                poster_type=type_label,
                uploader=uploader_el.get_text(strip=True) if uploader_el else "",
                set_id=set_match.group(1) if set_match else None,
                preview=_preview(card),
                item_url=urljoin(_base() + "/", item_el.get("href")) if item_el else None,
            )
        )
    return cards


def parse_set_meta(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    title = title.split(" | ")[0]
    designer = ""
    match = re.match(r"(?P<title>.+?)\s+Set By\s+(?P<user>.+)$", title, re.IGNORECASE)
    if match:
        title, designer = match.group("title").strip(), match.group("user").strip()
    if not designer:
        user = soup.select_one(".uploaded-by a") or soup.select_one("a[href*='/user/']")
        designer = user.get_text(strip=True) if user else ""
    return {"title": title, "designer": designer}


def parse_page_title(html: str) -> str:
    """Title of a TPDB item page: "Severance (2022)" from the heading or the window title."""
    soup = BeautifulSoup(html, "lxml")
    heading = soup.select_one("h1")
    if heading and heading.get_text(strip=True):
        return heading.get_text(" ", strip=True)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return re.sub(r"\s+Posters?\s*\|.*$", "", title).split(" | ")[0].strip()


def next_page(html: str, current: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for link in soup.select("a[rel=next], a.page-link[href]"):
        href = link.get("href", "")
        text = link.get_text(" ", strip=True).lower()
        if link.get("rel") == ["next"] or "next" in text or "»" in text or "weiter" in text:
            if "page=" in href:
                return urljoin(current, href)
    return None


# -- matching --------------------------------------------------------------
def _title_matches(card: Card, titles: list[str], year: int | None, strict_year: bool = False) -> bool:
    card_title, card_year = card.base_title
    wanted = {norm_title(split_title_year(t)[0]) for t in titles if t}
    if norm_title(card_title) not in wanted:
        return False
    if year and card_year and abs(card_year - year) > (0 if strict_year else 1):
        return False
    return True


def _ref_from_card(card: Card, kind: str, season: int | None, set_key: str | None, set_title: str | None) -> ImageRef:
    return ImageRef(
        source="tpdb",
        image_id=card.poster_id,
        kind=kind,
        season=season,
        url=f"{_base()}/api/assets/{card.poster_id}",
        thumb_url=card.preview,
        designer=card.uploader,
        set_key=set_key,
        set_title=set_title,
        label=card.title,
    )


def cards_to_images(cards: list[Card], item_type: str, titles: list[str] | str, year: int | None,
                    set_key: str | None, set_title: str | None) -> list[ImageRef]:
    if isinstance(titles, str):
        titles = [titles]
    images = []
    for card in cards:
        if not _title_matches(card, titles, year):
            continue
        if item_type == "movie":
            if card.poster_type in ("collection", "category") or card.season is not None:
                continue
            images.append(_ref_from_card(card, "movie", None, set_key, set_title))
            continue
        season = card.season
        images.append(_ref_from_card(card, "show" if season is None else "season", season, set_key, set_title))
    images.sort(key=lambda ref: (ref.kind != "show", ref.season if ref.season is not None else -1))
    return images


# -- public API -------------------------------------------------------------
async def search(title: str, year: int | None, item_type: str) -> list[dict]:
    section = "movies" if item_type == "movie" else "shows"
    key = f"tpdb:search:{section}:{norm_title(title)}"
    cached = cache.get(key)
    if cached is None:
        url = f"{_base()}/search?term={quote_plus(title)}&section={section}"
        html, _ = await get_page(url)
        cached = parse_search(html)
        cache.put(key, cached, 24 * 3600)
    return cached


async def find_item_page(title: str, year: int | None, item_type: str) -> tuple[str | None, bool]:
    """TPDB page for a title; the flag says whether the match is unambiguous."""
    results = await search(title, year, item_type)
    wanted = norm_title(title)
    exact = [r for r in results if norm_title(r["title"]) == wanted and (not year or not r["year"] or r["year"] == year)]
    if len(exact) == 1:
        return exact[0]["url"], bool(year and exact[0]["year"] == year)
    near = [r for r in results if norm_title(r["title"]) == wanted and year and r["year"] and abs(r["year"] - year) <= 1]
    if len(exact) > 1:
        same_year = [r for r in exact if r["year"] == year]
        if len(same_year) == 1:
            return same_year[0]["url"], True
        return exact[0]["url"], False
    if len(near) == 1:
        return near[0]["url"], False
    return None, False


async def item_cards(page_url: str, fresh: bool = False) -> list[Card]:
    key = f"tpdb:item:{page_url}"
    cached = None if fresh else cache.get(key)
    if cached is not None:
        return [Card(**card) for card in cached]
    cards: list[Card] = []
    url: str | None = page_url
    pages = max(1, int(settings.get("tpdb_pages") or 1))
    seen_ids: set[str] = set()
    for index in range(pages):
        if not url:
            break
        html, final = await get_page(url)
        if index == 0:
            page_title = parse_page_title(html)
            if page_title:
                cache.put(f"tpdb:title:{page_url}", page_title, 30 * 86400)
        new = [card for card in parse_cards(html) if card.poster_id not in seen_ids]
        if not new:
            break
        seen_ids.update(card.poster_id for card in new)
        cards.extend(new)
        url = next_page(html, final)
    cache.put(key, [card.__dict__ for card in cards], 12 * 3600)
    return cards


def _titles(item: dict, page_url: str | None) -> list[str]:
    titles = [item.get("search_title") or "", item.get("title") or ""]
    if page_url:
        stored = cache.get(f"tpdb:title:{page_url}")
        if stored:
            titles.append(stored)
    return [t for t in titles if t]


async def sets_for_item(item: dict, page_url: str) -> list[PosterSet]:
    """Group the posters on a title's TPDB page by set (seasons follow with the set page)."""
    year = item.get("search_year") or item.get("year")
    cards = await item_cards(page_url)
    title = _titles(item, page_url)
    groups: dict[str, PosterSet] = {}
    for card in cards:
        key = card.set_id or f"poster:{card.poster_id}"
        poster_set = groups.get(key)
        if poster_set is None:
            poster_set = PosterSet(
                source="tpdb",
                set_key=key,
                title=f"Set by {card.uploader}" if card.set_id else "Single poster",
                designer=card.uploader,
                url=f"{_base()}/set/{card.set_id}" if card.set_id else f"{_base()}/poster/{card.poster_id}",
                complete=not card.set_id,
            )
            groups[key] = poster_set
        poster_set.images.extend(
            cards_to_images([card], item["type"], title, year, key, poster_set.title)
        )
    return [s for s in groups.values() if s.images]


async def get_set(set_key: str, item: dict, fresh: bool = False) -> PosterSet | None:
    if set_key.startswith("poster:"):
        return None
    cache_key = f"tpdb:set:{set_key}"
    cached = None if fresh else cache.get(cache_key)
    if cached is None:
        html, _ = await get_page(f"{_base()}/set/{set_key}")
        cached = {"meta": parse_set_meta(html), "cards": [card.__dict__ for card in parse_cards(html)]}
        cache.put(cache_key, cached, 12 * 3600)
    meta = cached["meta"]
    cards = [Card(**card) for card in cached["cards"]]
    title = _titles(item, item.get("tpdb_url"))
    year = item.get("search_year") or item.get("year")
    set_title = meta.get("title") or f"Set {set_key}"
    images = cards_to_images(cards, item["type"], title, year, set_key, set_title)
    designer = meta.get("designer") or (cards[0].uploader if cards else "")
    for image in images:
        image.designer = image.designer or designer
    return PosterSet("tpdb", set_key, set_title, designer, f"{_base()}/set/{set_key}", None, None, images, True,
                     extra={"cards": len(cards)})


# -- downloads ---------------------------------------------------------------
def _looks_like_image(response: httpx.Response) -> bool:
    kind = response.headers.get("content-type", "").lower()
    return kind.startswith("image/") or response.content[:3] == b"\xff\xd8\xff" or response.content[:4] == b"\x89PNG"


async def login() -> str:
    email, password = settings.get("tpdb_email"), settings.get("tpdb_password")
    if not email or not password:
        raise LoginRequired("Downloads from ThePosterDB need an account (Settings › Sources)")
    async with _lock:
        await _polite()
        async with http.client(timeout=45.0, headers={"User-Agent": _ua()}, cookies=_cookies()) as client:
            try:
                page = await client.get(f"{_base()}/login")
                if is_challenge(page.status_code, page.text):
                    raise SourceError("ThePosterDB shows a Cloudflare check when signing in")
                soup = BeautifulSoup(page.text, "lxml")
                token_el = soup.select_one("input[name=_token]")
                meta = soup.select_one("meta[name=csrf-token]")
                token = (token_el.get("value") if token_el else None) or (meta.get("content") if meta else None)
                if not token:
                    raise SourceError("ThePosterDB sign-in page not recognised")
                response = await client.post(
                    f"{_base()}/login",
                    data={"_token": token, "login": email, "password": password, "remember": "on"},
                    headers={"Referer": f"{_base()}/login", "Origin": _base()},
                )
            except httpx.HTTPError as exc:
                raise SourceError(f"Cannot reach ThePosterDB: {exc.__class__.__name__}") from exc
            final_path = urlsplit(str(response.url)).path
            if response.status_code >= 400 or final_path.rstrip("/").endswith("/login"):
                raise LoginRequired("Sign-in to ThePosterDB failed (check email/username and password)")
            _save_cookies(client.cookies)
            soup = BeautifulSoup(response.text, "lxml")
            user = soup.select_one("a[href*='/user/']")
            return user.get_text(strip=True) if user else email


async def fetch(ref: ImageRef, thumb: bool = False) -> tuple[bytes, str]:
    if thumb and ref.thumb_url:
        async with http.client(timeout=40.0, headers={"User-Agent": _ua(), "Referer": _base() + "/"}) as client:
            try:
                response = await client.get(ref.thumb_url)
            except httpx.HTTPError as exc:
                raise SourceError(f"Cannot load ThePosterDB preview: {exc.__class__.__name__}") from exc
            if response.status_code < 400 and _looks_like_image(response):
                return response.content, response.headers.get("content-type", "")
    urls = [f"{_base()}/api/assets/{ref.image_id}", f"{_base()}/api/assets/{ref.image_id}/download"]
    logged_in = False
    for attempt in range(2):
        for url in urls:
            async with _lock:
                await _polite()
                async with http.client(timeout=90.0, headers={"User-Agent": _ua(), "Referer": _base() + "/",
                                                              "Accept": "image/jpeg,image/png,image/*;q=0.8"},
                                       cookies=_cookies()) as client:
                    try:
                        response = await client.get(url)
                    except httpx.HTTPError as exc:
                        raise SourceError(f"Cannot load ThePosterDB image: {exc.__class__.__name__}") from exc
                    if response.status_code < 400 and _looks_like_image(response):
                        _save_cookies(client.cookies)
                        return response.content, response.headers.get("content-type", "")
                    if is_challenge(response.status_code, response.text):
                        raise SourceError("ThePosterDB shows a Cloudflare check when downloading")
        if logged_in or attempt:
            break
        await login()
        logged_in = True
    raise SourceError("Cannot load ThePosterDB image (sign-in needed or poster removed)")
