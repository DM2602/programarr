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
from ..arr.library import TAG, clean_tag, profile_name
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

    def ports_for(self, config: dict) -> list[Port]:
        """The outputs with checked settings (a filter's outputs can depend on them)."""
        return list(self.ports)

    def raw_ports(self, config: dict | None) -> list[Port]:
        """The outputs for settings that may not pass their checks, so connections stay while editing."""
        return list(self.ports)

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

MAX_RULES = 12
RULE_ID = re.compile(r"^[a-z0-9_-]{1,24}$")


class Filter(NodeType):
    """Sends each title on to one or more of its ports."""
    category: ClassVar[str] = "filter"
    ports: ClassVar[list[Port]] = MATCH

    def route(self, config: dict, title: Title) -> tuple[list[str], str]:
        """The ports a title goes to, and what the node saw (e.g. 'IMDb 7.4')."""
        raise NotImplementedError


class RuleFilter(Filter):
    """One output per rule, plus No match. With a single rule the ports are Match and No match.

    Settings: {"rules": [{"id", "label", ...rule fields}], "each": bool}. Older flat settings count as
    one rule with the id "match". "each": a title that fits several rules goes to each of them,
    otherwise only to the first.
    """
    fields: ClassVar[list[Field]] = []
    rule_fields: ClassVar[list[Field]] = []
    each_default: ClassVar[bool] = False

    def info(self) -> dict:
        info = super().info()
        info.update(fields=[spec.as_dict() for spec in self.rule_fields], rules=True, each_default=self.each_default)
        return info

    @staticmethod
    def raw_rules(config: dict | None) -> list[dict]:
        config = config or {}
        if isinstance(config.get("rules"), list):
            return [rule for rule in config["rules"] if isinstance(rule, dict)]
        return [{"id": "match", **{key: value for key, value in config.items() if key not in ("rules", "each")}}]

    @staticmethod
    def rule_id(rule: dict, index: int) -> str:
        return str(rule.get("id") or ("match" if index == 0 else f"r{index + 1}")).strip()

    def raw_ports(self, config: dict | None) -> list[Port]:
        rules = self.raw_rules(config)
        ports = [Port(self.rule_id(rule, index), str(rule.get("label") or "").strip()
                      or ("Match" if len(rules) == 1 else f"Output {index + 1}")) for index, rule in enumerate(rules)]
        return [*ports, Port("no_match", "No match")]

    def clean_rule(self, rule: dict) -> dict:
        return clean_values(self.rule_fields, rule, ConfigError)

    def clean(self, config: dict) -> dict:
        raw = self.raw_rules(config)
        if not raw:
            raise ConfigError("Add an output")
        if len(raw) > MAX_RULES:
            raise ConfigError(f"At most {MAX_RULES} outputs")
        rules: list[dict] = []
        for index, rule in enumerate(raw):
            rule_id = self.rule_id(rule, index)
            if not RULE_ID.match(rule_id) or rule_id == "no_match" or any(other["id"] == rule_id for other in rules):
                raise ConfigError("Two outputs have the same id")
            try:
                values = self.clean_rule(rule)
            except ConfigError as exc:
                raise ConfigError(f"Output {index + 1}: {exc}" if len(raw) > 1 else str(exc)) from exc
            label = " ".join(str(rule.get("label") or "").split())[:40]
            rules.append({"id": rule_id, "label": label, **values})
        each = (config or {}).get("each")
        return {"rules": rules, "each": self.each_default if each is None else bool(each)}

    def rule_summary(self, rule: dict) -> str:
        """The rule in words, for the node's line."""
        raise NotImplementedError

    def rule_label(self, rule: dict) -> str:
        """The rule in a few words, for its output."""
        return self.rule_summary(rule)

    def rule_test(self, rule: dict, title: Title) -> tuple[bool, str]:
        raise NotImplementedError

    def ports_for(self, config: dict) -> list[Port]:
        rules = config["rules"]
        if len(rules) == 1:
            ports = [Port(rules[0]["id"], rules[0]["label"] or "Match")]
        else:
            ports = [Port(rule["id"], rule["label"] or self.rule_label(rule)) for rule in rules]
        return [*ports, Port("no_match", "No match")]

    def summary(self, config: dict) -> str:
        rules = config["rules"]
        if len(rules) == 1:
            return self.rule_summary(rules[0])
        return f"{len(rules)} outputs, {'each' if config['each'] else 'first'} that fits"

    def route(self, config: dict, title: Title) -> tuple[list[str], str]:
        matched: list[str] = []
        details: list[str] = []
        for rule in config["rules"]:
            hit, detail = self.rule_test(rule, title)
            if detail and detail not in details:
                details.append(detail)
            if hit:
                matched.append(rule["id"])
                if not config["each"]:
                    break
        return matched or ["no_match"], ", ".join(details)


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


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    loaded = db.loads(value, [])
    return loaded if isinstance(loaded, list) else []


class RatingFilter(RuleFilter):
    key = "rating"
    label = "Rating"
    rule_fields: ClassVar[list[Field]] = [
        Field("metric", "Rating", "select", options=[(key, value[0]) for key, value in RATINGS.items()], default="imdb"),
        Field("min", "At least", "decimal", default=7.0, step=0.1, min=0),
        Field("missing", "Without rating", "select", options=[("no_match", "No match"), ("match", "Match")],
              default="no_match"),
    ]

    def missing(self) -> str | None:
        return None if mdblist.configured() else "Ratings come from MDBList: set the API key under Settings › Lists"

    def clean_rule(self, rule: dict) -> dict:
        clean = super().clean_rule(rule)
        label, _, low, high = RATINGS[clean["metric"]]
        if not low <= clean["min"] <= high:
            raise ConfigError(f"{label}: {low}–{_number(high)}")
        if clean["metric"] in ("imdb_votes", "rt_critics", "rt_audience"):
            clean["min"] = int(clean["min"])
        return clean

    def rule_summary(self, rule: dict) -> str:
        unit = "%" if rule["metric"].startswith("rt_") else ""
        return f"{RATING_SHORT[rule['metric']]} ≥ {_number(rule['min'])}{unit}"

    def rule_test(self, rule: dict, title: Title) -> tuple[bool, str]:
        _, column, _, _ = RATINGS[rule["metric"]]
        value = title.media.get(column)
        name = RATING_SHORT[rule["metric"]]
        if value is None:
            return rule["missing"] == "match", "No IMDb votes" if rule["metric"] == "imdb_votes" else f"No {name} rating"
        unit = "%" if rule["metric"].startswith("rt_") else ""
        return value >= rule["min"], f"{name} {_number(value)}{unit}"


class YearFilter(RuleFilter):
    key = "year"
    label = "Release"
    rule_fields: ClassVar[list[Field]] = [
        Field("mode", "Release", "select", options=[("range", "Year"), ("recent", "Within the last")], default="recent"),
        Field("from", "From", "number", default=None, required=False, min=1880, max=2100, when={"mode": ["range"]}),
        Field("to", "To", "number", default=None, required=False, min=1880, max=2100, when={"mode": ["range"]}),
        Field("months", "Months", "number", default=24, min=1, max=1200, when={"mode": ["recent"]}),
    ]

    def clean_rule(self, rule: dict) -> dict:
        clean = super().clean_rule(rule)
        if clean["mode"] == "range":
            if clean["from"] is None and clean["to"] is None:
                raise ConfigError("Set a first or last year")
            if clean["from"] is not None and clean["to"] is not None and clean["from"] > clean["to"]:
                raise ConfigError("The first year comes after the last")
        return clean

    @staticmethod
    def _span(months: int) -> str:
        if months % 12 == 0:
            years = months // 12
            return "year" if years == 1 else f"{years} years"
        return f"{months} months"

    def rule_summary(self, rule: dict) -> str:
        if rule["mode"] == "recent":
            return f"Released in the last {self._span(rule['months'])}"
        return f"Released {self.rule_label(rule)}" if rule.get("from") and rule.get("to") else \
            f"Released {rule['from']} or later" if rule.get("from") else f"Released {rule['to']} or earlier"

    def rule_label(self, rule: dict) -> str:
        if rule["mode"] == "recent":
            return f"Last {self._span(rule['months'])}"
        low, high = rule.get("from"), rule.get("to")
        if low and high:
            return f"{low}–{high}" if low != high else str(low)
        return f"{low} or later" if low else f"{high} or earlier"

    def rule_test(self, rule: dict, title: Title) -> tuple[bool, str]:
        if rule["mode"] == "range":
            year = title.media.get("year")
            if not year:
                return False, "No release date"
            low, high = rule.get("from"), rule.get("to")
            return (low is None or year >= low) and (high is None or year <= high), str(year)
        try:
            day = date.fromisoformat(str(title.media.get("release_date"))[:10])
        except ValueError:
            return False, "No release date"
        today = date.today()
        months = (today.year - day.year) * 12 + today.month - day.month - (1 if today.day < day.day else 0)
        return months < rule["months"], f"Released {day.isoformat()}"


GENRE_MODES = {"any": "Has any of", "all": "Has all of", "only": "Has only", "none": "Has none of"}


class GenreFilter(RuleFilter):
    key = "genre"
    label = "Genre"
    each_default = True
    rule_fields: ClassVar[list[Field]] = [
        Field("mode", "Genres", "select", options=list(GENRE_MODES.items()), default="any"),
        Field("genres", "Genres", "multi", options=[(key, value[0]) for key, value in GENRES.items()], default=[]),
    ]

    def rule_summary(self, rule: dict) -> str:
        names = ", ".join(GENRES[key][0] for key in rule["genres"])
        return f"{GENRE_MODES[rule['mode']]} {names}"

    def rule_label(self, rule: dict) -> str:
        names = [GENRES[key][0] for key in rule["genres"]]
        if rule["mode"] == "all":
            return " and ".join(names)
        if rule["mode"] == "only":
            return f"Only {', '.join(names)}"
        if rule["mode"] == "none":
            return f"Not {', '.join(names)}"
        return ", ".join(names)

    def rule_test(self, rule: dict, title: Title) -> tuple[bool, str]:
        kind = title.media["kind"]
        have = set(_json_list(title.media.get("genres")))
        wanted = [set(genre_ids(key, kind)) for key in rule["genres"]]
        wanted_ids = set().union(*wanted) if wanted else set()
        names = ", ".join(sorted(GENRE_NAMES.get(genre, str(genre)) for genre in have)) or "No genres"
        mode = rule["mode"]
        if mode == "any":
            hit = bool(have & wanted_ids)
        elif mode == "all":
            hit = all(have & ids for ids in wanted)
        elif mode == "only":
            hit = bool(have) and have <= wanted_ids
        else:
            hit = not have & wanted_ids
        return hit, names


class CodeFilter(RuleFilter):
    """Countries or the original language: any of the codes, or none of them."""
    each_default = True
    upper: ClassVar[bool] = True
    codes: ClassVar[list[str]] = []
    word: ClassVar[str] = ""

    def clean_rule(self, rule: dict) -> dict:
        clean = super().clean_rule(rule)
        values = []
        for value in clean["codes"]:
            code = value.strip().upper() if self.upper else value.strip().lower()
            if not re.fullmatch(r"[A-Za-z]{2}", code):
                raise ConfigError(f"{self.rule_fields[1].label}: use two-letter codes, e.g. {self.codes[0]}")
            if code not in values:
                values.append(code)
        clean["codes"] = values
        return clean

    def rule_summary(self, rule: dict) -> str:
        codes = ", ".join(rule["codes"])
        return f"{self.word} {codes}" if rule["mode"] == "any" else f"Not {self.word.lower()} {codes}"

    def rule_label(self, rule: dict) -> str:
        codes = ", ".join(rule["codes"])
        return codes if rule["mode"] == "any" else f"Not {codes}"

    def values(self, title: Title) -> list[str]:
        raise NotImplementedError

    def rule_test(self, rule: dict, title: Title) -> tuple[bool, str]:
        have = self.values(title)
        hit = bool(set(have) & set(rule["codes"]))
        detail = ", ".join(have) if have else "Unknown"
        return (hit if rule["mode"] == "any" else not hit), detail


class CountryFilter(CodeFilter):
    key = "country"
    label = "Country"
    word = "From"
    codes = COUNTRIES
    rule_fields: ClassVar[list[Field]] = [
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
    rule_fields: ClassVar[list[Field]] = [
        Field("mode", "Languages", "select", options=[("any", "Any of"), ("none", "None of")], default="any"),
        Field("codes", "Languages", "tokens", default=[], suggestions=LANGUAGES, dynamic="languages",
              placeholder="en"),
    ]

    def values(self, title: Title) -> list[str]:
        language = title.media.get("language")
        return [str(language).lower()] if language else []


class RankFilter(RuleFilter):
    key = "rank"
    label = "List position"
    rule_fields: ClassVar[list[Field]] = [Field("max", "Top", "number", default=10, min=1, max=1000)]

    def rule_summary(self, rule: dict) -> str:
        return f"Top {rule['max']}"

    def rule_test(self, rule: dict, title: Title) -> tuple[bool, str]:
        if title.rank is None:
            return False, "No position"
        return title.rank <= rule["max"], f"#{title.rank}"


class SeasonsFilter(RuleFilter):
    key = "seasons"
    label = "Seasons"
    rule_fields: ClassVar[list[Field]] = [
        Field("min", "At least", "number", default=None, required=False, min=0, max=200),
        Field("max", "At most", "number", default=None, required=False, min=0, max=200),
    ]

    def clean_rule(self, rule: dict) -> dict:
        clean = super().clean_rule(rule)
        if clean["min"] is None and clean["max"] is None:
            raise ConfigError("Set a lowest or highest number of seasons")
        if clean["min"] is not None and clean["max"] is not None and clean["min"] > clean["max"]:
            raise ConfigError("At least is more than at most")
        return clean

    def rule_summary(self, rule: dict) -> str:
        low, high = rule.get("min"), rule.get("max")
        if low is not None and high is not None:
            return f"{low}–{high} seasons" if low != high else f"{low} seasons"
        return f"At least {low} seasons" if low is not None else f"At most {high} seasons"

    def rule_test(self, rule: dict, title: Title) -> tuple[bool, str]:
        if title.media["kind"] != "show":
            return False, "Movie"
        count = title.media.get("seasons")
        if count is None:
            return False, "Seasons unknown"
        low, high = rule.get("min"), rule.get("max")
        inside = (low is None or count >= low) and (high is None or count <= high)
        return inside, f"{count} season{'s' if count != 1 else ''}"


class TypeSwitch(Filter):
    key = "type"
    label = "Movie or show"
    ports: ClassVar[list[Port]] = [Port("movie", "Movies"), Port("show", "Shows")]
    fields: ClassVar[list[Field]] = []

    def summary(self, config: dict) -> str:
        return "Movies and shows apart"

    def route(self, config: dict, title: Title) -> tuple[list[str], str]:
        return [title.media["kind"]], ""


class PlexFilter(Filter):
    key = "plex"
    label = "In Plex"
    ports: ClassVar[list[Port]] = [Port("match", "In Plex"), Port("no_match", "Not in Plex")]
    fields: ClassVar[list[Field]] = []

    def summary(self, config: dict) -> str:
        return "Already in a Plex library"

    def route(self, config: dict, title: Title) -> tuple[list[str], str]:
        return (["match"], "In Plex") if title.in_plex else (["no_match"], "Not in Plex")


# -- outputs ----------------------------------------------------------------------------------------

OUTCOMES = {
    "added": "Added",
    "tagged": "Tags added",  # runs from before 26 Nov 2026 only
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

    def facts(self, config: dict) -> list[dict]:
        """Profile, folder and tags, labelled, for the output cards of a run."""
        profile = config.get("quality_profile")
        if profile in (None, ""):
            profile_text = "None"
        else:
            profile_text = profile_name(self.service, profile) or f"ID {profile}"
        facts = [{"label": "Profile", "value": profile_text},
                 {"label": "Folder", "value": config.get("root_folder") or "None"}]
        if config.get("tags"):
            facts.append({"label": "Tags", "value": ", ".join(config["tags"])})
        return facts

    def summary(self, config: dict) -> str:
        facts = {fact["label"]: fact["value"] for fact in self.facts(config)}
        parts = [facts["Profile"], facts["Folder"]]
        if "Tags" in facts:
            parts.append("tags " + facts["Tags"])
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
