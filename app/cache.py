"""Short-lived cache for source answers (database) and preview images (disk)."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from . import config, db


def get(key: str) -> Any | None:
    row = db.get().one("SELECT value, expires_at FROM cache WHERE key=?", (key,))
    if not row:
        return None
    if row["expires_at"] and row["expires_at"] < db.now():
        db.get().execute("DELETE FROM cache WHERE key=?", (key,))
        return None
    return db.loads(row["value"])


def put(key: str, value: Any, ttl: int) -> None:
    db.get().upsert(
        "cache", ("key",), {"key": key, "value": db.dumps(value), "expires_at": db.now() + int(ttl)}
    )


def drop(prefix: str) -> None:
    db.get().execute("DELETE FROM cache WHERE key LIKE ?", (prefix.replace("%", "") + "%",))


def prune() -> None:
    db.get().execute("DELETE FROM cache WHERE expires_at < ?", (db.now(),))
    folder = config.DATA_DIR / "cache" / "images"
    if folder.exists():
        limit = time.time() - 14 * 86400
        for path in folder.iterdir():
            try:
                if path.stat().st_mtime < limit:
                    path.unlink()
            except OSError:
                pass


def _image_path(key: str) -> Path:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return config.DATA_DIR / "cache" / "images" / digest


def image_get(key: str) -> bytes | None:
    path = _image_path(key)
    try:
        return path.read_bytes()
    except OSError:
        return None


def image_put(key: str, data: bytes) -> None:
    path = _image_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
