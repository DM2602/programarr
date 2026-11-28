"""The media cache: one row per movie or show, filled from TMDB, ratings from MDBList.

A show's TVDB ID comes from TMDB. Where TMDB has no link to TVDB (often with new or less known
shows), it is taken from the list itself, from MDBList or, at a Sonarr output, from Sonarr.
"""
from __future__ import annotations

from .. import cache, db
from . import tmdb

META_DAYS = 14
TVDB_MISS_SECONDS = 86400  # Sonarr knew no TVDB ID: ask again after a day


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
    if kind == "show" and not row["tvdb_id"]:
        del row["tvdb_id"]  # keeps a TVDB ID found elsewhere until TMDB has its own
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


def note_tvdb(media_id: int, tvdb_id) -> bool:
    """A TVDB ID from elsewhere for a show that has none yet; True if it was taken."""
    try:
        value = int(tvdb_id)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    cursor = db.get().execute("UPDATE media_cache SET tvdb_id=? WHERE id=? AND kind='show' AND tvdb_id IS NULL",
                              (value, media_id))
    return bool(cursor.rowcount)


async def fill_tvdb(media_id: int) -> int | None:
    """The TVDB ID of a show, asking Sonarr when nobody else had one; Sonarr at most once a day per show."""
    row = get(media_id)
    if not row or row["kind"] != "show":
        return None
    if row.get("tvdb_id"):
        return int(row["tvdb_id"])
    from .. import arr  # only here does the media cache need Sonarr
    from ..arr import library
    if not arr.configured("sonarr") or cache.get(f"tvdb:miss:{media_id}"):
        return None
    try:
        found = await library.tvdb_id(row)
    except arr.ArrError:
        return None
    if not found:
        cache.put(f"tvdb:miss:{media_id}", True, TVDB_MISS_SECONDS)
        return None
    note_tvdb(media_id, found)
    return found


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


def in_plex(media_ids: list[int], library_key: str | None = None) -> dict[int, str]:
    """Which of these titles Plex already has: media id -> Plex item key (matched by TMDB, TVDB or IMDb id),
    in any library or in the one given."""
    if not media_ids:
        return {}
    marks = ",".join("?" for _ in media_ids)
    library = "AND i.library_key=? " if library_key else ""
    rows = db.get().query(
        f"""SELECT m.id, MIN(i.key) AS item_key FROM media_cache m JOIN items i ON i.gone=0 {library}AND i.type=m.kind
           AND ((m.tmdb_id IS NOT NULL AND i.tmdb_id=CAST(m.tmdb_id AS TEXT))
             OR (m.tvdb_id IS NOT NULL AND i.tvdb_id=CAST(m.tvdb_id AS TEXT))
             OR (m.imdb_id IS NOT NULL AND i.imdb_id=m.imdb_id))
           WHERE m.id IN ({marks}) GROUP BY m.id""",
        [library_key, *media_ids] if library_key else list(media_ids),
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
