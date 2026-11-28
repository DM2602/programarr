"""Collections in the database: one row per collection output of a flow, its titles and the queue.

The flow's node holds what goes in (library, name, mirror or append, order); the row keeps what Plex
shows (visibility, poster) and the state in Plex. Removing the node or its flow leaves the collection in
Plex and marks the row as no longer in a flow.
"""
from __future__ import annotations

import re
import time
from datetime import datetime

from .. import db, settings

NODE_TYPE = "collection"
SYNC_MODES = ("mirror", "append")
ORDERS = ("list", "release", "alpha")
PLACEHOLDERS = ("list", "month", "year")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
_TEMPLATE = re.compile(r"\{([^{}]*)\}")


class TemplateError(ValueError):
    """A collection name with an unknown placeholder; the message is for the form."""


def check_template(template: str) -> str:
    text = " ".join(str(template or "").split())[:120]
    if not text:
        raise TemplateError("Name the collection")
    unknown = [name for name in _TEMPLATE.findall(text) if name not in PLACEHOLDERS]
    if unknown:
        raise TemplateError(f"Unknown placeholder {{{unknown[0]}}}: use {{list}}, {{month}} or {{year}}")
    return text


def resolve(template: str, list_name: str | None, now: float | None = None) -> str:
    """The collection's title: {list} is the flow's first source, {month} and {year} today's date."""
    when = datetime.fromtimestamp(now if now is not None else time.time())
    values = {"list": list_name or "", "month": MONTHS[when.month - 1], "year": str(when.year)}
    title = _TEMPLATE.sub(lambda match: values.get(match.group(1), match.group(0)), template)
    return " ".join(title.split()) or " ".join(_TEMPLATE.sub("", template).split()) or "Collection"


def queue_days() -> int:
    return max(0, int(settings.get("queue_days") or 0))


# -- definitions -------------------------------------------------------------------------------------

def sync_definitions(flow_id: int, graph: dict) -> None:
    """Rows for the flow's collection outputs. New nodes get a row, settings follow the node; a removed
    node leaves its collection in Plex."""
    from ..flows.nodes import ConfigError, TYPES  # the node types import this module

    node_type = TYPES.get(NODE_TYPE)
    database = db.get()
    wanted: dict[str, dict] = {}
    for node in graph.get("nodes") or []:
        if node.get("type") != NODE_TYPE or node_type is None:
            continue
        try:
            config = node_type.clean(node.get("config") or {})
        except ConfigError:
            continue  # not set up yet: no collection until it is
        wanted[str(node["id"])] = {
            "library_key": config["library"],
            "name_template": config["name"],
            "sync_mode": config["sync"],
            "item_order": config["order"],
            "max_items": int(config.get("limit") or 0),
            "in_flow": 1,
        }
    existing = {row["node_id"]: row for row in database.query("SELECT * FROM collections WHERE flow_id=?", (flow_id,))}
    now = db.now()
    with database.transaction() as conn:
        for node_id, values in wanted.items():
            if node_id in existing:
                sets = ", ".join(f"{column}=?" for column in values)
                conn.execute(f"UPDATE collections SET {sets} WHERE id=?", [*values.values(), existing[node_id]["id"]])
            else:
                columns = ["flow_id", "node_id", "created_at", *values]
                conn.execute(f"INSERT INTO collections ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                             [flow_id, node_id, now, *values.values()])
        for node_id, row in existing.items():
            if node_id not in wanted and row["in_flow"]:
                conn.execute("UPDATE collections SET in_flow=0 WHERE id=?", (row["id"],))


def flow_deleted(flow_id: int) -> None:
    db.get().execute("UPDATE collections SET in_flow=0 WHERE flow_id=?", (flow_id,))


# -- reading -----------------------------------------------------------------------------------------

def get(collection_id: int) -> dict | None:
    return db.get().one("SELECT * FROM collections WHERE id=?", (collection_id,))


def for_node(flow_id: int, node_id: str) -> dict | None:
    return db.get().one("SELECT * FROM collections WHERE flow_id=? AND node_id=?", (flow_id, node_id))


def all_rows() -> list[dict]:
    return db.get().query(
        "SELECT c.*, f.name AS flow_name, f.dry_run AS flow_dry_run FROM collections c "
        "LEFT JOIN flows f ON f.id=c.flow_id ORDER BY c.library_key, COALESCE(c.title, c.name_template) COLLATE NOCASE")


def library(key: str) -> dict | None:
    return db.get().one("SELECT key, title, type FROM libraries WHERE key=?", (key,))


def items(collection_id: int) -> list[dict]:
    return db.get().query("SELECT * FROM collection_items WHERE collection_id=? ORDER BY position, first_at, media_id",
                          (collection_id,))


def counts(collection_id: int | None = None) -> dict:
    """Titles per state, for one collection or all."""
    where, params = ("WHERE collection_id=?", (collection_id,)) if collection_id else ("", ())
    result = {"in_plex": 0, "queued": 0, "missing": 0}
    for row in db.get().query(f"SELECT state, COUNT(*) AS n FROM collection_items {where} GROUP BY state", params):
        result[row["state"]] = row["n"]
    return result


def update(collection_id: int, values: dict) -> None:
    db.get().update("collections", "id", collection_id, values)


def managed_keys() -> set[str]:
    return {row["plex_key"] for row in db.get().query("SELECT plex_key FROM collections WHERE plex_key IS NOT NULL")}
