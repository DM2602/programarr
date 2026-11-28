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

SCHEMA_VERSION = 4

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

# Phase 2: list sources, the shared media cache and the match log.
SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS media_cache (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('movie','show')),
  tmdb_id INTEGER, imdb_id TEXT, tvdb_id INTEGER,
  title TEXT NOT NULL, original_title TEXT, year INTEGER, release_date TEXT,
  genres TEXT,
  language TEXT, countries TEXT, seasons INTEGER, status TEXT,
  poster_path TEXT, popularity REAL,
  imdb_rating REAL, imdb_votes INTEGER, rt_critics INTEGER, rt_audience INTEGER,
  meta_at INTEGER, ratings_at INTEGER,
  UNIQUE (kind, tmdb_id)
);
CREATE INDEX IF NOT EXISTS media_cache_imdb ON media_cache (imdb_id);
CREATE INDEX IF NOT EXISTS media_cache_tvdb ON media_cache (tvdb_id);

CREATE TABLE IF NOT EXISTS parser_runs (
  id INTEGER PRIMARY KEY,
  flow_run_id INTEGER,
  source TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  label TEXT,
  started_at INTEGER, finished_at INTEGER,
  status TEXT CHECK (status IN ('running','ok','partial','error')), error TEXT,
  params TEXT, notes TEXT,
  raw_count INTEGER, matched INTEGER, uncertain INTEGER, failed INTEGER, excluded INTEGER
);
CREATE INDEX IF NOT EXISTS parser_runs_ref ON parser_runs (source_ref, id);

CREATE TABLE IF NOT EXISTS parser_entries (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES parser_runs(id) ON DELETE CASCADE,
  position INTEGER, raw_title TEXT, raw_year INTEGER, raw_kind TEXT, raw_ids TEXT,
  raw_url TEXT, extra TEXT,
  fingerprint TEXT NOT NULL,
  method TEXT,
  media_id INTEGER REFERENCES media_cache(id),
  status TEXT NOT NULL CHECK (status IN ('matched','uncertain','failed','excluded')),
  score REAL, candidates TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS parser_entries_status ON parser_entries (status, run_id);
CREATE INDEX IF NOT EXISTS parser_entries_run ON parser_entries (run_id, position);
CREATE INDEX IF NOT EXISTS parser_entries_fp ON parser_entries (fingerprint);

CREATE TABLE IF NOT EXISTS parser_overrides (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL DEFAULT '',
  fingerprint TEXT NOT NULL,
  media_id INTEGER REFERENCES media_cache(id),
  ignore INTEGER NOT NULL DEFAULT 0,
  note TEXT, created_at INTEGER,
  UNIQUE (source, fingerprint)
);

CREATE TABLE IF NOT EXISTS exclusions (
  id INTEGER PRIMARY KEY,
  media_id INTEGER NOT NULL REFERENCES media_cache(id),
  scope TEXT NOT NULL DEFAULT 'global',
  reason TEXT, created_at INTEGER,
  UNIQUE (media_id, scope)
);
"""


# Phase 3: flows (a JSON graph of nodes) and their history, title by title.
SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS flows (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  graph TEXT NOT NULL,  -- JSON: nodes [{id, type, config, position}], edges [{source, port, target}]
  version INTEGER NOT NULL DEFAULT 1,
  schedule TEXT,  -- cron (5 fields) or 'every:6h'; NULL = manual only
  enabled INTEGER NOT NULL DEFAULT 1,
  dry_run INTEGER NOT NULL DEFAULT 1,
  next_run_at INTEGER, created_at INTEGER, updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS flow_runs (
  id INTEGER PRIMARY KEY,
  flow_id INTEGER NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
  flow_version INTEGER NOT NULL,
  trigger TEXT,  -- manual | schedule
  dry_run INTEGER NOT NULL,
  started_at INTEGER, finished_at INTEGER,
  status TEXT CHECK (status IN ('running','ok','partial','error')),
  error TEXT, notes TEXT,
  stats TEXT  -- JSON: titles per node and port, outcomes, comparisons
);
CREATE INDEX IF NOT EXISTS flow_runs_flow ON flow_runs (flow_id, id);

CREATE TABLE IF NOT EXISTS flow_trace (
  run_id INTEGER NOT NULL REFERENCES flow_runs(id) ON DELETE CASCADE,
  media_id INTEGER NOT NULL REFERENCES media_cache(id),
  node_id TEXT NOT NULL,
  port TEXT,  -- sources: out; filters: rule ids, match, no_match or a switch port; outputs: added, present, skipped, failed (tagged in older runs)
  detail TEXT,  -- e.g. 'IMDb 7.1 < 7.5' or Radarr's response
  PRIMARY KEY (run_id, media_id, node_id)
);
CREATE INDEX IF NOT EXISTS flow_trace_node ON flow_trace (run_id, node_id, port);
CREATE INDEX IF NOT EXISTS parser_runs_flow ON parser_runs (flow_run_id);
"""


# Phase 3: the import list exclusions of Radarr and Sonarr, kept in step with Programarr's both ways.
SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS arr_exclusions (
  service TEXT NOT NULL CHECK (service IN ('radarr','sonarr')),
  external_id INTEGER NOT NULL,  -- TMDB id (Radarr) or TVDB id (Sonarr)
  arr_id INTEGER,  -- the entry's id there, NULL until written there
  title TEXT, year INTEGER,
  state TEXT NOT NULL DEFAULT 'synced' CHECK (state IN ('synced','add','remove')),  -- add/remove: to write there
  added_at INTEGER,  -- first seen on that list, or excluded in Programarr
  PRIMARY KEY (service, external_id)
);
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
            if current < 2:
                script += SCHEMA_V2
            if current < 3:
                script += SCHEMA_V3
            if current < 4:
                script += SCHEMA_V4
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

    def update_where(self, table: str, where: dict, values: dict) -> None:
        """UPDATE for a row with a key of several columns."""
        if not values:
            return
        sets = ",".join(f"{c}=?" for c in values)
        conditions = " AND ".join(f"{c}=?" for c in where)
        self.execute(f"UPDATE {table} SET {sets} WHERE {conditions}", [*values.values(), *where.values()])

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
