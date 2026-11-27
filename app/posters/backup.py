"""A copy of every poster file, kept by title so it survives a deleted folder or a re-added title.

The copies live in /data/backup/<library>/<movie|show>-<id>/, one per slot (poster, season-02),
keyed by TMDB, TVDB or IMDb ID rather than Plex' rating key: a title that Plex removes and adds
again (new folder, new key) finds its posters again. The file name carries the start of the
content hash, so an unchanged poster is not copied twice.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
import uuid
from pathlib import Path

from .. import config, db, imaging, jobs, settings
from .sources.base import ImageRef, parse_slot_id
from ..util import event, safe_name

HASH_CHARS = 12


def root() -> Path:
    return config.DATA_DIR / "backup"


def enabled() -> bool:
    return bool(settings.get("backup_posters"))


def title_dir(item: dict) -> Path | None:
    """The folder for one title; None without an ID that stays when Plex adds the title again."""
    kind = item.get("type")
    order = ("tmdb", "imdb") if kind == "movie" else ("tvdb", "tmdb", "imdb")
    for name in order:
        value = item.get(f"{name}_id")
        if value:
            return root() / safe_name(str(item.get("library_key") or "0")) / safe_name(f"{kind}-{name}-{value}")
    return None


def slot_name(kind: str, season: int | None) -> str:
    return f"season-{int(season or 0):02d}" if kind == "season" else "poster"


def _copies(folder: Path, name: str) -> list[Path]:
    if not folder.is_dir():
        return []
    prefix = f"{name}-"
    return sorted((p for p in folder.iterdir() if p.is_file() and p.name.startswith(prefix) and not p.name.startswith(".")),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def find(item: dict, kind: str, season: int | None) -> Path | None:
    folder = title_dir(item)
    if folder is None:
        return None
    copies = _copies(folder, slot_name(kind, season))
    return copies[0] if copies else None


def _store(folder: Path, name: str, sha1: str, ext: str, write) -> bool:
    """Write one copy unless the same content is there already; older copies of the slot go."""
    target = folder / f"{name}-{sha1[:HASH_CHARS]}{ext}"
    if target.exists():
        return False
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".{target.name}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        write(tmp)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    for old in _copies(folder, name):
        if old != target:
            old.unlink(missing_ok=True)
    forget_stats()
    return True


def save_bytes(item: dict, kind: str, season: int | None, data: bytes, ext: str) -> bool:
    """Right after Programarr wrote a poster."""
    folder = title_dir(item)
    if folder is None or not enabled():
        return False
    try:
        return _store(folder, slot_name(kind, season), hashlib.sha1(data).hexdigest(), ext, lambda p: p.write_bytes(data))
    except OSError as exc:
        event("warning", "backup", f"Backup failed for {item.get('title')}: {exc.strerror or exc}", item.get("key"))
        return False


def save_file(item: dict, slot: dict) -> bool:
    """A file found in the folder (written by anyone); its hash is known from the scan."""
    folder = title_dir(item)
    path = Path(slot["file"]) if slot.get("file") else None
    if folder is None or path is None or not slot.get("file_sha1"):
        return False
    kind, _, season = parse_slot_id(slot["id"])
    return _store(folder, slot_name(kind, season), slot["file_sha1"], path.suffix.lower(),
                  lambda p: shutil.copyfile(path, p))


def drop(item: dict, kind: str, season: int | None) -> int:
    """Remove the copies of a slot: a poster removed on purpose must not come back."""
    folder = title_dir(item)
    if folder is None:
        return 0
    copies = _copies(folder, slot_name(kind, season))
    for path in copies:
        path.unlink(missing_ok=True)
    if copies:
        forget_stats()
    return len(copies)


def run() -> dict:
    """Copy every poster file that has no current copy (maintenance, every six hours)."""
    if not enabled():
        return {"saved": 0, "checked": 0}
    database = db.get()
    rows = database.query(
        "SELECT s.*, i.type, i.library_key, i.tmdb_id, i.tvdb_id, i.imdb_id, i.title FROM slots s "
        "JOIN items i ON i.key=s.item_key WHERE s.file IS NOT NULL AND s.file_sha1 IS NOT NULL AND i.gone=0"
    )
    saved = failed = 0
    for index, row in enumerate(rows):
        if index % 50 == 0:
            jobs.progress(index, len(rows), "Backing up posters…")
        item = {key: row[key] for key in ("type", "library_key", "tmdb_id", "tvdb_id", "imdb_id", "title")}
        item["key"] = row["item_key"]
        try:
            saved += save_file(item, row)
        except OSError:
            failed += 1
    if saved or failed:
        event("info", "backup", f"Poster backup: {saved} new copies" + (f", {failed} failed" if failed else ""))
    return {"saved": saved, "checked": len(rows), "failed": failed}


_stats: dict = {"at": 0.0, "value": None}


def forget_stats() -> None:
    _stats.update(at=0.0, value=None)


def stats(max_age: float = 0) -> dict:
    """Files and bytes in the backup; max_age reuses a count that young (seconds)."""
    if max_age and _stats["value"] is not None and time.time() - _stats["at"] < max_age:
        return dict(_stats["value"])
    count = size = 0
    base = root()
    if base.is_dir():
        for path in base.rglob("*"):
            if path.is_file() and not path.name.startswith("."):
                count += 1
                size += path.stat().st_size
    _stats.update(at=time.time(), value={"files": count, "bytes": size})
    return {"files": count, "bytes": size}


def ref_for(path: Path, kind: str, season: int | None) -> ImageRef:
    """An image reference to a copy, carrying the origin the file was stamped with."""
    stamp = imaging.read_stamp_file(path) or {}
    return ImageRef(
        source="backup",
        image_id=path.name,
        kind=kind,
        season=season,
        url=str(path),
        designer=stamp.get("by"),
        set_key=stamp.get("set"),
        extra={"src": stamp.get("src") or "backup", "img": stamp.get("img"), "mode": stamp.get("mode")},
    )


async def restore(item_key: str, slots: list[str] | None = None) -> list[str]:
    """Put the copies back for slots without a file; returns the restored slots."""
    from . import apply  # late import: apply writes copies through this module

    database = db.get()
    item = database.one("SELECT * FROM items WHERE key=?", (item_key,))
    if not item or item.get("gone") or not enabled():
        return []
    rows = database.query("SELECT * FROM slots WHERE item_key=? AND file IS NULL", (item_key,))
    assignments = []
    for row in rows:
        if slots is not None and row["id"] not in slots:
            continue
        kind, _, season = parse_slot_id(row["id"])
        if kind == "season" and not database.one(
                "SELECT 1 FROM seasons WHERE show_key=? AND number=? AND gone=0", (item_key, season)):
            continue
        path = find(item, kind, season)
        if path is not None:
            assignments.append(apply.Assignment(row["id"], ref_for(path, kind, season)))
    if not assignments:
        return []
    # A copy of a set image keeps following its set; anything else stays as it is.
    restored: list[str] = []
    for mode in ("set", "manual"):
        group = [a for a in assignments if (a.ref.extra.get("mode") == "set") == (mode == "set")]
        if group:
            outcomes = await apply.apply(item_key, group, "backup", mode)
            restored += [o.slot_id for o in outcomes if o.ok]
    if restored:
        event("info", "backup", f"{item['title']}: {len(restored)} poster(s) restored from backup", item_key)
    return restored
