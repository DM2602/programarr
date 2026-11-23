"""SQLite storage. One connection, guarded by a lock; the app runs in one process."""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS libraries (
  key TEXT PRIMARY KEY, title TEXT, type TEXT, enabled INTEGER DEFAULT 0,
  locations TEXT DEFAULT '[]', baseline_at INTEGER, synced_at INTEGER
);

CREATE TABLE IF NOT EXISTS items (
  key TEXT PRIMARY KEY, library_key TEXT, type TEXT,
  title TEXT, original_title TEXT, year INTEGER, sort_title TEXT,
  tmdb_id TEXT, tvdb_id TEXT, imdb_id TEXT,
  folder TEXT, files TEXT DEFAULT '[]',
  added_at INTEGER, updated_at INTEGER, thumb TEXT,
  locked TEXT DEFAULT '[]',
  is_new INTEGER DEFAULT 0, first_seen INTEGER, synced_at INTEGER, gone INTEGER DEFAULT 0,
  search_title TEXT, search_year INTEGER, tpdb_url TEXT, tpdb_sure INTEGER DEFAULT 0, details_at INTEGER,
  collection_id TEXT, collection_name TEXT,
  show_status TEXT, status_at INTEGER
);
CREATE INDEX IF NOT EXISTS items_lib ON items(library_key);

CREATE TABLE IF NOT EXISTS seasons (
  key TEXT PRIMARY KEY, show_key TEXT, number INTEGER, title TEXT,
  added_at INTEGER, folder TEXT, locked TEXT DEFAULT '[]', thumb TEXT,
  first_seen INTEGER, synced_at INTEGER, gone INTEGER DEFAULT 0, is_new INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS seasons_show ON seasons(show_key);

CREATE TABLE IF NOT EXISTS slots (
  id TEXT PRIMARY KEY, item_key TEXT NOT NULL, kind TEXT NOT NULL, season INTEGER,
  file TEXT, file_sha1 TEXT, file_size INTEGER, file_mtime REAL,
  source TEXT, set_key TEXT, image_id TEXT, image_modified TEXT, designer TEXT, source_url TEXT,
  mode TEXT, applied_at INTEGER, written_sha1 TEXT,
  plex_state TEXT, plex_checked_at INTEGER,
  waiting INTEGER DEFAULT 0, waiting_since INTEGER, wait_checked_at INTEGER, wait_given_up INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS slots_item ON slots(item_key);

CREATE TABLE IF NOT EXISTS bindings (
  item_key TEXT PRIMARY KEY, source TEXT, set_key TEXT, set_title TEXT, designer TEXT, set_url TEXT,
  bound_at INTEGER, bound_by TEXT, checked_at INTEGER, set_updated TEXT,
  snapshot TEXT DEFAULT '{}', state TEXT DEFAULT 'ok', note TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, item_key TEXT, season INTEGER, kind TEXT, reason TEXT,
  data TEXT DEFAULT '{}', created_at INTEGER, status TEXT DEFAULT 'open', resolved_at INTEGER
);
CREATE INDEX IF NOT EXISTS reviews_status ON reviews(status, kind);

CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER, item_key TEXT, slot_id TEXT, action TEXT,
  path TEXT, archived TEXT, source TEXT, set_key TEXT, image_id TEXT, designer TEXT,
  origin TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS history_item ON history(item_key);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, title TEXT, status TEXT,
  done INTEGER DEFAULT 0, total INTEGER DEFAULT 0, message TEXT, result TEXT,
  created_at INTEGER, started_at INTEGER, finished_at INTEGER
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER, level TEXT, area TEXT,
  message TEXT, item_key TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS events_at ON events(at);

CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, expires_at INTEGER);

CREATE TABLE IF NOT EXISTS hashes (key TEXT PRIMARY KEY, p TEXT, d TEXT, g TEXT, at INTEGER);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    # -- schema ---------------------------------------------------------
    def _migrate(self) -> None:
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if current == SCHEMA_VERSION:
            return
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"The database comes from a newer Programarr version (schema {current}). "
                "Please use that version."
            )
        if current > 0 and self.path.exists():
            backup = self.path.with_name(f"programarr-pre-v{current}.db")
            if not backup.exists():
                self.conn.execute("PRAGMA wal_checkpoint(FULL)")
                shutil.copy2(self.path, backup)
        with self.lock:
            script = ""
            if current < 1:
                script += SCHEMA_V1
            # executescript commits on its own, so the whole migration is one script.
            self.conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;")

    # -- helpers --------------------------------------------------------
    @contextmanager
    def transaction(self):
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def execute(self, sql: str, params: Iterable[Any] | dict = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self.lock:
            self.conn.executemany(sql, rows)

    def query(self, sql: str, params: Iterable[Any] | dict = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Iterable[Any] | dict = ()) -> dict | None:
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def scalar(self, sql: str, params: Iterable[Any] | dict = (), default: Any = None) -> Any:
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
            return row[0] if row and row[0] is not None else default

    def upsert(self, table: str, key_cols: tuple[str, ...], values: dict) -> None:
        cols = list(values.keys())
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in key_cols)
        sql = (
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({','.join(key_cols)}) DO "
            + (f"UPDATE SET {updates}" if updates else "NOTHING")
        )
        self.execute(sql, [values[c] for c in cols])

    def update(self, table: str, key_col: str, key: Any, values: dict) -> None:
        if not values:
            return
        sets = ",".join(f"{c}=?" for c in values)
        self.execute(f"UPDATE {table} SET {sets} WHERE {key_col}=?", [*values.values(), key])

    def close(self) -> None:
        with self.lock:
            self.conn.close()


_db: Database | None = None


def init(path: Path) -> Database:
    global _db
    if _db is not None:
        _db.close()
    _db = Database(path)
    return _db


def get() -> Database:
    if _db is None:
        raise RuntimeError("Database not initialised")
    return _db


def now() -> int:
    return int(time.time())


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
