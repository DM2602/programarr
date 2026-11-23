"""The media cache: one row per movie or show, filled from TMDB, ratings from MDBList."""
from __future__ import annotations

from .. import db
from . import tmdb

META_DAYS = 14


def get(media_id: int) -> dict | None:
    return db.get().one("SELECT * FROM media_cache WHERE id=?", (media_id,))


def find(kind: str, tmdb_id: int) -> dict | None:
    return db.get().one("SELECT * FROM media_cache WHERE kind=? AND tmdb_id=?", (kind, int(tmdb_id)))


def _row_from_details(kind: str, data: dict) -> dict:
    external = data.get("external_ids") or {}
    if kind == "movie":
        date = data.get("release_date") or None
        countries = [c.get("iso_3166_1") for c in data.get("production_countries") or [] if c.get("iso_3166_1")]
        seasons = None
    else:
        date = data.get("first_air_date") or None
        countries = list(data.get("origin_country") or [])
        seasons = data.get("number_of_seasons")
    return {
        "kind": kind,
        "tmdb_id": int(data["id"]),
        "imdb_id": external.get("imdb_id") or data.get("imdb_id") or None,
        "tvdb_id": external.get("tvdb_id") if kind == "show" else None,
        "title": data.get("title") or data.get("name") or "",
        "original_title": data.get("original_title") or data.get("original_name") or None,
        "year": tmdb.year_of(date),
        "release_date": date,
        "genres": db.dumps([g["id"] for g in data.get("genres") or [] if "id" in g]),
        "language": data.get("original_language") or None,
        "countries": db.dumps(countries),
        "seasons": seasons,
        "status": data.get("status") or None,
        "poster_path": data.get("poster_path") or None,
        "popularity": float(data.get("popularity") or 0),
        "meta_at": db.now(),
    }


def save_details(kind: str, data: dict) -> int:
    row = _row_from_details(kind, data)
    database = db.get()
    with database.lock:
        database.upsert("media_cache", ("kind", "tmdb_id"), row)
        return database.scalar("SELECT id FROM media_cache WHERE kind=? AND tmdb_id=?", (kind, row["tmdb_id"]))


async def ensure(kind: str, tmdb_id: int, max_age_days: int = META_DAYS) -> int | None:
    """Media id for a TMDB title, loading or refreshing its details when needed. None if TMDB lacks it."""
    row = find(kind, tmdb_id)
    if row and row.get("meta_at") and row["meta_at"] > db.now() - max_age_days * 86400:
        return row["id"]
    data = await tmdb.details(kind, tmdb_id)
    if data is None:
        return row["id"] if row else None
    return save_details(kind, data)


async def by_external(source: str, value: str | int, kind: str | None) -> int | None:
    """Media id for an IMDb or TVDB id, via the cache or TMDB's lookup."""
    column = {"imdb_id": "imdb_id", "tvdb_id": "tvdb_id"}[source]
    sql = f"SELECT id FROM media_cache WHERE {column}=?" + (" AND kind=?" if kind else "")
    found = db.get().scalar(sql, (value, kind) if kind else (value,))
    if found:
        return found
    for found_kind, tmdb_id in await tmdb.find(str(value), source):
        if kind and found_kind != kind:
            continue
        return await ensure(found_kind, tmdb_id)
    return None


def in_plex(media_ids: list[int]) -> dict[int, str]:
    """Which of these titles Plex already has: media id -> Plex item key (matched by TMDB, TVDB or IMDb id)."""
    if not media_ids:
        return {}
    marks = ",".join("?" for _ in media_ids)
    rows = db.get().query(
        f"""SELECT m.id, MIN(i.key) AS item_key FROM media_cache m JOIN items i ON i.gone=0 AND i.type=m.kind AND (
              (m.tmdb_id IS NOT NULL AND i.tmdb_id=CAST(m.tmdb_id AS TEXT))
           OR (m.tvdb_id IS NOT NULL AND i.tvdb_id=CAST(m.tvdb_id AS TEXT))
           OR (m.imdb_id IS NOT NULL AND i.imdb_id=m.imdb_id))
           WHERE m.id IN ({marks}) GROUP BY m.id""",
        list(media_ids),
    )
    return {row["id"]: row["item_key"] for row in rows}


def view(row: dict) -> dict:
    """Media row as the API shows it."""
    return {
        "id": row["id"],
        "kind": row["kind"],
        "tmdb_id": row.get("tmdb_id"),
        "imdb_id": row.get("imdb_id"),
        "tvdb_id": row.get("tvdb_id"),
        "title": row["title"],
        "original_title": row.get("original_title"),
        "year": row.get("year"),
        "status": row.get("status"),
        "seasons": row.get("seasons"),
        "poster": tmdb.poster_url(row.get("poster_path")),
        "imdb_rating": row.get("imdb_rating"),
        "imdb_votes": row.get("imdb_votes"),
        "rt_critics": row.get("rt_critics"),
        "rt_audience": row.get("rt_audience"),
        "ratings_at": row.get("ratings_at"),
    }
