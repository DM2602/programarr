"""MDBList lists, by address, "user/list" or number."""
from __future__ import annotations

import re

from ..media import mdblist
from .base import Adapter, Fetched, Field, RawEntry, SourceError, int_or_none

_URL = re.compile(r"mdblist\.com/lists/([^/?#]+)/([^/?#]+)", re.I)


def list_ref(value: str) -> str:
    """'user/slug' or the list number."""
    text = (value or "").strip()
    found = _URL.search(text)
    if found:
        return f"{found.group(1)}/{found.group(2)}"
    text = text.strip("/")
    if text.isdigit():
        return text
    parts = [part for part in text.split("/") if part]
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}"
    raise SourceError("List: paste the address, e.g. https://mdblist.com/lists/name/list-name")


class Mdblist(Adapter):
    key = "mdblist"
    label = "MDBList"
    fields = [
        Field("list", "List address", "text", default="", placeholder="https://mdblist.com/lists/name/list-name"),
        Field("limit", "Titles", "number", default=100, min=1, max=1000),
    ]

    def missing(self) -> str | None:
        return None if mdblist.configured() else "Set an MDBList API key under Settings › Lists"

    def clean(self, params: dict) -> dict:
        clean = super().clean(params)
        clean["list"] = list_ref(clean["list"])
        return clean

    def describe(self, params: dict) -> str:
        return f"MDBList {params['list']}"

    async def fetch(self, params: dict) -> Fetched:
        path = f"/lists/{params['list']}/items"
        try:
            data = await mdblist.request("GET", path, {"limit": params["limit"], "offset": 0})
        except mdblist.MdblistError as exc:
            raise SourceError(str(exc)) from exc
        if data is None:
            raise SourceError("MDBList: list not found")
        rows: list[dict] = []
        if isinstance(data, dict):
            for name, kind in (("movies", "movie"), ("shows", "show")):
                rows += [{**row, "_kind": kind} for row in data.get(name) or [] if isinstance(row, dict)]
        elif isinstance(data, list):
            rows = [{**row, "_kind": "show" if row.get("mediatype") == "show" else "movie"}
                    for row in data if isinstance(row, dict)]
        rows.sort(key=lambda row: (int_or_none(row.get("rank")) or 10**6))
        entries = []
        for row in rows[: params["limit"]]:
            kind = "show" if (row.get("mediatype") or row["_kind"]) == "show" else "movie"
            ids = {"tmdb": int_or_none(row.get("tmdb_id") or row.get("id")), "imdb": row.get("imdb_id"),
                   "tvdb": int_or_none(row.get("tvdb_id"))}
            entries.append(RawEntry(
                position=len(entries) + 1,
                title=row.get("title") or "",
                year=int_or_none(row.get("release_year") or row.get("year")),
                kind=kind,
                ids={name: value for name, value in ids.items() if value},
                extra={"rank": row.get("rank")},
            ))
        return Fetched(entries)
