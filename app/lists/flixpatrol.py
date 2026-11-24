"""FlixPatrol TOP 10 pages: per platform and country, one table per section.

The world lists come from the streaming overview (/top10/), which holds the world TOP 10 of every
big platform; platforms missing there are read from /top10/<platform>/world/. Kind and year come
from each title's FlixPatrol page (cached), as in the FlixPatrol script.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import ClassVar

from bs4 import BeautifulSoup

from .. import cache, settings
from .base import Adapter, Fetched, Field, RawEntry, SourceError, slug
from .scrape import NotFound, page

PAGE_HOURS = 2
TITLE_DAYS = 30

PLATFORMS = {
    "netflix": "Netflix",
    "disney": "Disney+",
    "amazon-prime": "Amazon Prime",
    "hbo-max": "HBO Max",
    "apple-tv": "Apple TV",
    "paramount-plus": "Paramount+",
    "hulu": "Hulu",
    "peacock": "Peacock",
}
SECTIONS = {
    "movies": "Movies",
    "shows": "TV Shows",
    "overall": "Overall",
    "kids-movies": "Kids Movies",
    "kids-shows": "Kids TV Shows",
}
SECTION_KIND = {"movies": "movie", "shows": "show", "kids-movies": "movie", "kids-shows": "show", "overall": None}
# "TOP 10 Movies" (country pages) and "TOP Movies on Netflix on <date>"; never "TOP Movies and TV Shows ... by day".
_HEADING = re.compile(r"^top(?:\s*\d+)?\s+(kids\s+)?(movies|tv\s*shows|overall)\b(?!\s+and\b)", re.IGNORECASE)
_TOC = re.compile(r"^toc-(.+)-(movies|tv-shows)$")
_DAYS = re.compile(r"^(\d+)\s*d$")
_CHANGE = re.compile(r"^(?:[+\-\u2212\u2013]\s*\d+|[\-\u2013]|new)$", re.IGNORECASE)
_POINTS = re.compile(r"^\d[\d,']*$")
_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_SLUG_YEAR = re.compile(r"/title/[^/]*-((?:19|20)\d{2})/?$")
_IMDB = re.compile(r"imdb\.com/title/(tt\d+)")


def section_of(heading: str) -> str | None:
    match = _HEADING.match(" ".join(heading.split()))
    if not match:
        return None
    kids, name = match.group(1), match.group(2).lower().replace(" ", "")
    if name == "overall":
        return None if kids else "overall"
    base = "movies" if name == "movies" else "shows"
    return f"kids-{base}" if kids else base


def _change(text: str) -> int | str:
    """Rank change badge: "+1", "-2", "–" (same) or "NEW"."""
    if text.lower() == "new":
        return "new"
    digits = text.replace("\u2212", "-").replace("\u2013", "-").replace(" ", "")
    return 0 if digits == "-" else int(digits)


def parse_rows(table) -> list[dict]:
    """Rows of a TOP 10 table: rank, title, path, and what the page shows of change, days, points, originals."""
    rows: list[dict] = []
    for row in table.find_all("tr"):
        link = row.find("a", href=re.compile(r"^/title/"))
        title = link.get_text(" ", strip=True) if link else ""
        if not title:
            continue
        cells = row.find_all("td")
        rank_text = cells[0].get_text(strip=True).rstrip(".") if cells else ""
        entry = {
            "rank": int(rank_text) if rank_text.isdigit() else len(rows) + 1,
            "title": title,
            "path": link["href"],
            "change": None,
            "days": None,
            "points": None,
            "original": row.find(title=re.compile(r"\boriginal\b", re.IGNORECASE)) is not None,
        }
        for cell in cells[1:]:
            if cell.find("a", href=re.compile(r"^/title/")):
                continue
            text = cell.get_text(" ", strip=True).replace("\xa0", " ")
            if not text:
                continue
            if (days := _DAYS.match(text)) and entry["days"] is None:
                entry["days"] = int(days.group(1))
            elif _CHANGE.match(text) and entry["change"] is None:
                entry["change"] = _change(text)
            elif _POINTS.match(text) and entry["points"] is None:
                entry["points"] = int(re.sub(r"[,']", "", text))
        rows.append(entry)
    return rows


def parse_top10(html: str) -> dict[str, list[dict]]:
    """Sections of a TOP 10 page: {"movies": [row, ...], "shows": [...], ...}; the first table per section counts."""
    soup = BeautifulSoup(html, "lxml")
    sections: dict[str, list[dict]] = {}
    for heading in soup.find_all(["h2", "h3"]):
        name = section_of(heading.get_text(" ", strip=True))
        if not name or name in sections:
            continue
        table = heading.find_next("table")
        rows = parse_rows(table) if table is not None else []
        if rows:
            sections[name] = rows
    return sections


def parse_overview(html: str) -> dict[str, dict[str, list[dict]]]:
    """The streaming overview (/top10/): {platform: {"movies": rows, "shows": rows}} from the headings' ids."""
    soup = BeautifulSoup(html, "lxml")
    platforms: dict[str, dict[str, list[dict]]] = {}
    for heading in soup.find_all(["h2", "h3"], id=_TOC):
        platform, kind = _TOC.match(heading["id"]).groups()
        table = heading.find_next("table")
        rows = parse_rows(table) if table is not None else []
        if rows:
            platforms.setdefault(platform, {})["movies" if kind == "movies" else "shows"] = rows
    return platforms


def parse_title(html: str, path: str = "") -> dict:
    """Kind, year and premiere from a FlixPatrol title page."""
    soup = BeautifulSoup(html, "lxml")
    info: dict = {"kind": None, "year": None, "premiere": None, "country": None}
    header = soup.select_one(".info-grid-header")
    if header is not None:
        for block in header.select("div[title]"):
            label = block.get("title") or ""
            text = block.get_text("", strip=True)
            if label == "Premiere":
                found = _DATE.search(text)
                if found and _real_year(int(found.group(3))):
                    month, day, year = (int(part) for part in found.groups())
                    info["premiere"] = f"{year:04d}-{month:02d}-{day:02d}"
                    info["year"] = year
            elif label.isdigit() and info["kind"] is None:
                first = block.find("div")
                kind_text = (first.get_text(" ", strip=True) if first else text).lower()
                if "show" in kind_text or "series" in kind_text:
                    info["kind"] = "show"
                elif "movie" in kind_text or "film" in kind_text:
                    info["kind"] = "movie"
        flag = header.select_one("[class*='fflag-']")
        if flag is not None:
            codes = [name[6:] for name in flag.get("class", []) if name.startswith("fflag-") and len(name) == 8]
            info["country"] = codes[0] if codes else None
    if info["year"] is None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (TypeError, ValueError):
                continue
            created = str(data.get("dateCreated") or "") if isinstance(data, dict) else ""
            if re.match(r"^\d{4}", created) and _real_year(int(created[:4])):
                info["year"] = int(created[:4])
                info["premiere"] = info["premiere"] or created[:10]
                break
    if info["year"] is None:
        info["year"] = slug_year(path)
    for link in soup.find_all("a", href=_IMDB):
        info["imdb"] = _IMDB.search(link["href"]).group(1)  # like the script: an IMDb link decides
        break
    return info


def _real_year(year: int) -> bool:
    """FlixPatrol uses 1900 for an unknown year (e.g. /title/spike-1900/)."""
    return 1901 <= year <= 2100


def slug_year(path: str) -> int | None:
    found = _SLUG_YEAR.search(path)
    year = int(found.group(1)) if found else None
    return year if year and _real_year(year) else None


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _name(value: str, names: dict[str, str]) -> str:
    return names.get(value) or " ".join(part.capitalize() for part in value.split("-"))


class FlixPatrol(Adapter):
    key = "flixpatrol"
    label = "FlixPatrol"
    fields: ClassVar[list[Field]] = [
        Field("platform", "Platform", "text", default="netflix", suggestions=list(PLATFORMS), placeholder="netflix"),
        Field("region", "Region", "text", default="world", suggestions=["world", "switzerland", "germany", "austria",
                                                                       "united-states", "united-kingdom"],
              placeholder="world"),
        Field("sections", "Sections", "multi", options=list(SECTIONS.items()), default=["movies", "shows"]),
        Field("limit", "Titles per section", "number", default=10, min=1, max=10),
    ]

    def clean(self, params: dict) -> dict:
        clean = super().clean(params)
        clean["platform"] = slug(clean["platform"], "Platform")
        clean["region"] = slug(clean["region"], "Region")
        return clean

    def describe(self, params: dict) -> str:
        sections = ", ".join(SECTIONS[name] for name in params["sections"])
        return f"{_name(params['platform'], PLATFORMS)} {_name(params['region'], {'world': 'World'})} – {sections}"

    def base(self) -> str:
        return (settings.get("flixpatrol_base") or "https://flixpatrol.com").rstrip("/")

    async def top10(self, platform: str, region: str) -> dict[str, list[dict]]:
        key = f"fp:top10:{platform}/{region}:{_today()}"
        sections = cache.get(key)
        if sections is None:
            try:
                html = await page(f"{self.base()}/top10/{platform}/{region}/", self.delay(), "FlixPatrol")
            except NotFound:
                raise SourceError(f"FlixPatrol has no TOP 10 page for {platform}/{region}") from None
            sections = parse_top10(html)
            if not sections:
                raise SourceError("FlixPatrol page without TOP 10 tables (layout changed?)")
            cache.put(key, sections, PAGE_HOURS * 3600)
        return sections

    async def overview(self) -> dict[str, dict[str, list[dict]]]:
        key = f"fp:overview:{_today()}"
        platforms = cache.get(key)
        if platforms is None:
            html = await page(f"{self.base()}/top10/", self.delay(), "FlixPatrol")
            platforms = parse_overview(html)
            cache.put(key, platforms, PAGE_HOURS * 3600)
        return platforms

    async def sections(self, platform: str, region: str, wanted: list[str]) -> dict[str, list[dict]]:
        """TOP 10 sections for a platform and region. World: the overview first, the platform's page for the rest."""
        if region != "world":
            return await self.top10(platform, region)
        try:
            found = dict((await self.overview()).get(platform, {}))
        except NotFound:
            found = {}
        if any(name not in found for name in wanted):
            try:
                for name, rows in (await self.top10(platform, region)).items():
                    found.setdefault(name, rows)
            except SourceError:
                if not found:
                    raise
        return found

    async def title_info(self, path: str) -> dict:
        key = f"fp:title:{path}"
        info = cache.get(key)
        if info is None:
            try:
                html = await page(f"{self.base()}{path}", self.delay(), "FlixPatrol")
            except NotFound:
                info = {"kind": None, "year": slug_year(path), "premiere": None, "country": None, "missing": True}
                cache.put(key, info, 86400)
                return info
            info = parse_title(html, path)
            cache.put(key, info, TITLE_DAYS * 86400)
        return info

    def delay(self) -> float:
        try:
            return max(0.0, float(settings.get("flixpatrol_delay")))
        except (TypeError, ValueError):
            return 1.0

    async def fetch(self, params: dict) -> Fetched:
        sections = await self.sections(params["platform"], params["region"], params["sections"])
        missing = [SECTIONS[name] for name in params["sections"] if name not in sections]
        found = ", ".join(SECTIONS[name] for name in sections) or "none"
        if len(missing) == len(params["sections"]):
            raise SourceError(f"Sections not on this page. Found: {found}")
        entries: list[RawEntry] = []
        for name in params["sections"]:
            for row in sections.get(name, [])[: params["limit"]]:
                info = await self.title_info(row["path"])
                kind = SECTION_KIND[name] or info.get("kind")
                entries.append(RawEntry(
                    position=len(entries) + 1,
                    title=row["title"],
                    year=info.get("year"),
                    kind=kind,
                    ids={"imdb": info["imdb"]} if info.get("imdb") else {},
                    url=f"{self.base()}{row['path']}",
                    extra={"section": name, "rank": row["rank"], "change": row.get("change"),
                           "days": row.get("days"), "points": row.get("points"),
                           "original": row.get("original") or False, "premiere": info.get("premiere"),
                           "country": info.get("country"), "page_kind": info.get("kind")},
                ))
        notes = [f"Not on this page: {', '.join(missing)}. Found: {found}"] if missing else []
        return Fetched(entries, notes)
