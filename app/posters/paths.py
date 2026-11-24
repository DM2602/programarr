"""Where posters live: path mapping, Plex file names, safe writing and the archive."""
from __future__ import annotations

import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import config
from ..util import safe_name

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tbn")
MOVIE_STEMS = ("poster", "cover", "default", "folder", "movie")
SHOW_STEMS = ("poster", "show", "folder")
_VARIANT = re.compile(r"^(?P<stem>.+?)(-\d+)?$")
_STACK = re.compile(r"[ ._-]*(cd|dvd|part|pt|disc|disk)[ ._-]*\d+$", re.IGNORECASE)


class PathError(Exception):
    """A folder is missing or not writable; the message is for the UI."""


# -- mapping ---------------------------------------------------------------
def _norm(path: str) -> str:
    path = (path or "").strip().replace("\\", "/")
    if len(path) > 1:
        path = path.rstrip("/")
    return path


class PathMapper:
    """Translate the paths Plex reports into paths inside this container."""

    def __init__(self, maps: list[dict] | None):
        pairs = []
        for entry in maps or []:
            plex, local = _norm(entry.get("plex", "")), _norm(entry.get("local", ""))
            if plex and local:
                pairs.append((plex, local))
        self.pairs = sorted(pairs, key=lambda pair: -len(pair[0]))

    def to_local(self, plex_path: str | None) -> Path | None:
        if not plex_path:
            return None
        path = _norm(plex_path)
        for plex, local in self.pairs:
            if path == plex or path.startswith(plex + "/"):
                return Path(local + path[len(plex):])
        return Path(path)

    def to_plex(self, local_path: Path | str) -> str:
        path = _norm(str(local_path))
        for plex, local in sorted(self.pairs, key=lambda pair: -len(pair[1])):
            if path == local or path.startswith(local + "/"):
                return plex + path[len(local):]
        return path


# -- slot places -----------------------------------------------------------
@dataclass
class Place:
    """Everything needed to find or name the poster file of one slot."""

    kind: str  # movie | show | season
    folder: Path  # movie folder, or the show folder for shows and seasons
    season: int | None = None
    season_folder: Path | None = None
    video_stem: str | None = None
    shared: bool = False
    extra_folders: list[Path] = field(default_factory=list)


def video_stem(file_name: str) -> str:
    stem = PurePosixPath(file_name.replace("\\", "/")).stem
    return _STACK.sub("", stem)


def _season_root_stems(n: int) -> list[str]:
    if n == 0:
        return ["season-specials-poster", "season00-poster", "season0-poster", "season00", "season0"]
    return [f"season{n:02d}-poster", f"season{n}-poster", f"season{n:02d}", f"season{n}"]


def _season_folder_stems(n: int) -> list[str]:
    if n == 0:
        return ["season-specials-poster", "season00", "season0", "season00-poster"]
    return [f"season{n:02d}", f"season{n}", f"season{n:02d}-poster", f"season{n}-poster"]


def _list_images(folder: Path | None) -> list[Path]:
    if folder is None:
        return []
    try:
        with os.scandir(folder) as entries:
            return sorted(
                Path(entry.path)
                for entry in entries
                if entry.is_file() and Path(entry.name).suffix.lower() in IMAGE_EXTS and not entry.name.startswith(".")
            )
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return []


def _matching(files: list[Path], stems: list[str] | tuple[str, ...], variants: bool = True) -> list[Path]:
    """Files whose stem is one of stems (or stem-N), ordered by the stems' priority."""
    order = {stem: index for index, stem in enumerate(stems)}
    found = []
    for path in files:
        stem = path.stem.lower()
        base = stem
        if variants:
            match = _VARIANT.match(stem)
            if match and match.group("stem") in order and match.group(0) != match.group("stem"):
                base = match.group("stem")
        if base in order:
            variant_rank = 0 if base == stem else 1
            ext_rank = IMAGE_EXTS.index(path.suffix.lower())
            found.append(((order[base], variant_rank, ext_rank, path.name), path))
    return [path for _, path in sorted(found)]


def existing(place: Place) -> list[Path]:
    """All files Plex would read for this slot, the preferred one first."""
    if place.kind == "movie":
        files = _list_images(place.folder)
        own = []
        if place.video_stem:
            own = _matching(files, [place.video_stem.lower()])
        if place.shared:
            return own
        return _matching(files, MOVIE_STEMS) + [p for p in own if p not in _matching(files, MOVIE_STEMS)]
    if place.kind == "show":
        return _matching(_list_images(place.folder), SHOW_STEMS)
    if place.kind == "season" and place.season is not None:
        found = _matching(_list_images(place.folder), _season_root_stems(place.season), variants=False)
        if place.season_folder and place.season_folder != place.folder:
            found += _matching(_list_images(place.season_folder), _season_folder_stems(place.season), variants=False)
        return found
    return []


def scheme_of(path: Path, place: Place) -> str | None:
    """Which naming scheme an existing file follows (for library-wide detection)."""
    stem = path.stem.lower()
    if place.kind == "season":
        in_root = path.parent == place.folder
        if in_root:
            return "root" if stem.endswith("-poster") else "root_plain"
        return "folder"
    if place.kind == "movie":
        if place.video_stem and stem == place.video_stem.lower():
            return "file"
        return stem if stem in MOVIE_STEMS else None
    if place.kind == "show":
        base = _VARIANT.match(stem).group("stem") if _VARIANT.match(stem) else stem
        return base if base in SHOW_STEMS else None
    return None


def _season_dir(place: Place) -> Path | None:
    """The season's own folder: from Plex' episode paths, else "Season 03" in the show folder."""
    if place.season_folder and place.season_folder != place.folder:
        return place.season_folder
    n = place.season or 0
    names = ["season 00", "season 0", "staffel 00", "staffel 0", "specials"] if n == 0 else \
        [f"season {n:02d}", f"season {n}", f"season{n:02d}", f"staffel {n:02d}", f"staffel {n}"]
    try:
        with os.scandir(place.folder) as entries:
            dirs = {entry.name.lower(): Path(entry.path) for entry in entries if entry.is_dir()}
    except OSError:
        return None
    for name in names:
        if name in dirs:
            return dirs[name]
    return None


def _named(place: Place, ext: str, naming: dict) -> Path:
    """The file name the naming settings ask for."""
    if place.kind == "movie":
        mode = naming.get("movie", "poster")
        if place.shared or mode == "file":
            if not place.video_stem:
                raise PathError("Unknown movie file name")
            return place.folder / f"{place.video_stem}{ext}"
        return place.folder / f"{mode if mode in MOVIE_STEMS else 'poster'}{ext}"
    if place.kind == "show":
        mode = naming.get("show", "poster")
        return place.folder / f"{mode if mode in SHOW_STEMS else 'poster'}{ext}"
    if place.kind == "season" and place.season is not None:
        n = place.season
        mode = naming.get("season", "root")
        if n == 0 and mode == "root":
            # The show-folder name Plex documents for specials.
            return place.folder / f"season-specials-poster{ext}"
        # Specials like every other season: "Season 00" folder, Season00.jpg.
        if mode == "folder":
            season_dir = _season_dir(place)
            # No season folder (all episodes in the show folder): same name, one level up.
            return (season_dir or place.folder) / f"Season{n:02d}{ext}"
        if mode == "root_plain":
            return place.folder / f"Season{n:02d}{ext}"
        return place.folder / f"season{n:02d}-poster{ext}"
    raise PathError("Unknown poster slot")


def _keep_ext(path: Path, ext: str) -> Path:
    if path.suffix.lower() == ".tbn":
        return path.with_suffix(ext)
    if ext == ".jpg" and path.suffix.lower() in (".jpg", ".jpeg"):
        return path
    return path.with_suffix(ext)


def target(place: Place, ext: str, naming: dict) -> Path:
    """Where a new poster for this slot goes.

    With a fixed naming setting the configured name wins (other files for the
    same slot are archived as conflicts). With "as found" an existing
    file keeps its name and place.
    """
    current = existing(place)
    if place.kind in naming.get("explicit", ()) and not (place.kind == "movie" and place.shared):
        wanted = _named(place, ext, naming)
        for path in current:
            if path.parent == wanted.parent and path.stem.lower() == wanted.stem.lower():
                return _keep_ext(path, ext)
        return wanted
    if current:
        return _keep_ext(current[0], ext)
    return _named(place, ext, naming)


def current(place: Place, naming: dict | None = None) -> Path | None:
    """The file Plex reads for this slot; with a fixed naming setting its name comes first."""
    files = existing(place)
    if not files:
        return None
    if naming and place.kind in naming.get("explicit", ()):
        try:
            wanted = _named(place, ".jpg", naming)
        except PathError:
            return files[0]
        for path in files:
            if path.parent == wanted.parent and path.stem.lower() == wanted.stem.lower():
                return path
    return files[0]


def conflicts(place: Place, chosen: Path) -> list[Path]:
    """Other files Plex could pick for the same slot instead of chosen."""
    chosen_key = str(chosen).lower()
    return [p for p in existing(place) if str(p).lower() != chosen_key]


def check_folder(folder: Path) -> None:
    if not folder.exists():
        raise PathError(f"Folder not found: {folder}. Check the path mapping under Settings › Plex.")
    if not folder.is_dir():
        raise PathError(f"Not a folder: {folder}")
    if not os.access(folder, os.W_OK):
        raise PathError(f"No write access in {folder} (check PUID/PGID and the volume)")


def inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(folder.resolve(strict=False))
        return True
    except ValueError:
        return False


def _owner(path: Path) -> None:
    if os.name != "posix" or os.geteuid() != 0:
        return
    if config.PUID is None:
        return
    try:
        os.chown(path, config.PUID, config.PGID if config.PGID is not None else config.PUID)
    except OSError:
        pass


def _chmod(path: Path) -> None:
    """Best effort, like Sonarr/Radarr: on NFS/SMB shares (e.g. Unraid with squashed users)
    the server owns the file and refuses chmod, although writing works."""
    try:
        os.chmod(path, config.file_mode())
    except OSError:
        pass


def write_atomic(path: Path, data: bytes) -> None:
    """Write to a hidden temp file next to the target, then rename over it."""
    tmp = path.with_name(f".{path.name}.programarr-{uuid.uuid4().hex[:8]}.tmp")
    step = "create file"
    try:
        with open(tmp, "wb") as handle:
            step = "write file"
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _chmod(tmp)
        _owner(tmp)
        step = "replace file"
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise PathError(f"Write failed ({step}): {path}: {exc.strerror or exc} "
                        f"(Programarr runs as UID {os.geteuid()}, GID {os.getegid()})") from exc


# -- archive ---------------------------------------------------------------
def archive_dir(slot_id: str) -> Path:
    return config.DATA_DIR / "archive" / safe_name(slot_id.replace(":", "_"))


def archive(path: Path, slot_id: str, move: bool) -> Path:
    """Keep a copy of a poster file before it is replaced (move=True removes the original)."""
    folder = archive_dir(slot_id)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = folder / f"{stamp}-{uuid.uuid4().hex[:4]}__{path.name}"
    try:
        # Contents only: file metadata from network shares cannot always be copied.
        shutil.copyfile(path, dest)
        if move:
            path.unlink()
    except OSError as exc:
        if path.exists():
            dest.unlink(missing_ok=True)  # original still there: no half archive entry
        raise PathError(f"Archiving failed: {path} ({exc.strerror or exc})") from exc
    return dest


def prune_archive(slot_id: str, keep: int) -> list[Path]:
    folder = archive_dir(slot_id)
    if keep <= 0 or not folder.exists():
        return []
    files = sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: p.name, reverse=True)
    removed = []
    for old in files[keep:]:
        try:
            old.unlink()
            removed.append(old)
        except OSError:
            pass
    return removed


def original_name(archived: Path) -> str:
    return archived.name.split("__", 1)[-1]
