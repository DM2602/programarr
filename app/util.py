"""Small helpers shared across modules."""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import datetime
from typing import Any

from . import db

log = logging.getLogger("programarr")

_YEAR_RE = re.compile(r"\s*\((\d{4}|N/A)\)\s*$")
_SEASON_RE = re.compile(r"\bseason\s*(\d{1,3})\b", re.IGNORECASE)
_SPECIALS_RE = re.compile(r"\bspecials?\b", re.IGNORECASE)


def fold(text: str | None) -> str:
    """Lower-case, strip accents and unify whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("ß", "ss").lower()
    return re.sub(r"\s+", " ", text).strip()


def norm_title(text: str | None) -> str:
    """Comparable form of a title: no accents, punctuation or articles at the end."""
    text = fold(text)
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_title_year(text: str | None) -> tuple[str, int | None]:
    """'Severance (2022)' -> ('Severance', 2022)."""
    if not text:
        return "", None
    text = str(text).strip()
    match = _YEAR_RE.search(text)
    if not match:
        return text, None
    year = match.group(1)
    return text[: match.start()].strip(), (int(year) if year.isdigit() else None)


def season_from_label(text: str | None) -> int | None:
    """Season number from a poster label such as 'Show (2022) - Season 2'; 0 for specials."""
    if not text:
        return None
    tail = text.rsplit(" - ", 1)[-1] if " - " in text else text
    if _SPECIALS_RE.search(tail):
        return 0
    match = _SEASON_RE.search(tail)
    if match:
        return int(match.group(1))
    return None


def safe_name(text: str, limit: int = 80) -> str:
    text = fold(text)
    text = re.sub(r"[^a-z0-9._-]+", "-", text).strip("-")
    return text[:limit] or "x"


def human_time(ts: int | float | None) -> str:
    if not ts:
        return "–"
    ts = float(ts)
    diff = time.time() - ts
    if 0 <= diff < 60:
        return "just now"
    if 0 <= diff < 3600:
        return f"{int(diff // 60)} min ago"
    dt = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    if dt.date() == today:
        return f"today {dt:%H:%M}"
    if (today - dt.date()).days == 1:
        return f"yesterday {dt:%H:%M}"
    return f"{dt:%Y-%m-%d %H:%M}"


def date_only(ts: int | float | None) -> str:
    if not ts:
        return "–"
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")


def event(level: str, area: str, message: str, item_key: str | None = None, data: Any = None) -> None:
    """Record something for the Logs page and the log file."""
    getattr(log, level if level in ("debug", "info", "warning", "error") else "info")(
        "[%s] %s", area, message
    )
    try:
        database = db.get()
    except RuntimeError:
        return
    database.execute(
        "INSERT INTO events (at, level, area, message, item_key, data) VALUES (?,?,?,?,?,?)",
        (db.now(), level, area, message, item_key, db.dumps(data) if data is not None else None),
    )


def prune_events(keep_days: int = 60) -> None:
    db.get().execute("DELETE FROM events WHERE at < ?", (db.now() - keep_days * 86400,))


def season_label(number: int | None) -> str:
    if number is None:
        return "Show"
    if number == 0:
        return "Specials"
    return f"Season {number}"
