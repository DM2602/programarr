"""Node types: sources (the list adapters), filters with their ports, and the Radarr and Sonarr outputs.

Each type has a form (fields), checks its settings (clean) and describes them in one line (summary).
Filters send every title to exactly one port.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar

from .. import arr, db
from ..arr.library import TAG, clean_tag
from ..lists import runs
from ..lists.base import Adapter, Field, SourceError, clean_values
from ..lists.mdblist import list_ref
from ..media import mdblist
from .codes import COUNTRIES, GENRE_NAMES, GENRES, LANGUAGES, genre_ids


class ConfigError(Exception):
    """A node's settings are incomplete or wrong; the message is meant for the form."""


@dataclass
class Port:
    key: str
    label: str


@dataclass
class Title:
    """One title on its way through a flow."""
    media_id: int
    media: dict
    rank: int | None = None
    in_plex: bool | None = None


@dataclass
class Verdict:
    port: str
    detail: str = ""


MATCH = [Port("match", "Match"), Port("no_match", "No match")]


class NodeType:
    key: ClassVar[str] = ""
    label: ClassVar[str] = ""
    category: ClassVar[str] = ""  # source | filter | output
    inputs: ClassVar[bool] = True
    ports: ClassVar[list[Port]] = []
    fields: ClassVar[list[Field]] = []

    def missing(self) -> str | None:
        """What has to be set up first, or None."""
        return None

    def clean(self, config: dict) -> dict:
        return clean_values(self.fields, config or {}, ConfigError)

    def summary(self, config: dict) -> str:
        return self.label

    def info(self) -> dict:
        return {
            "type": self.key,
            "label": self.label,
            "category": self.category,
            "inputs": self.inputs,
            "ports": [{"key": port.key, "label": port.label} for port in self.ports],
            "fields": [spec.as_dict() for spec in self.fields],
            "missing": self.missing(),
        }


# -- sources ----------------------------------------------------------------------------------------

class SourceNode(NodeType):
    category: ClassVar[str] = "source"
    inputs: ClassVar[bool] = False
    ports: ClassVar[list[Port]] = [Port("out", "Titles")]

    def __init__(self, adapter: Adapter):
        self.adapter = adapter
        self.key = adapter.key  # type: ignore[misc]
        self.label = adapter.label  # type: ignore[misc]
        self.fields = adapter.fields  # type: ignore[misc]

    def missing(self) -> str | None:
        return self.adapter.missing()

    def clean(self, config: dict) -> dict:
        try:
            return self.adapter.clean(config or {})
        except SourceError as exc:
            raise ConfigError(str(exc)) from exc

    def summary(self, config: dict) -> str:
        return self.adapter.describe(config)


# -- filters ----------------------------------------------------------------------------------------

class Filter(NodeType):
    category: ClassVar[str] = "filter"
    ports: ClassVar[list[Port]] = MATCH

    def test(self, config: dict, title: Title) -> Verdict:
        raise NotImplementedError


RATINGS = {
    "imdb": ("IMDb rating", "imdb_rating", 0, 10),
    "imdb_votes": ("IMDb votes", "imdb_votes", 0, 100_000_000),
    "rt_critics": ("Rotten Tomatoes critics", "rt_critics", 0, 100),
    "rt_audience": ("Rotten Tomatoes audience", "rt_audience", 0, 100),
}
RATING_SHORT = {"imdb": "IMDb", "imdb_votes": "IMDb votes", "rt_critics": "RT critics", "rt_audience": "RT audience"}


def _number(value: float | None) -> str:
    if value is None:
        return "–"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}"
    return f"{int(value):,}".replace(",", "'")


class RatingFilter(Filter):
    key = "rating"
    label = "Rating"
    fields: ClassVar[list[Field]] = [
        Field("metric", "Rating", "select", options=[(key, value[0]) for key, value in RATINGS.items()], default="imdb"),
        Field("min", "At least", "decimal", default=7.0, step=0.1, min=0),
        Field("missing", "Without rating", "select", options=[("no_match", "No match"), ("match", "Match")],
              default="no_match"),
    ]

    def missing(self) -> str | None:
        return None if mdblist.configured() else "Ratings come from MDBList: set the API key under Settings › Lists"

    def clean(self, config: dict) -> dict:
        clean = super().clean(config)
        label, _, low, high = RATINGS[clean["metric"]]
        if not low <= clean["min"] <= high:
            raise ConfigError(f"{label}: {low}–{_number(high)}")
        if clean["metric"] in ("imdb_votes", "rt_critics", "rt_audience"):
            clean["min"] = int(clean["min"])
        return clean

    def summary(self, config: dict) -> str:
        unit = "%" if config["metric"].startswith("rt_") else ""
        return f"{RATING_SHORT[config['metric']]} ≥ {_number(config['min'])}{unit}"

    def test(self, config: dict, title: Title) -> Verdict:
        _, column, _, _ = RATINGS[config["metric"]]
        value = title.media.get(column)
        name = RATING_SHORT[config["metric"]]
        if value is None:
            return Verdict(config["missing"], f"No {name} rating" if config["metric"] != "imdb_votes" else "No IMDb votes")
        unit = "%" if config["metric"].startswith("rt_") else ""
        if value >= config["min"]:
            return Verdict("match", f"{name} {_number(value)}{unit} ≥ {_number(config['min'])}{unit}")
        return Verdict("no_match", f"{name} {_number(value)}{unit} < {_number(config['min'])}{unit}")


class YearFilter(Filter):
    key = "year"
    label = "Release"
    fields: ClassVar[list[Field]] = [
        Field("mode", "Release", "select", options=[("range", "Year"), ("recent", "Within the last")], default="recent"),
        Field("from", "From", "number", default=None, required=False, min=1880, max=2100, when={"mode": ["range"]}),
        Field("to", "To", "number", default=None, required=False, min=1880, max=2100, when={"mode": ["range"]}),
        Field("months", "Months", "number", default=24, min=1, max=1200, when={"mode": ["recent"]}),
    ]

    def clean(self, config: dict) -> dict:
        clean = super().clean(config)
        if clean["mode"] == "range":
            if clean["from"] is None and clean["to"] is None:
                raise ConfigError("Set a first or last year")
            if clean["from"] is not None and clean["to"] is not None and clean["from"] > clean["to"]:
                raise ConfigError("The first year comes after the last")
        return clean

    def summary(self, config: dict) -> str:
        if config["mode"] == "recent":
            months = config["months"]
            if months % 12 == 0:
                years = months // 12
                return f"Released in the last {'year' if years == 1 else f'{years} years'}"
            return f"Released in the last {months} months"
        low, high = config.get("from"), config.get("to")
        if low and high:
            return f"Released {low}–{high}" if low != high else f"Released in {low}"
        return f"Released {low} or later" if low else f"Released {high} or earlier"

    def test(self, config: dict, title: Title) -> Verdict:
        released = title.media.get("release_date")
        year = title.media.get("year")
        if config["mode"] == "range":
            if not year:
                return Verdict("no_match", "No release date")
            low, high = config.get("from"), config.get("to")
            inside = (low is None or year >= low) and (high is None or year <= high)
            return Verdict("match" if inside else "no_match", str(year))
        try:
            day = date.fromisoformat(str(released)[:10])
        except ValueError:
            return Verdict("no_match", "No release date")
        today = date.today()
        months = (today.year - day.year) * 12 + today.month - day.month - (1 if today.day < day.day else 0)
        inside = months < config["months"]
        return Verdict("match" if inside else "no_match", f"Released {day.isoformat()}")


GENRE_MODES = {"any": "Has any of", "all": "Has all of", "only": "Has only", "none": "Has none of"}


class GenreFilter(Filter):
    key = "genre"
    label = "Genre"
    fields: ClassVar[list[Field]] = [
        Field("mode", "Genres", "select", options=list(GENRE_MODES.items()), default="any"),
        Field("genres", "Genres", "multi", options=[(key, value[0]) for key, value in GENRES.items()], default=[]),
    ]

    def summary(self, config: dict) -> str:
        names = ", ".join(GENRES[key][0] for key in config["genres"])
        return f"{GENRE_MODES[config['mode']]} {names}"

    def test(self, config: dict, title: Title) -> Verdict:
        kind = title.media["kind"]
        have = set(_json_list(title.media.get("genres")))
        wanted = [set(genre_ids(key, kind)) for key in config["genres"]]
        wanted_ids = set().union(*wanted) if wanted else set()
        names = ", ".join(sorted(GENRE_NAMES.get(genre, str(genre)) for genre in have)) or "No genres"
        mode = config["mode"]
        if mode == "any":
            ok = bool(have & wanted_ids)
        elif mode == "all":
            ok = all(have & ids for ids in wanted)
        elif mode == "only":
            ok = bool(have) and have <= wanted_ids
        else:
            ok = not have & wanted_ids
        return Verdict("match" if ok else "no_match", names)


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    loaded = db.loads(value, [])
    return loaded if isinstance(loaded, list) else []


class CodeFilter(Filter):
    """Countries or the original language: any of the codes, or none of them."""
    column: ClassVar[str] = ""
    upper: ClassVar[bool] = True
    codes: ClassVar[list[str]] = []
    word: ClassVar[str] = ""

    def clean(self, config: dict) -> dict:
        clean = super().clean(config)
        values = []
        for value in clean["codes"]:
            code = value.strip().upper() if self.upper else value.strip().lower()
            if not re.fullmatch(r"[A-Za-z]{2}", code):
                raise ConfigError(f"{self.fields[1].label}: use two-letter codes, e.g. {self.codes[0]}")
            if code not in values:
                values.append(code)
        clean["codes"] = values
        return clean

    def summary(self, config: dict) -> str:
        codes = ", ".join(config["codes"])
        return f"{self.word} {codes}" if config["mode"] == "any" else f"Not {self.word.lower()} {codes}"

    def values(self, title: Title) -> list[str]:
        raise NotImplementedError

    def test(self, config: dict, title: Title) -> Verdict:
        have = self.values(title)
        hit = bool(set(have) & set(config["codes"]))
        detail = ", ".join(have) if have else "Unknown"
        if config["mode"] == "any":
            return Verdict("match" if hit else "no_match", detail)
        return Verdict("no_match" if hit else "match", detail)


class CountryFilter(CodeFilter):
    key = "country"
    label = "Country"
    word = "From"
    codes = COUNTRIES
    fields: ClassVar[list[Field]] = [
        Field("mode", "Countries", "select", options=[("any", "Any of"), ("none", "None of")], default="any"),
        Field("codes", "Countries", "tokens", default=[], suggestions=COUNTRIES, dynamic="countries",
              placeholder="US"),
    ]

    def values(self, title: Title) -> list[str]:
        return [str(code).upper() for code in _json_list(title.media.get("countries"))]


class LanguageFilter(CodeFilter):
    key = "language"
    label = "Original language"
    word = "In"
    upper = False
    codes = LANGUAGES
    fields: ClassVar[list[Field]] = [
        Field("mode", "Languages", "select", options=[("any", "Any of"), ("none", "None of")], default="any"),
        Field("codes", "Languages", "tokens", default=[], suggestions=LANGUAGES, dynamic="languages",
              placeholder="en"),
    ]

    def values(self, title: Title) -> list[str]:
        language = title.media.get("language")
        return [str(language).lower()] if language else []


class RankFilter(Filter):
    key = "rank"
    label = "List position"
    fields: ClassVar[list[Field]] = [Field("max", "Top", "number", default=10, min=1, max=1000)]

    def summary(self, config: dict) -> str:
        return f"Top {config['max']}"

    def test(self, config: dict, title: Title) -> Verdict:
        if title.rank is None:
            return Verdict("no_match", "No position")
        return Verdict("match" if title.rank <= config["max"] else "no_match", f"#{title.rank}")


class SeasonsFilter(Filter):
    key = "seasons"
    label = "Seasons"
    fields: ClassVar[list[Field]] = [
        Field("min", "At least", "number", default=None, required=False, min=0, max=200),
        Field("max", "At most", "number", default=None, required=False, min=0, max=200),
    ]

    def clean(self, config: dict) -> dict:
        clean = super().clean(config)
        if clean["min"] is None and clean["max"] is None:
            raise ConfigError("Set a lowest or highest number of seasons")
        if clean["min"] is not None and clean["max"] is not None and clean["min"] > clean["max"]:
            raise ConfigError("At least is more than at most")
        return clean

    def summary(self, config: dict) -> str:
        low, high = config.get("min"), config.get("max")
        if low is not None and high is not None:
            return f"{low}–{high} seasons" if low != high else f"{low} seasons"
        return f"At least {low} seasons" if low is not None else f"At most {high} seasons"

    def test(self, config: dict, title: Title) -> Verdict:
        if title.media["kind"] != "show":
            return Verdict("no_match", "Movie")
        count = title.media.get("seasons")
        if count is None:
            return Verdict("no_match", "Seasons unknown")
        low, high = config.get("min"), config.get("max")
        inside = (low is None or count >= low) and (high is None or count <= high)
        return Verdict("match" if inside else "no_match", f"{count} season{'s' if count != 1 else ''}")


class TypeSwitch(Filter):
    key = "type"
    label = "Movie or show"
    ports: ClassVar[list[Port]] = [Port("movie", "Movies"), Port("show", "Shows")]
    fields: ClassVar[list[Field]] = []

    def summary(self, config: dict) -> str:
        return "Movies and shows apart"

    def test(self, config: dict, title: Title) -> Verdict:
        kind = title.media["kind"]
        return Verdict(kind, "Movie" if kind == "movie" else "Show")


class PlexFilter(Filter):
    key = "plex"
    label = "In Plex"
    ports: ClassVar[list[Port]] = [Port("match", "In Plex"), Port("no_match", "Not in Plex")]
    fields: ClassVar[list[Field]] = []

    def summary(self, config: dict) -> str:
        return "Already in a Plex library"

    def test(self, config: dict, title: Title) -> Verdict:
        return Verdict("match", "In Plex") if title.in_plex else Verdict("no_match", "Not in Plex")


# -- outputs ----------------------------------------------------------------------------------------

OUTCOMES = {
    "added": "Added",
    "tagged": "Tags added",
    "present": "Already there",
    "skipped": "Skipped",
    "failed": "Failed",
}


def _compare_field() -> Field:
    return Field("compare", "Compare with MDBList", "tokens", default=[], required=False, dynamic="mdblist.lists",
                 placeholder="user/list-name")


class ArrOutput(NodeType):
    category: ClassVar[str] = "output"
    ports: ClassVar[list[Port]] = []
    service: ClassVar[str] = ""

    def missing(self) -> str | None:
        name = arr.label(self.service)
        return None if arr.configured(self.service) else f"Set up {name} under Settings › Services"

    def clean(self, config: dict) -> dict:
        clean = super().clean(config)
        try:
            clean["quality_profile"] = int(clean["quality_profile"])
        except (TypeError, ValueError):
            raise ConfigError("Quality profile: choose one") from None
        tags = []
        for value in clean["tags"]:
            tag = clean_tag(value)
            if not TAG.match(tag):
                raise ConfigError("Tags: use a–z, 0–9 and dashes")
            if tag not in tags:
                tags.append(tag)
        clean["tags"] = tags
        refs = []
        for value in clean["compare"]:
            try:
                ref = list_ref(value)
            except SourceError as exc:
                raise ConfigError(f"Compare with MDBList: {exc}") from exc
            if ref not in refs:
                refs.append(ref)
        clean["compare"] = refs
        return clean

    def summary(self, config: dict) -> str:
        parts = [config.get("root_folder") or "No root folder"]
        if config.get("tags"):
            parts.append("tags " + ", ".join(config["tags"]))
        return ", ".join(parts)


class RadarrOutput(ArrOutput):
    key = "radarr"
    label = "Radarr"
    service = "radarr"
    fields: ClassVar[list[Field]] = [
        Field("quality_profile", "Quality profile", "select", default="", dynamic="radarr.profiles"),
        Field("root_folder", "Root folder", "select", default="", dynamic="radarr.root_folders"),
        Field("tags", "Tags", "tokens", default=[], required=False, dynamic="radarr.tags", placeholder="programarr"),
        Field("monitored", "Monitor", "bool", default=True),
        Field("minimum_availability", "Minimum availability", "select",
              options=[("announced", "Announced"), ("inCinemas", "In cinemas"), ("released", "Released")],
              default="released"),
        Field("search", "Search on add", "bool", default=True),
        Field("limit", "New titles per run, at most", "number", default=20, min=1, max=500),
        _compare_field(),
    ]


SONARR_MONITOR = [
    ("all", "All episodes"),
    ("future", "Future episodes"),
    ("missing", "Missing episodes"),
    ("existing", "Existing episodes"),
    ("recent", "Recent episodes"),
    ("pilot", "Pilot episode"),
    ("firstSeason", "First season"),
    ("lastSeason", "Last season"),
    ("none", "None"),
]


class SonarrOutput(ArrOutput):
    key = "sonarr"
    label = "Sonarr"
    service = "sonarr"
    fields: ClassVar[list[Field]] = [
        Field("quality_profile", "Quality profile", "select", default="", dynamic="sonarr.profiles"),
        Field("root_folder", "Root folder", "select", default="", dynamic="sonarr.root_folders"),
        Field("tags", "Tags", "tokens", default=[], required=False, dynamic="sonarr.tags", placeholder="programarr"),
        Field("monitor", "Monitor", "select", options=SONARR_MONITOR, default="all"),
        Field("series_type", "Series type", "select",
              options=[("standard", "Standard"), ("daily", "Daily"), ("anime", "Anime")], default="standard"),
        Field("season_folder", "Season folders", "bool", default=True),
        Field("search", "Search for missing episodes", "bool", default=True),
        Field("limit", "New titles per run, at most", "number", default=20, min=1, max=500),
        _compare_field(),
    ]


@dataclass
class Registry:
    types: dict[str, NodeType] = field(default_factory=dict)

    def get(self, key: str) -> NodeType | None:
        return self.types.get(key)

    def infos(self) -> list[dict]:
        return [node.info() for node in self.types.values()]


def _build() -> Registry:
    registry = Registry()
    for adapter in runs.ADAPTERS.values():
        registry.types[adapter.key] = SourceNode(adapter)
    for node in (TypeSwitch(), RatingFilter(), YearFilter(), GenreFilter(), CountryFilter(), LanguageFilter(),
                 RankFilter(), SeasonsFilter(), PlexFilter(), RadarrOutput(), SonarrOutput()):
        registry.types[node.key] = node
    return registry


TYPES = _build()
