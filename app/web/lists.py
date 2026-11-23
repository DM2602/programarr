"""List sources: previews, the match log with corrections, exclusions and title search."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from ..lists import runs
from ..lists.base import SourceError
from ..media import mdblist, store, tmdb
from .common import start_job
from .schemas import (Changed, ExclusionBody, ExclusionsPage, FixBody, JobStarted, ListSources, MatchesPage,
                      MdblistLists, MediaSearch, MediaView, Ok, OverridesPage, PreviewBody, ProblemsPage, RunDetail,
                      RunsPage)

router = APIRouter(prefix="/api", tags=["lists"])


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc))


@router.get("/lists/sources", response_model=ListSources)
async def sources():
    return {"sources": [adapter.info() for adapter in runs.ADAPTERS.values()]}


@router.post("/lists/preview", response_model=JobStarted)
async def preview(body: PreviewBody):
    try:
        params = runs.prepare(body.source, body.params)
    except SourceError as exc:
        raise _bad(exc) from exc
    label = runs.adapter(body.source).describe(params)
    return start_job("preview", f"Preview: {label}", runs.preview, body.source, params, lane="flows")


@router.get("/lists/runs", response_model=RunsPage)
async def list_runs(source_ref: str = "", limit: int = 100):
    return {"runs": runs.runs(source_ref or None, limit)}


@router.get("/lists/runs/{run_id}", response_model=RunDetail)
async def run_detail(run_id: int):
    detail = runs.run_detail(run_id)
    if detail is None:
        raise HTTPException(404, "Run not found")
    return detail


@router.post("/lists/runs/{run_id}/again", response_model=JobStarted)
async def run_again(run_id: int):
    row = db.get().one("SELECT source, params FROM parser_runs WHERE id=?", (run_id,))
    if not row:
        raise HTTPException(404, "Run not found")
    try:
        params = runs.prepare(row["source"], db.loads(row["params"], {}) or {})
    except SourceError as exc:
        raise _bad(exc) from exc
    label = runs.adapter(row["source"]).describe(params)
    return start_job("preview", f"Preview: {label}", runs.preview, row["source"], params, lane="flows")


@router.get("/lists/problems", response_model=ProblemsPage)
async def problems():
    return {"rows": runs.problems()}


@router.get("/lists/matches", response_model=MatchesPage)
async def matches(status: str = "", q: str = "", page: int = 1):
    return runs.matches(status, q, page)


@router.post("/lists/entries/{entry_id}/fix", response_model=Changed)
async def fix(entry_id: int, body: FixBody):
    try:
        result = await runs.fix(entry_id, body.kind, body.tmdb_id)
    except (SourceError, tmdb.TmdbError) as exc:
        raise _bad(exc) from exc
    return {"ok": True, "changed": result["changed"], "message": "Match saved"}


@router.post("/lists/entries/{entry_id}/ignore", response_model=Changed)
async def ignore(entry_id: int):
    try:
        changed = runs.ignore(entry_id)
    except SourceError as exc:
        raise _bad(exc) from exc
    return {"ok": True, "changed": changed, "message": "Ignored"}


@router.post("/lists/entries/{entry_id}/reset", response_model=Changed)
async def reset(entry_id: int):
    try:
        changed = await runs.reset(entry_id)
    except (SourceError, tmdb.TmdbError) as exc:
        raise _bad(exc) from exc
    return {"ok": True, "changed": changed, "message": "Back to automatic"}


@router.get("/lists/overrides", response_model=OverridesPage)
async def overrides():
    return {"rows": runs.overrides()}


@router.delete("/lists/overrides/{override_id}", response_model=Changed)
async def remove_override(override_id: int):
    try:
        changed = await runs.remove_override(override_id)
    except (SourceError, tmdb.TmdbError) as exc:
        raise _bad(exc) from exc
    return {"ok": True, "changed": changed, "message": "Correction removed"}


@router.get("/lists/mdblist/mine", response_model=MdblistLists)
async def mdblist_mine():
    try:
        return {"lists": await mdblist.my_lists()}
    except mdblist.MdblistError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/exclusions", response_model=ExclusionsPage)
async def exclusions():
    return {"rows": runs.exclusions()}


@router.post("/exclusions", response_model=Ok)
async def exclude(body: ExclusionBody):
    try:
        await runs.exclude(body.kind, body.tmdb_id, body.media_id, body.reason)
    except (SourceError, tmdb.TmdbError) as exc:
        raise _bad(exc) from exc
    return {"ok": True, "message": "Excluded"}


@router.delete("/exclusions/media/{media_id}", response_model=Ok)
async def unexclude_media(media_id: int):
    try:
        runs.unexclude_media(media_id)
    except SourceError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "message": "Exclusion removed"}


@router.delete("/exclusions/{exclusion_id}", response_model=Ok)
async def unexclude(exclusion_id: int):
    try:
        runs.unexclude(exclusion_id)
    except SourceError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "message": "Exclusion removed"}


@router.get("/media/search", response_model=MediaSearch)
async def media_search(q: str, kind: str = "", year: int | None = None):
    text = q.strip()
    if not text:
        return {"results": []}
    kinds = [kind] if kind in ("movie", "show") else ["movie", "show"]
    results: list[dict] = []
    try:
        if text.isdigit() and kind in ("movie", "show"):
            data = await tmdb.details(kind, int(text))
            if data:
                results.append(tmdb.brief(kind, data))
        for each in kinds:
            results += await tmdb.search(each, text, year)
    except tmdb.TmdbError as exc:
        raise HTTPException(502, str(exc)) from exc
    results.sort(key=lambda row: -row["popularity"])
    seen, unique = set(), []
    for row in results:
        if (row["kind"], row["tmdb_id"]) not in seen:
            seen.add((row["kind"], row["tmdb_id"]))
            unique.append(runs.candidate_view(row))
    return {"results": unique[:20]}


@router.get("/media/{media_id}", response_model=MediaView)
async def media(media_id: int):
    row = store.get(media_id)
    if not row:
        raise HTTPException(404, "Title not found")
    return store.view(row)
