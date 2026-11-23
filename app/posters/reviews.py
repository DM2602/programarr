"""The Review list: decisions Programarr leaves to you."""
from __future__ import annotations

from typing import Any

from .. import db

KINDS = {
    "new_item": "New title",
    "new_season": "New season without set",
    "set_changed": "Set changed",
    "detect": "Matched",
    "plex": "Plex ignores the file",
}


def add(item_key: str, kind: str, reason: str, season: int | None = None, data: Any = None) -> int:
    database = db.get()
    existing = database.one(
        "SELECT id FROM reviews WHERE item_key=? AND kind=? AND status='open' AND COALESCE(season,-1)=COALESCE(?,-1)",
        (item_key, kind, season),
    )
    if existing:
        database.update("reviews", "id", existing["id"], {"reason": reason, "data": db.dumps(data or {})})
        return existing["id"]
    cursor = database.execute(
        "INSERT INTO reviews (item_key, season, kind, reason, data, created_at, status) VALUES (?,?,?,?,?,?, 'open')",
        (item_key, season, kind, reason, db.dumps(data or {}), db.now()),
    )
    return cursor.lastrowid


def resolve(review_id: int, status: str = "done") -> None:
    db.get().execute(
        "UPDATE reviews SET status=?, resolved_at=? WHERE id=? AND status='open'", (status, db.now(), review_id)
    )


def resolve_for(item_key: str, kinds: tuple[str, ...] | None = None, season: int | None = None,
                any_season: bool = True) -> None:
    sql = "UPDATE reviews SET status='done', resolved_at=? WHERE item_key=? AND status='open'"
    params: list[Any] = [db.now(), item_key]
    if kinds:
        sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
        params += list(kinds)
    if not any_season:
        sql += " AND COALESCE(season,-1)=COALESCE(?,-1)"
        params.append(season)
    db.get().execute(sql, params)


def get(review_id: int) -> dict | None:
    row = db.get().one("SELECT * FROM reviews WHERE id=?", (review_id,))
    if row:
        row["data"] = db.loads(row["data"], {})
    return row


def open_items(kind: str | None = None) -> list[dict]:
    sql = (
        "SELECT r.*, i.title, i.year, i.type, i.thumb FROM reviews r LEFT JOIN items i ON i.key=r.item_key "
        "WHERE r.status='open'"
    )
    params: list[Any] = []
    if kind:
        sql += " AND r.kind=?"
        params.append(kind)
    sql += " ORDER BY r.created_at DESC"
    rows = db.get().query(sql, params)
    for row in rows:
        row["data"] = db.loads(row["data"], {})
    return rows


def count_open() -> dict[str, int]:
    rows = db.get().query("SELECT kind, COUNT(*) AS n FROM reviews WHERE status='open' GROUP BY kind")
    return {row["kind"]: row["n"] for row in rows}
