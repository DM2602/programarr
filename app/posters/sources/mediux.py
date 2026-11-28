"""MediUX through its GraphQL API (personal token, same API as AURA)."""
from __future__ import annotations

import asyncio
import re

import httpx

from ... import cache, http, settings
from .base import ImageRef, PosterSet, SourceError

IMAGE_FIELDS = "id modified_on filesize language { display_name }"
SET_FIELDS = "id set_title user_created { username } date_created date_updated popularity"

Q_SHOW_SETS = f"""
query programarrShowSets($tmdb_id: ID!) {{
  shows_by_id(id: $tmdb_id) {{
    id title first_air_date tvdb_id
    show_sets {{
      {SET_FIELDS}
      show_poster {{ {IMAGE_FIELDS} }}
      season_posters {{ {IMAGE_FIELDS} season {{ season_number }} }}
    }}
  }}
}}"""

Q_SHOW_SET = f"""
query programarrShowSet($set_id: ID!) {{
  show_sets_by_id(id: $set_id) {{
    {SET_FIELDS}
    show_id {{ id title first_air_date }}
    show_poster {{ {IMAGE_FIELDS} }}
    season_posters {{ {IMAGE_FIELDS} season {{ season_number }} }}
  }}
}}"""

Q_MOVIE_SETS = f"""
query programarrMovieSets($tmdb_id: ID!) {{
  movies_by_id(id: $tmdb_id) {{
    id title release_date
    movie_sets {{
      {SET_FIELDS}
      movie_poster {{ {IMAGE_FIELDS} }}
    }}
    collection_id {{
      id collection_name
      movies {{
        id title release_date
        posters(filter: {{ collection_set: {{ id: {{ _nnull: true }} }} }}) {{
          {IMAGE_FIELDS}
          collection_set {{ {SET_FIELDS} }}
        }}
      }}
    }}
  }}
}}"""

Q_MOVIE_SET = f"""
query programarrMovieSet($set_id: ID!) {{
  movie_sets_by_id(id: $set_id) {{
    {SET_FIELDS}
    movie_id {{ id title release_date }}
    movie_poster {{ {IMAGE_FIELDS} }}
  }}
}}"""

Q_COLLECTION_SET = f"""
query programarrCollectionSet($set_id: ID!, $set_id_str: GraphQLStringOrFloat) {{
  collection_sets_by_id(id: $set_id) {{
    {SET_FIELDS}
    collection_id {{
      id collection_name
      movies {{
        id title release_date
        posters(filter: {{ collection_set: {{ id: {{ _eq: $set_id_str }} }} }}) {{ {IMAGE_FIELDS} }}
      }}
    }}
  }}
}}"""

Q_TVDB = """
query programarrTvdb($tvdb_id: String!) {
  shows(filter: { tvdb_id: { _eq: $tvdb_id } }) { id tvdb_id }
}"""

Q_FOLLOWS = """
query programarrFollows {
  user_follows(filter: { follower_id: { id: { _eq: "$CURRENT_USER" } } }) { followee_id { username } }
  user_hides(filter: { hider_id: { id: { _eq: "$CURRENT_USER" } } }) { hiding_id { username } }
}"""


def _api() -> str:
    return settings.get("mediux_api").rstrip("/")


def _site() -> str:
    return settings.get("mediux_site").rstrip("/")


def configured() -> bool:
    return bool(settings.get("mediux_token"))


def _version(modified: str | None) -> str:
    digits = re.sub(r"\D", "", modified or "")
    return digits[:14] or "0"


def image_url(image_id: str, modified: str | None, quality: str = "original") -> str:
    url = f"{_api()}/assets/{image_id}?v={_version(modified)}"
    if quality == "thumb":
        url += "&key=thumb"
    elif quality == "optimized":
        url += "&key=jpg"
    return url


def _auth_headers(token: str | None = None) -> dict:
    return {"Authorization": f"Bearer {token or settings.get('mediux_token')}"}


async def _graphql(query: str, variables: dict | None = None) -> dict:
    if not configured():
        raise SourceError("No MediUX token set (Settings › Sources)")
    body = {"query": query, "variables": variables or {}}
    async with http.client(timeout=60.0, headers={**_auth_headers(), "Accept": "application/json"}) as client:
        for attempt in range(3):
            try:
                response = await client.post(f"{_api()}/graphql", json=body)
            except httpx.HTTPError as exc:
                raise SourceError(f"Cannot reach MediUX: {exc.__class__.__name__}") from exc
            if response.status_code == 429 and attempt < 2:
                wait = response.headers.get("retry-after", "")
                await asyncio.sleep(min(float(wait) if wait.isdigit() else 3.0 * (attempt + 1), 15.0))
                continue
            break
    if response.status_code in (401, 403):
        raise SourceError("MediUX rejects the token")
    if response.status_code >= 400:
        raise SourceError(f"MediUX returned {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceError("Unexpected response from MediUX") from exc
    errors = payload.get("errors") or []
    data = payload.get("data") or {}
    if errors and not any(data.values() if isinstance(data, dict) else []):
        message = errors[0].get("message", "Error") if isinstance(errors[0], dict) else str(errors[0])
        if "forbidden" in message.lower() or "permission" in message.lower():
            # Directus answers unknown ids with FORBIDDEN as well.
            return {}
        raise SourceError(f"MediUX: {message}")
    return data


async def test(token: str | None = None) -> str:
    """Check the token; returns the MediUX user name."""
    token = token or settings.get("mediux_token")
    if not token:
        raise SourceError("No MediUX token set")
    async with http.client(timeout=20.0, headers={**_auth_headers(token), "Accept": "application/json"}) as client:
        try:
            response = await client.get(f"{_api()}/users/me")
        except httpx.HTTPError as exc:
            raise SourceError(f"Cannot reach MediUX: {exc.__class__.__name__}") from exc
    if response.status_code in (401, 403):
        raise SourceError("MediUX rejects the token")
    if response.status_code >= 400:
        raise SourceError(f"MediUX returned {response.status_code}")
    try:
        data = response.json().get("data", {})
    except ValueError:
        data = {}
    return data.get("username") or data.get("first_name") or "signed in"


def _language(image: dict) -> str | None:
    lang = image.get("language")
    if isinstance(lang, dict):
        return lang.get("display_name")
    return None


def _set_meta(raw: dict) -> dict:
    user = raw.get("user_created") or {}
    return {
        "title": raw.get("set_title") or "",
        "designer": (user.get("username") if isinstance(user, dict) else "") or "",
        "updated": raw.get("date_updated") or raw.get("date_created"),
        "popularity": raw.get("popularity"),
    }


def _ref(image: dict, kind: str, set_key: str, meta: dict, season: int | None = None, label: str = "") -> ImageRef:
    image_id = str(image.get("id"))
    modified = image.get("modified_on")
    return ImageRef(
        source="mediux",
        image_id=image_id,
        kind=kind,
        season=season,
        url=image_url(image_id, modified, settings.get("mediux_quality")),
        thumb_url=image_url(image_id, modified, "thumb"),
        modified=modified,
        language=_language(image),
        designer=meta["designer"],
        set_key=set_key,
        set_title=meta["title"],
        label=label,
    )


def _show_set(raw: dict) -> PosterSet:
    set_key = f"show:{raw.get('id')}"
    meta = _set_meta(raw)
    images: list[ImageRef] = []
    for image in raw.get("show_poster") or []:
        if image and image.get("id"):
            images.append(_ref(image, "show", set_key, meta, label="Show"))
    for image in raw.get("season_posters") or []:
        season = (image.get("season") or {}).get("season_number")
        if image and image.get("id") and season is not None:
            images.append(_ref(image, "season", set_key, meta, int(season)))
    images.sort(key=lambda ref: (ref.kind != "show", ref.season if ref.season is not None else -1))
    return PosterSet(
        source="mediux",
        set_key=set_key,
        title=meta["title"],
        designer=meta["designer"],
        url=f"{_site()}/sets/{raw.get('id')}",
        updated=meta["updated"],
        popularity=meta["popularity"],
        images=images,
    )


def _movie_set(raw: dict) -> PosterSet:
    set_key = f"movie:{raw.get('id')}"
    meta = _set_meta(raw)
    images = [_ref(img, "movie", set_key, meta, label="Movie") for img in raw.get("movie_poster") or [] if img and img.get("id")]
    return PosterSet("mediux", set_key, meta["title"], meta["designer"], f"{_site()}/sets/{raw.get('id')}",
                     meta["updated"], meta["popularity"], images)


def _collection_sets_for_movie(collection: dict | None, tmdb_id: str) -> list[PosterSet]:
    if not collection:
        return []
    sets: dict[str, PosterSet] = {}
    for movie in collection.get("movies") or []:
        if str(movie.get("id")) != str(tmdb_id):
            continue
        for image in movie.get("posters") or []:
            raw_set = image.get("collection_set") or {}
            if not raw_set.get("id"):
                continue
            set_key = f"collection:{raw_set['id']}"
            meta = _set_meta(raw_set)
            poster_set = sets.get(set_key)
            if poster_set is None:
                poster_set = PosterSet("mediux", set_key, meta["title"], meta["designer"],
                                       f"{_site()}/sets/{raw_set['id']}", meta["updated"], meta["popularity"], [],
                                       extra={"collection": collection.get("collection_name"),
                                              "collection_id": str(collection.get("id"))})
                sets[set_key] = poster_set
            poster_set.images.append(_ref(image, "movie", set_key, meta, label="Movie"))
    return list(sets.values())


async def show_sets(tmdb_id: str, fresh: bool = False) -> tuple[dict, list[PosterSet]]:
    key = f"mediux:show:{tmdb_id}"
    cached = None if fresh else cache.get(key)
    if cached is None:
        data = await _graphql(Q_SHOW_SETS, {"tmdb_id": str(tmdb_id)})
        cached = data.get("shows_by_id") or {}
        cache.put(key, cached, 6 * 3600)
    info = {"title": cached.get("title"), "date": cached.get("first_air_date"), "tvdb": cached.get("tvdb_id")}
    sets = [_show_set(raw) for raw in cached.get("show_sets") or [] if raw]
    return info, [s for s in sets if s.images]


async def movie_sets(tmdb_id: str, fresh: bool = False) -> tuple[dict, list[PosterSet]]:
    key = f"mediux:movie:{tmdb_id}"
    cached = None if fresh else cache.get(key)
    if cached is None:
        data = await _graphql(Q_MOVIE_SETS, {"tmdb_id": str(tmdb_id)})
        cached = data.get("movies_by_id") or {}
        cache.put(key, cached, 6 * 3600)
    info = {"title": cached.get("title"), "date": cached.get("release_date"),
            "collection": (cached.get("collection_id") or {}).get("collection_name"),
            "collection_id": str((cached.get("collection_id") or {}).get("id") or "") or None}
    sets = [_movie_set(raw) for raw in cached.get("movie_sets") or [] if raw]
    sets += _collection_sets_for_movie(cached.get("collection_id"), tmdb_id)
    return info, [s for s in sets if s.images]


async def get_set(set_key: str, tmdb_id: str | None = None) -> PosterSet | None:
    """Load one set fresh from MediUX (for bindings and update checks)."""
    kind, _, set_id = set_key.partition(":")
    if kind == "show":
        data = await _graphql(Q_SHOW_SET, {"set_id": set_id})
        raw = data.get("show_sets_by_id")
        return _show_set(raw) if raw else None
    if kind == "movie":
        data = await _graphql(Q_MOVIE_SET, {"set_id": set_id})
        raw = data.get("movie_sets_by_id")
        return _movie_set(raw) if raw else None
    if kind == "collection":
        data = await _graphql(Q_COLLECTION_SET, {"set_id": set_id, "set_id_str": set_id})
        raw = data.get("collection_sets_by_id")
        if not raw:
            return None
        meta = _set_meta(raw)
        collection = raw.get("collection_id") or {}
        poster_set = PosterSet("mediux", set_key, meta["title"], meta["designer"], f"{_site()}/sets/{set_id}",
                               meta["updated"], meta["popularity"], [],
                               extra={"collection": collection.get("collection_name"),
                                      "collection_id": str(collection.get("id") or "") or None,
                                      "movies": [str(m.get("id")) for m in collection.get("movies") or []
                                                 if m.get("posters")]})
        for movie in collection.get("movies") or []:
            if tmdb_id is None or str(movie.get("id")) == str(tmdb_id):
                for image in movie.get("posters") or []:
                    ref = _ref(image, "movie", set_key, meta, label=movie.get("title") or "Movie")
                    ref.extra["tmdb"] = str(movie.get("id"))
                    poster_set.images.append(ref)
        return poster_set
    raise SourceError(f"Unknown MediUX set type: {set_key}")


async def tmdb_for_tvdb(tvdb_id: str) -> str | None:
    data = await _graphql(Q_TVDB, {"tvdb_id": str(tvdb_id)})
    shows = data.get("shows") or []
    return str(shows[0]["id"]) if shows else None


async def follows() -> dict:
    data = await _graphql(Q_FOLLOWS)
    follow = [((row.get("followee_id") or {}).get("username") or "") for row in data.get("user_follows") or []]
    hide = [((row.get("hiding_id") or {}).get("username") or "") for row in data.get("user_hides") or []]
    return {"follow": [name for name in follow if name], "hide": [name for name in hide if name]}


async def fetch(ref: ImageRef, thumb: bool = False) -> tuple[bytes, str]:
    url = ref.thumb_url if thumb and ref.thumb_url else ref.url
    if not url:
        url = image_url(ref.image_id, ref.modified, "thumb" if thumb else settings.get("mediux_quality"))
    headers = {"Accept": "image/*"}
    if url.startswith(_api()):
        headers.update(_auth_headers())
    async with http.client(timeout=90.0, headers=headers) as client:
        for attempt in range(3):
            try:
                async with client.stream("GET", url) as response:
                    if response.status_code == 429 and attempt < 2:
                        await asyncio.sleep(3.0 * (attempt + 1))
                        continue
                    if response.status_code >= 400:
                        raise SourceError(f"Cannot load MediUX image ({response.status_code})")
                    data = await http.read_limited(response)
                    return data, response.headers.get("content-type", "")
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise SourceError(f"Cannot load MediUX image: {exc.__class__.__name__}") from exc
                await asyncio.sleep(2.0)
    raise SourceError("Cannot load MediUX image")
