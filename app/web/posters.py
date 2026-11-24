"""Poster API: library, one title, the picker, Review, Waiting, Match existing and History."""
from __future__ import annotations

import asyncio
import math

from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import arr, db, http, imaging, settings
from ..plex import PlexError
from ..posters import binding, catalog, detect, library, missing, refsign, reviews, sources, status, tasks, views
from ..posters.sources import tpdb
from ..posters.sources.base import ImageRef, SourceError
from ..util import fold
from .common import item_or_404, job_row, slot_of, start_job
from .schemas import (ApplyBody, ArrLink, BindBody, ConfirmAll, DetectStats, Follows, HistoryPage, ImageUrlBody,
                      ImageView, Images, ItemDetail, JobStarted, LibraryPage, MissingSeasons, Ok, OnlineResponse,
                      ReviewsPage, SetsResponse, SetView, SlotBody, TokenBody, TpdbSaved, UrlBody, WaitingPage)

router = APIRouter(prefix="/api", tags=["posters"])

PAGE_SIZE = 120
HISTORY_PAGE = 60


# -- library ------------------------------------------------------------------------------
@router.get("/library", response_model=LibraryPage)
async def library_page(type: str = "", status: str = "all", q: str = "", library_key: str = "",  # noqa: A002
                       sort: str = "title", page: int = 1):
    sql = views.LIBRARY_SQL
    params: list = []
    if type in ("movie", "show"):
        sql += " AND i.type=?"
        params.append(type)
    if library_key:
        sql += " AND i.library_key=?"
        params.append(library_key)
    sql += views.STATUS_FILTERS.get(status, views.STATUS_FILTERS["all"])[1]
    if sort == "added":
        sql += " ORDER BY i.added_at DESC"
    rows = db.get().query(sql, params)
    if q.strip():
        needle = fold(q.strip())
        rows = [r for r in rows if needle in fold(r["title"]) or needle in fold(r.get("search_title"))
                or needle in fold(r.get("original_title"))]
    if sort != "added":
        rows.sort(key=lambda r: fold(r.get("sort_title") or r["title"]))
    total = len(rows)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, page), pages)
    cards = [views.card_view(row) for row in rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]]
    any_items = db.get().scalar("SELECT 1 FROM items WHERE gone=0 LIMIT 1", (), 0)
    return {
        "cards": cards,
        "total": total,
        "page": page,
        "pages": pages,
        "page_size": PAGE_SIZE,
        "filters": [{"key": key, "label": label} for key, (label, _) in views.STATUS_FILTERS.items()],
        "libraries": [{"key": lib["key"], "title": lib["title"], "type": lib["type"]}
                      for lib in library.libraries(enabled_only=True)],
        "empty_library": not any_items,
    }


# -- one title ------------------------------------------------------------------------------
@router.get("/items/{key}/arr", response_model=ArrLink)
async def item_arr(key: str):
    """Radarr (movies) or Sonarr (shows): open the title there, or add it."""
    item = item_or_404(key)
    found = await arr.lookup(item["type"], item.get("tmdb_id"), item.get("tvdb_id"), item.get("imdb_id"),
                             item.get("search_title") or item.get("title") or "")
    return found or {}


@router.get("/items/{key}/missing", response_model=MissingSeasons)
async def item_missing(key: str):
    """Seasons TMDB or Sonarr know that Plex does not have yet."""
    return await missing.seasons(item_or_404(key))


@router.get("/items/{key}", response_model=ItemDetail)
async def item_detail(key: str):
    database = db.get()
    item = item_or_404(key)
    slots = views.item_slots(item)
    lib = database.one("SELECT key, title, type FROM libraries WHERE key=?", (item["library_key"],))
    history = database.query("SELECT * FROM history WHERE item_key=? ORDER BY id DESC LIMIT 25", (key,))
    open_reviews = database.query(
        "SELECT id, kind, reason, season FROM reviews WHERE item_key=? AND status='open' ORDER BY created_at DESC",
        (key,))
    mapper = library.mapper()
    local = mapper.to_local(item["folder"]) if item.get("folder") else None
    alive = [s for s in slots if s["kind"] == "season" and not s["gone"]]
    return {
        "item": {**item, "local_folder": str(local) if local else None, "gone": bool(item.get("gone"))},
        "library": lib,
        "slots": slots,
        "binding": views.binding_view(key),
        "history": [views.history_view(row, item) for row in history],
        "reviews": [{**row, "kind_label": reviews.KINDS.get(row["kind"], row["kind"]), "reason": row["reason"] or ""}
                    for row in open_reviews],
        "links": views.external_links(item),
        "complete": item["type"] == "show" and status.is_done(key),
        "seasons": sum(1 for s in alive if s["season"] != 0),
        "specials": any(s["season"] == 0 for s in alive),
        "has_files": any(s["file"] for s in slots),
    }


@router.get("/items/{key}/sets", response_model=SetsResponse)
async def item_sets(key: str, fresh: bool = False):
    item = item_or_404(key)
    ranked, found = await catalog.gather_ranked(item, fresh=fresh, complete_top=0)
    item = item_or_404(key)
    bound = binding.get(key)
    return {
        "type": item["type"],
        "sets": [views.set_view(entry, bound) for entry in ranked],
        "errors": {views.source_name(name): message for name, message in found.errors.items()},
        "seasons": catalog.present_seasons(key) if item["type"] == "show" else [],
        "tpdb_url": found.tpdb_url,
        "tpdb_sure": bool(found.tpdb_sure),
    }


@router.post("/items/{key}/sets/full", response_model=SetView)
async def item_set_full(key: str, body: TokenBody):
    """Load a ThePosterDB set page completely (season posters)."""
    item = item_or_404(key)
    try:
        poster_set = refsign.poster_set(body.token)
        full = await catalog.complete_tpdb(item, poster_set)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc
    seasons = catalog.present_seasons(key) if item["type"] == "show" else []
    entry = catalog.rank_sets(item, [full], seasons)[0]
    return views.set_view(entry, binding.get(key))


@router.get("/items/{key}/plex", response_model=Images)
async def item_plex(key: str, slot: str):
    item = item_or_404(key)
    kind, season = slot_of(item, slot)
    rating_key = key
    if kind == "season":
        row = db.get().one("SELECT key FROM seasons WHERE show_key=? AND number=? AND gone=0", (key, season))
        if not row:
            raise HTTPException(404, "Plex no longer has this season")
        rating_key = row["key"]
    try:
        refs = await sources.plex_images(rating_key, kind, season)
    except PlexError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"images": [refsign.image_dict(ref) for ref in refs]}


@router.get("/items/{key}/online", response_model=OnlineResponse)
async def item_online(key: str, slot: str):
    item = await catalog.ensure_ids(item_or_404(key))
    kind, season = slot_of(item, slot)
    found = await sources.online_images(item, kind, season)
    result = []
    for name in sources.ONLINE_SOURCES:
        value = found.get(name, [])
        result.append({
            "key": name,
            "label": views.source_name(name),
            "enabled": sources.source_enabled(name),
            "error": value if isinstance(value, str) else None,
            "images": [] if isinstance(value, str) else [refsign.image_dict(ref) for ref in value],
        })
    return {"sources": result}


@router.post("/items/{key}/apply", response_model=JobStarted)
async def item_apply(key: str, body: ApplyBody):
    item = item_or_404(key)
    kind, season = slot_of(item, body.slot)
    try:
        ref = refsign.ref(body.token)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc
    bound = binding.get(key)
    # Another image of the linked set (e.g. season 1 for the show) counts as picked by hand.
    own_place = ref.kind == kind and (kind != "season" or ref.season == season)
    from_set = bool(bound and ref.set_key == bound["set_key"] and ref.source == bound["source"])
    mode = "set" if from_set and own_place else "manual"
    return start_job("apply", f"Set poster: {item['title']}", tasks.apply_image, key, body.slot, ref.to_dict(), mode)


@router.post("/items/{key}/bind", response_model=JobStarted)
async def item_bind(key: str, body: BindBody):
    item = item_or_404(key)
    try:
        poster_set = refsign.poster_set(body.token)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return start_job("bind", f"Apply set: {item['title']}", tasks.bind_set, key, poster_set.to_dict(),
                     body.replace_all, body.review)


@router.post("/items/{key}/unbind", response_model=Ok)
async def item_unbind(key: str):
    item_or_404(key)
    binding.unbind(key)
    return {"ok": True, "message": "Unlinked. Files stay, new seasons no longer come automatically."}


@router.post("/items/{key}/check", response_model=JobStarted)
async def item_check(key: str):
    item = item_or_404(key)
    if not binding.get(key):
        raise HTTPException(400, "This title is not linked to a set")
    return start_job("setcheck_one", f"Check set: {item['title']}", tasks.check_one, key)


@router.post("/items/{key}/detect", response_model=JobStarted)
async def item_detect(key: str):
    item = item_or_404(key)
    return start_job("detect_one", f"Match existing: {item['title']}", tasks.detect_one, key)


@router.post("/items/{key}/refresh", response_model=JobStarted)
async def item_refresh(key: str):
    item = item_or_404(key)
    return start_job("refresh", f"Refresh metadata: {item['title']}", tasks.plex_refresh, key)


@router.post("/items/{key}/rescan", response_model=JobStarted)
async def item_rescan(key: str):
    item = item_or_404(key)
    return start_job("rescan_one", f"Rescan folder: {item['title']}", tasks.rescan, key)


@router.post("/items/{key}/force", response_model=JobStarted)
async def item_force(key: str, body: SlotBody):
    item = item_or_404(key)
    slot_of(item, body.slot)
    return start_job("force", f"Select file in Plex: {item['title']}", tasks.force_local, key, body.slot)


@router.post("/items/{key}/remove", response_model=JobStarted)
async def item_remove(key: str, body: SlotBody):
    item = item_or_404(key)
    slot_of(item, body.slot)
    return start_job("remove", f"Remove file: {item['title']}", tasks.remove_file, key, body.slot)


@router.post("/items/{key}/retry", response_model=JobStarted)
async def item_retry(key: str):
    item = item_or_404(key)
    return start_job("auto", f"Search sets: {item['title']}", tasks.retry_new_item, key)


@router.post("/items/{key}/tpdb", response_model=TpdbSaved)
async def item_tpdb(key: str, body: UrlBody):
    item_or_404(key)
    url = body.url.strip()
    base = settings.get("tpdb_base").rstrip("/")
    match = tpdb._POSTERS_HREF.search(url)  # noqa: SLF001 - same pattern as the parser
    if match:
        page = f"{base}/posters/{match.group(1)}"
    elif tpdb._SET_HREF.search(url):  # noqa: SLF001
        raise HTTPException(400, "That's a set link. Paste the link of the title page (…/posters/12345).")
    else:
        raise HTTPException(400, "Not a ThePosterDB title page link (…/posters/12345)")
    db.get().update("items", "key", key, {"tpdb_url": page, "tpdb_sure": 1})
    return {"message": "ThePosterDB page saved", "url": page}


@router.post("/items/{key}/slots/stopwait", response_model=Ok)
async def stop_waiting(key: str, body: SlotBody):
    item = item_or_404(key)
    slot_of(item, body.slot)
    db.get().update("slots", "id", body.slot, {"waiting": 0, "wait_given_up": 1})
    return {"ok": True, "message": "Stopped waiting for the set"}


# -- uploads and links ------------------------------------------------------------------------
@router.post("/upload", response_model=ImageView)
async def upload(file: UploadFile = File(...), kind: str = "movie", season: int | None = None):
    data = await file.read()
    if len(data) > http.MAX_IMAGE_BYTES:
        raise HTTPException(413, "File too large")
    try:
        info = await asyncio.to_thread(imaging.prepare, data)
    except imaging.ImageError as exc:
        raise HTTPException(400, str(exc)) from exc
    image_id = sources.store_upload(data)
    ref = ImageRef("upload", image_id, kind, season, label=file.filename or "Upload",
                   width=info.width, height=info.height)
    return refsign.image_dict(ref)


@router.post("/fetch-url", response_model=ImageView)
async def fetch_url(body: ImageUrlBody):
    try:
        data, _ = await http.fetch_public(body.url, allow_private=bool(settings.get("allow_private_urls")))
        info = await asyncio.to_thread(imaging.prepare, data)
    except (http.FetchError, imaging.ImageError) as exc:
        raise HTTPException(400, str(exc)) from exc
    image_id = sources.store_upload(data)
    url = body.url.strip()
    ref = ImageRef("url", image_id, body.kind, body.season, url=url, label=url[:80],
                   width=info.width, height=info.height)
    return refsign.image_dict(ref)


# -- review ------------------------------------------------------------------------------------
@router.get("/reviews", response_model=ReviewsPage)
async def review_list(kind: str = ""):
    rows = reviews.open_items(kind or None)
    if kind == "detect":
        rows.sort(key=lambda r: (r["data"].get("confidence") != "high", fold(r.get("title") or "")))
    open_counts = reviews.count_open()
    return {
        "reviews": [views.review_view(row) for row in rows],
        "kinds": [{"key": key, "label": label, "count": open_counts.get(key, 0)}
                  for key, label in reviews.KINDS.items()],
        "total": sum(open_counts.values()),
    }


@router.post("/reviews/{review_id}/dismiss", response_model=Ok)
async def review_dismiss(review_id: int):
    reviews.resolve(review_id, "dismissed")
    return {"ok": True, "message": "Dismissed"}


@router.post("/reviews/{review_id}/accept", response_model=JobStarted)
async def review_accept(review_id: int, take: bool = True):
    return start_job("accept", "Take set changes" if take else "Keep current posters",
                     tasks.accept_changes, review_id, take)


@router.post("/reviews/{review_id}/confirm", response_model=Ok)
async def review_confirm(review_id: int):
    if not detect.confirm(review_id):
        raise HTTPException(404, "Match not found")
    return {"ok": True, "message": "Linked"}


@router.post("/reviews/confirm-all", response_model=ConfirmAll)
async def review_confirm_all():
    count = detect.confirm_all("high")
    return {"ok": True, "message": f"Linked {count} title{'s' if count != 1 else ''}", "count": count}


# -- waiting -------------------------------------------------------------------------------------
@router.get("/waiting", response_model=WaitingPage)
async def waiting_list():
    rows = db.get().query(
        "SELECT s.id, s.item_key, s.season, s.waiting, s.waiting_since, s.wait_checked_at, i.title, i.year, "
        "b.set_title, b.designer, b.source, b.checked_at FROM slots s JOIN items i ON i.key=s.item_key "
        "LEFT JOIN bindings b ON b.item_key=s.item_key "
        "WHERE (s.waiting=1 OR s.wait_given_up=1) AND i.gone=0 ORDER BY s.waiting DESC, s.waiting_since")
    return {
        "rows": [{
            "slot": row["id"],
            "item_key": row["item_key"],
            "title": row["title"],
            "year": row["year"],
            "season": row["season"],
            "thumb": f"/img/slot/{row['id']}?v=w",
            "waiting": bool(row["waiting"]),
            "waiting_since": row["waiting_since"],
            "checked_at": row["wait_checked_at"] or row["checked_at"],
            "set_title": row["set_title"],
            "designer": row["designer"],
            "source_label": views.source_name(row["source"]) if row["source"] else None,
        } for row in rows],
        "max_days": int(settings.get("wait_max_days") or 0),
    }


@router.post("/setcheck", response_model=JobStarted)
async def setcheck():
    return start_job("setcheck", "Check linked sets", tasks.check_sets, lane="bg", dedupe=True)


# -- match existing --------------------------------------------------------------------------------
@router.get("/detect/stats", response_model=DetectStats)
async def detect_stats():
    job = db.get().one("SELECT * FROM jobs WHERE kind='detect' ORDER BY id DESC LIMIT 1")
    return {
        "shows": views.unlinked_with_files("show"),
        "movies": views.unlinked_with_files("movie"),
        "complete": views.complete_count(),
        "job": job_row(job),
    }


@router.post("/detect", response_model=JobStarted)
async def detect_library(scope: str = "shows"):
    if scope not in ("shows", "movies", "all"):
        raise HTTPException(400, "Unknown scope")
    title = {"shows": "Match existing: shows", "movies": "Match existing: movies", "all": "Match existing"}[scope]
    return start_job("detect", title, tasks.detect_library, scope, lane="bg", dedupe=True)


@router.post("/adopt", response_model=JobStarted)
async def adopt():
    return start_job("adopt", "Save Plex uploads as files", tasks.adopt_uploads, lane="bg", dedupe=True)


@router.post("/rescan", response_model=JobStarted)
async def rescan():
    return start_job("rescan", "Rescan folders", tasks.rescan, None, lane="bg", dedupe=True)


@router.post("/follows/refresh", response_model=Follows)
async def follows_refresh():
    data = await catalog.refresh_follows(force=True)
    return {"follow": data.get("follow", []), "hide": data.get("hide", [])}


# -- history ------------------------------------------------------------------------------------------
@router.get("/history", response_model=HistoryPage)
async def history_page(page: int = 1):
    database = db.get()
    total = database.scalar("SELECT COUNT(*) FROM history", (), 0)
    pages = max(1, math.ceil(total / HISTORY_PAGE))
    page = min(max(1, page), pages)
    rows = database.query(
        "SELECT h.*, i.title, i.type FROM history h LEFT JOIN items i ON i.key=h.item_key "
        "ORDER BY h.id DESC LIMIT ? OFFSET ?", (HISTORY_PAGE, (page - 1) * HISTORY_PAGE))
    return {"rows": [views.history_view(row) for row in rows], "page": page, "pages": pages, "total": total}


@router.post("/history/{history_id}/restore", response_model=JobStarted)
async def history_restore(history_id: int):
    entry = db.get().one("SELECT * FROM history WHERE id=?", (history_id,))
    if not entry or not entry.get("archived"):
        raise HTTPException(404, "No archived file")
    return start_job("restore", "Restore poster", tasks.restore, history_id)
