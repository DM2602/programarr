"""From a raw list entry to a media_cache row: corrections first, then IDs, then a strict title search.

The title passes follow the FlixPatrol script: exact title and year, then year ±1, then the title
when one side has no year. TMDB's alternative titles count as exact titles. Whatever none of these
settles is left for a person, with the closest candidates.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from .. import cache, db
from ..lists.base import RawEntry
from ..util import fold
from . import store, tmdb

MATCH_DAYS = 7
RETRY_HOURS = 12
# A second hit in the same pass with at least this share of the first one's popularity needs a person.
AMBIGUOUS = 0.5
MIN_SIMILARITY = 0.6


@dataclass
class Match:
    status: str  # matched | uncertain | failed | excluded
    method: str | None = None
    media_id: int | None = None
    score: float | None = None
    candidates: list[dict] = field(default_factory=list)
    error: str | None = None


def match_key(title: str | None) -> str:
    """Title reduced to letters and digits: "Spider-Man: No Way Home" -> "spidermannowayhome"."""
    text = fold(title or "").replace("&", " and ")
    return re.sub(r"[\W_]+", "", text)


def fingerprint(entry: RawEntry) -> str:
    """Stable key for the same raw entry across runs; corrections are stored against it."""
    kind = entry.kind if entry.kind in ("movie", "show") else "any"
    for name in ("tmdb", "imdb", "tvdb"):
        if entry.ids.get(name):
            return f"{kind}|{name}:{entry.ids[name]}"
    return f"{kind}|{match_key(entry.title)}|{entry.year or ''}"


# -- corrections and exclusions -------------------------------------------------------------------

def override_for(source: str, fp: str) -> dict | None:
    """The correction for this entry: one for this source wins over one for all sources ('')."""
    return db.get().one(
        "SELECT * FROM parser_overrides WHERE fingerprint=? AND source IN (?, '') ORDER BY source DESC LIMIT 1",
        (fp, source),
    )


def excluded(media_id: int | None, scope: str = "global") -> bool:
    """Own exclusion, or on the import list exclusions of Radarr (movies) or Sonarr (shows)."""
    if not media_id:
        return False
    database = db.get()
    if database.scalar("SELECT 1 FROM exclusions WHERE media_id=? AND scope=?", (media_id, scope)):
        return True
    if scope != "global":
        return False
    return bool(database.scalar(
        "SELECT 1 FROM media_cache m JOIN arr_exclusions a ON a.state!='remove' AND ("
        "(a.service='radarr' AND m.kind='movie' AND a.external_id=m.tmdb_id) OR "
        "(a.service='sonarr' AND m.kind='show' AND a.external_id=m.tvdb_id)) WHERE m.id=?", (media_id,)))


# -- resolving -------------------------------------------------------------------------------------

async def resolve(source: str, entry: RawEntry) -> tuple[str, Match]:
    fp = fingerprint(entry)
    rule = override_for(source, fp)
    if rule and rule["ignore"]:
        return fp, Match("excluded", "ignored", score=1.0)
    if rule and rule["media_id"] and store.get(rule["media_id"]):
        return fp, _exclusions(Match("matched", "fixed", rule["media_id"], 1.0))
    try:
        match = await _by_ids(entry)
        if match is None:
            match = await _by_title(entry, fp)
    except tmdb.TmdbError as exc:
        return fp, Match("failed", "error", error=str(exc))
    return fp, _exclusions(match)


def _exclusions(match: Match) -> Match:
    if match.status == "matched" and excluded(match.media_id):
        match.status = "excluded"
    return match


async def _by_ids(entry: RawEntry) -> Match | None:
    ids = entry.ids or {}
    if not any(ids.get(name) for name in ("tmdb", "imdb", "tvdb")):
        return None
    kind = entry.kind if entry.kind in ("movie", "show") else None
    media_id = None
    if ids.get("tmdb") and kind:
        media_id = await store.ensure(kind, int(ids["tmdb"]))
        row = store.get(media_id) if media_id else None
        if row and ids.get("imdb") and row.get("imdb_id") and row["imdb_id"] != ids["imdb"]:
            media_id = None  # the two IDs disagree: the IMDb ID decides
    if not media_id and ids.get("imdb"):
        media_id = await store.by_external("imdb_id", str(ids["imdb"]), kind)
    if not media_id and ids.get("tvdb") and kind != "movie":
        media_id = await store.by_external("tvdb_id", int(ids["tvdb"]), "show")
    if media_id:
        return Match("matched", "id", media_id, 1.0)
    if entry.title:
        return None  # IDs unknown to TMDB: try the title
    return Match("failed", error="IDs not found on TMDB")


async def _by_title(entry: RawEntry, fp: str) -> Match:
    key = match_key(entry.title)
    if not key:
        return Match("failed", error="No title")
    cached = cache.get(f"match:{fp}")
    if cached:
        media_id = None
        if cached.get("tmdb_id"):
            media_id = await store.ensure(cached["kind"], cached["tmdb_id"])
        if media_id or not cached.get("tmdb_id"):
            return Match(cached["status"], cached.get("method"), media_id, cached.get("score"),
                         cached.get("candidates") or [], cached.get("error"))
    kinds = [entry.kind] if entry.kind in ("movie", "show") else ["movie", "show"]
    year = entry.year
    results: list[dict] = []
    found = None
    if year:
        results = await _search(kinds, entry.title, year)
        found = _pick(results, key, year)
    if not found:
        results = _merge(results, await _search(kinds, entry.title, None))
        found = _pick(results, key, year)
    candidates = _rank(results, key, year)
    if not found:
        found = await _pick_alternative(candidates, key, year)
    outcome = _decide(found, candidates)
    chosen = outcome.pop("chosen", None)
    if chosen:
        outcome["kind"], outcome["tmdb_id"] = chosen["kind"], chosen["tmdb_id"]
    ttl = MATCH_DAYS * 86400 if outcome["status"] == "matched" else RETRY_HOURS * 3600
    cache.put(f"match:{fp}", outcome, ttl)
    media_id = await store.ensure(chosen["kind"], chosen["tmdb_id"]) if chosen else None
    if chosen and not media_id:
        return Match("failed", error="TMDB has no details for this title", candidates=candidates)
    return Match(outcome["status"], outcome.get("method"), media_id, outcome.get("score"),
                 outcome["candidates"], outcome.get("error"))


async def _search(kinds: list[str], title: str, year: int | None) -> list[dict]:
    results: list[dict] = []
    for kind in kinds:
        results = _merge(results, await tmdb.search(kind, title, year))
    return results


def _merge(first: list[dict], second: list[dict]) -> list[dict]:
    seen = {(row["kind"], row["tmdb_id"]) for row in first}
    return first + [row for row in second if (row["kind"], row["tmdb_id"]) not in seen]


def _title_hit(candidate: dict, key: str) -> bool:
    return key in (match_key(candidate.get("title")), match_key(candidate.get("original_title")))


def _pick(results: list[dict], key: str, year: int | None) -> tuple[str, float, list[dict]] | None:
    """First pass with a hit: (method, score, hits sorted by popularity)."""
    hits = [row for row in results if _title_hit(row, key)]
    checks = (
        ("exact", 1.0, lambda row: year is not None and row["year"] == year),
        ("year±1", 0.9, lambda row: year is not None and row["year"] is not None and abs(row["year"] - year) == 1),
        ("title", 0.8, lambda row: year is None or row["year"] is None),
    )
    for method, score, test in checks:
        passed = sorted((row for row in hits if test(row)), key=lambda row: -row["popularity"])
        if passed:
            return method, score, passed
    return None


async def _pick_alternative(candidates: list[dict], key: str, year: int | None) -> tuple[str, float, list[dict]] | None:
    """TMDB's alternative titles, for the closest candidates whose year fits."""
    hits = []
    for row in candidates:
        if year is not None and row["year"] is not None and abs(row["year"] - year) > 1:
            continue
        titles = await tmdb.alternative_titles(row["kind"], row["tmdb_id"])
        if any(match_key(title) == key for title in titles):
            hits.append(row)
    if not hits:
        return None
    return "alt-title", 0.85, sorted(hits, key=lambda row: -row["popularity"])


def _similarity(row: dict, key: str, year: int | None) -> float:
    ratios = [difflib.SequenceMatcher(None, key, match_key(text)).ratio()
              for text in (row.get("title"), row.get("original_title")) if text]
    value = max(ratios or [0.0])
    if year and row.get("year"):
        value -= min(abs(row["year"] - year), 5) * 0.04
    return round(max(value, 0.0), 3)


def _rank(results: list[dict], key: str, year: int | None, limit: int = 5) -> list[dict]:
    ranked = [{**row, "similarity": _similarity(row, key, year)} for row in results]
    ranked.sort(key=lambda row: (-row["similarity"], -row["popularity"]))
    return ranked[:limit]


def _decide(found: tuple[str, float, list[dict]] | None, candidates: list[dict]) -> dict:
    if found:
        method, score, hits = found
        chosen = hits[0]
        rival = hits[1] if len(hits) > 1 else None
        if rival and rival["popularity"] >= AMBIGUOUS * chosen["popularity"]:
            options = _merge([{**row, "similarity": 1.0} for row in hits[:5]], candidates)[:5]
            return {"status": "uncertain", "method": method, "score": round(score * 0.5, 3),
                    "candidates": options, "error": "Several titles fit"}
        others = [row for row in candidates if (row["kind"], row["tmdb_id"]) != (chosen["kind"], chosen["tmdb_id"])]
        return {"status": "matched", "method": method, "score": score, "candidates": others[:4], "chosen": chosen}
    if candidates and candidates[0]["similarity"] >= MIN_SIMILARITY:
        return {"status": "uncertain", "method": "similar", "score": candidates[0]["similarity"],
                "candidates": candidates, "error": "No exact match"}
    return {"status": "failed", "method": None, "score": None, "candidates": candidates, "error": "Not found on TMDB"}
