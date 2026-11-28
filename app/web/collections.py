"""Collections: the ones flows keep (titles, visibility, poster), the others in Plex, and the queue."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db, settings
from ..collections import posters
from ..collections import store
from ..collections import sync
from ..media import store as media_store
from ..plex import PlexError
from ..posters import refsign
from ..posters.sources import tpdb
from ..posters.sources.base import SourceError
from .common import start_job
from .images import plex_image_url
from .schemas import (
    CollectionDetail,
    CollectionImages,
    CollectionsPage,
    CollectionUpdate,
    JobStarted,
    KeysBody,
    Ok,
    OtherCollections,
    PlexLibraries,
    QueuePage,
    TokenBody,
    TpdbResults,
)

router = APIRouter(prefix="/api", tags=["collections"])


def _collection_or_404(collection_id: int) -> dict:
    collection = store.get(collection_id)
    if collection is None:
        raise HTTPException(404, "Collection not found")
    return collection


def _libraries() -> dict[str, dict]:
    return {row["key"]: row for row in db.get().query("SELECT key, title, type, enabled FROM libraries")}


def _brief(row: dict, libraries: dict[str, dict], counts: dict[int, dict]) -> dict:
    lib = libraries.get(row["library_key"]) or {}
    if "flow_name" in row:  # store.all_rows() reads the flow along
        flow = {"name": row["flow_name"], "dry_run": row["flow_dry_run"]} if row["flow_name"] is not None else None
    else:
        flow = db.get().one("SELECT name, dry_run FROM flows WHERE id=?", (row["flow_id"],)) if row["flow_id"] else None
    version = row["poster_applied"] if row["poster"] else row["synced_at"]
    return {
        "id": row["id"],
        "title": row["title"] or row["name_template"],
        "name_template": row["name_template"],
        "library_key": row["library_key"],
        "library": lib.get("title") or row["library_key"],
        "kind": "show" if lib.get("type") == "show" else "movie",
        "flow_id": row["flow_id"] if flow else None,
        "flow_name": flow["name"] if flow else None,
        "in_flow": bool(row["in_flow"] and flow),
        "live": bool(row["in_flow"] and flow and not flow["dry_run"]),
        "sync_mode": row["sync_mode"],
        "item_order": row["item_order"],
        "max_items": row["max_items"] or 0,
        "plex_key": row["plex_key"],
        "show_recommended": bool(row["show_recommended"]),
        "show_home": bool(row["show_home"]),
        "show_shared": bool(row["show_shared"]),
        "image": f"/img/collection/{row['id']}?v={row['poster'] or ''}{version or 0}",
        "poster_source": row["poster_source"] if row["poster"] else None,
        "synced_at": row["synced_at"],
        "error": row["error"],
        "counts": counts.get(row["id"], {"in_plex": 0, "queued": 0, "missing": 0}),
    }


def _counts() -> dict[int, dict]:
    result: dict[int, dict] = {}
    for row in db.get().query("SELECT collection_id, state, COUNT(*) AS n FROM collection_items "
                              "GROUP BY collection_id, state"):
        result.setdefault(row["collection_id"], {"in_plex": 0, "queued": 0, "missing": 0})[row["state"]] = row["n"]
    return result


def _detail(collection: dict) -> dict:
    rows = db.get().query(
        "SELECT ci.*, m.* FROM collection_items ci JOIN media_cache m ON m.id=ci.media_id "
        "WHERE ci.collection_id=? ORDER BY CASE ci.state WHEN 'in_plex' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, "
        "ci.position, m.title", (collection["id"],))
    titles = [{
        "media": media_store.view(row),
        "state": row["state"],
        "position": row["position"],
        "service": row["service"],
        "item_key": row["item_key"],
        "queued_at": row["queued_at"],
        "expires_at": row["expires_at"],
    } for row in rows]
    return {**_brief(collection, _libraries(), _counts()), "titles": titles}


@router.get("/collections", response_model=CollectionsPage)
async def collections():
    libraries, counts = _libraries(), _counts()
    return {"collections": [_brief(row, libraries, counts) for row in store.all_rows()]}


@router.get("/collections/other", response_model=OtherCollections)
async def other_collections():
    """Collections in the selected libraries that Programarr did not make: Plex' franchises (automatic),
    Kometa's and those made by hand."""
    try:
        found = await sync.others()
    except PlexError as exc:
        return {"collections": [], "error": str(exc)}
    for row in found:
        row["image"] = plex_image_url(row.pop("thumb"))
    return {"collections": found, "error": None}


@router.post("/collections/other/delete", response_model=JobStarted)
async def delete_other_collections(body: KeysBody):
    """Only collections made by hand or by tools; Programarr's own and Plex' franchises stay."""
    keys = [str(key) for key in body.keys if str(key).strip()]
    if not keys:
        raise HTTPException(400, "Choose at least one collection")
    return start_job("collections_delete", f"Delete {len(keys)} collections in Plex", sync.delete_others, keys)


@router.get("/collections/{collection_id}", response_model=CollectionDetail)
async def collection(collection_id: int):
    return _detail(_collection_or_404(collection_id))


@router.put("/collections/{collection_id}", response_model=CollectionDetail)
async def update_collection(collection_id: int, body: CollectionUpdate):
    """Where Plex shows the collection: Recommended in the library, on the own Home, on friends' Home."""
    _collection_or_404(collection_id)
    values = {key: int(value) for key, value in body.model_dump(exclude_none=True).items()}
    if values:
        store.update(collection_id, values)
        try:
            await sync.apply_visibility(collection_id)
        except PlexError as exc:
            raise HTTPException(502, f"Saved, but Plex did not take it: {exc}") from exc
    return _detail(store.get(collection_id))


@router.delete("/collections/{collection_id}", response_model=Ok)
async def delete_collection(collection_id: int, keep: bool = False):
    """Only collections whose output is no longer in a flow; keep leaves the collection in Plex."""
    brief = _brief(_collection_or_404(collection_id), _libraries(), {})
    if brief["in_flow"]:
        raise HTTPException(409, f"Remove the output from the flow {brief['flow_name']} first")
    try:
        await sync.delete(collection_id, keep_in_plex=keep)
    except PlexError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, "message": "Removed from Programarr" if keep else "Collection deleted"}


# -- posters ------------------------------------------------------------------------------------

@router.get("/collections/{collection_id}/tpdb", response_model=TpdbResults)
async def tpdb_search(collection_id: int, q: str = ""):
    """Collection pages on ThePosterDB for a name."""
    collection = _collection_or_404(collection_id)
    term = " ".join((q or collection["title"] or collection["name_template"]).split())
    if not term:
        return {"results": [], "error": None}
    try:
        return {"results": await posters.tpdb_search(term), "error": None}
    except SourceError as exc:
        return {"results": [], "error": str(exc)}


@router.get("/collections/{collection_id}/tpdb/posters", response_model=CollectionImages)
async def tpdb_posters(collection_id: int, url: str):
    _collection_or_404(collection_id)
    match = tpdb._POSTERS_HREF.search(url or "")  # noqa: SLF001 - same pattern as the parser
    if not match:
        raise HTTPException(400, "Not a ThePosterDB page link (…/posters/12345)")
    page = f"{settings.get('tpdb_base').rstrip('/')}/posters/{match.group(1)}"
    try:
        refs = await posters.tpdb_posters(page)
    except SourceError as exc:
        return {"images": [], "error": str(exc)}
    return {"images": [refsign.image_dict(ref) for ref in refs], "error": None}


@router.post("/collections/{collection_id}/poster", response_model=JobStarted)
async def set_poster(collection_id: int, body: TokenBody):
    """A poster from the picker: ThePosterDB, an upload or a link (the token from /api/upload or /api/fetch-url)."""
    collection = _collection_or_404(collection_id)
    try:
        ref = refsign.ref(body.token)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc
    title = collection["title"] or collection["name_template"]
    return start_job("collection_poster", f"Set poster: {title}", sync.set_poster, collection_id, ref.to_dict())


# -- queue --------------------------------------------------------------------------------------

@router.get("/queue", response_model=QueuePage)
async def queue():
    """Titles in Radarr or Sonarr waiting for Plex, with the collection they go into."""
    rows = db.get().query(
        "SELECT ci.collection_id, ci.service, ci.queued_at, ci.expires_at, "
        "COALESCE(c.title, c.name_template) AS collection, m.* FROM collection_items ci "
        "JOIN collections c ON c.id=ci.collection_id JOIN media_cache m ON m.id=ci.media_id "
        "WHERE ci.state='queued' ORDER BY ci.queued_at DESC, m.title")
    return {
        "entries": [{"collection_id": row["collection_id"], "collection": row["collection"],
                     "media": media_store.view(row), "service": row["service"], "queued_at": row["queued_at"],
                     "expires_at": row["expires_at"]} for row in rows],
        "days": store.queue_days(),
    }


@router.delete("/queue/{collection_id}/{media_id}", response_model=Ok)
async def unqueue(collection_id: int, media_id: int):
    if not sync.unqueue(collection_id, media_id):
        raise HTTPException(404, "Not in the queue")
    return {"ok": True, "message": "Removed from the queue"}


# -- options ------------------------------------------------------------------------------------

@router.get("/plex/libraries", response_model=PlexLibraries)
async def plex_libraries():
    """The libraries under Settings › Plex, for the flow editor."""
    rows = db.get().query("SELECT key, title, type FROM libraries WHERE enabled=1 "
                          "ORDER BY CASE type WHEN 'movie' THEN 0 ELSE 1 END, title")
    return {"libraries": rows}
