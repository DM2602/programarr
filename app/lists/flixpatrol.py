"""FlixPatrol TOP 10 pages: per platform and country (or world), one table per section.

Kind and year come from each title's FlixPatrol page (cached), as in the FlixPatrol script.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from .. import cache, settings
from .base import Adapter, Fetched, Field, RawEntry, SourceError, slug
from .scrape import NotFound, page

PAGE_HOURS = 2
TITLE_DAYS = 30

PLATFORMS = {
    "netflix": "Netflix",
    "disney": "Disney+",
    "amazon-prime": "Prime Video",
    "hbo-max": "HBO Max",
    "apple-tv": "Apple TV+",
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
_HEADING = re.compile(r"^top\s*\d+\s+(kids\s+)?(movies|tv\s*shows|overall)\b", re.I)
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


def parse_top10(html: str) -> dict[str, list[dict]]:
    """Sections of a TOP 10 page: {"movies": [{"rank", "title", "path", "stat"}], ...}."""
    soup = BeautifulSoup(html, "lxml")
    sections: dict[str, list[dict]] = {}
    for heading in soup.find_all(["h2", "h3"]):
        name = section_of(heading.get_text(" ", strip=True))
        if not name or name in sections:
            continue
        table = heading.find_next("table")
        if table is None:
            continue
        rows = []
        for row in table.find_all("tr"):
            link = row.find("a", href=re.compile(r"^/title/"))
            title = link.get_text(" ", strip=True) if link else ""
            if not title:
                continue
            cells = row.find_all("td")
            rank_text = cells[0].get_text(strip=True).rstrip(".") if cells else ""
            rows.append({
                "rank": int(rank_text) if rank_text.isdigit() else len(rows) + 1,
                "title": title,
                "path": link["href"],
                "stat": (cells[-1].get_text(" ", strip=True).replace("\xa0", " ") or None) if len(cells) > 2 else None,
            })
        if rows:
            sections[name] = rows
    return sections


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


def _name(value: str, names: dict[str, str]) -> str:
    return names.get(value) or " ".join(part.capitalize() for part in value.split("-"))


class FlixPatrol(Adapter):
    key = "flixpatrol"
    label = "FlixPatrol"
    fields = [
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
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"fp:top10:{platform}/{region}:{day}"
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
        sections = await self.top10(params["platform"], params["region"])
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
                    extra={"section": name, "rank": row["rank"], "stat": row.get("stat"),
                           "premiere": info.get("premiere"), "country": info.get("country"),
                           "page_kind": info.get("kind")},
                ))
        notes = [f"Not on this page: {', '.join(missing)}. Found: {found}"] if missing else []
        return Fetched(entries, notes)
