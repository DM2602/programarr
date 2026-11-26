"""Common types for poster sources."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SOURCE_NAMES = {
    "mediux": "MediUX",
    "tpdb": "ThePosterDB",
    "plex": "Plex",
    "tmdb": "TMDB",
    "fanart": "fanart.tv",
    "tvmaze": "TVmaze",
    "url": "Link",
    "upload": "Upload",
    "extern": "Changed in folder",
    "archive": "Archive",
    "backup": "Backup",
}


class SourceError(Exception):
    """A source could not deliver; the message is for the UI."""


@dataclass
class ImageRef:
    source: str
    image_id: str
    kind: str  # movie | show | season
    season: int | None = None
    url: str = ""
    thumb_url: str = ""
    modified: str | None = None
    language: str | None = None
    designer: str | None = None
    set_key: str | None = None
    set_title: str | None = None
    label: str = ""
    width: int | None = None
    height: int | None = None
    likes: int | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ImageRef":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        values = {k: v for k, v in (data or {}).items() if k in known}
        if values.get("season") is not None:
            values["season"] = int(values["season"])
        return cls(**values)

    def version(self) -> str:
        """Identity of the exact image version, used to spot changes in a set."""
        return f"{self.image_id}@{self.modified or ''}"


@dataclass
class PosterSet:
    source: str
    set_key: str
    title: str
    designer: str
    url: str = ""
    updated: str | None = None
    popularity: float | None = None
    images: list[ImageRef] = field(default_factory=list)
    complete: bool = True  # False while only part of the set is known (TPDB item page)
    extra: dict = field(default_factory=dict)

    def image_for(self, kind: str, season: int | None = None) -> ImageRef | None:
        for image in self.images:
            if image.kind == kind and (kind != "season" or image.season == season):
                return image
        return None

    def seasons(self) -> set[int]:
        return {img.season for img in self.images if img.kind == "season" and img.season is not None}

    def has_show_poster(self) -> bool:
        return any(img.kind == "show" for img in self.images)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["images"] = [img.to_dict() for img in self.images]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "PosterSet":
        values: dict[str, Any] = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        values["images"] = [ImageRef.from_dict(img) for img in data.get("images", [])]
        return cls(**values)


def slot_id(kind: str, item_key: str, season: int | None = None) -> str:
    if kind == "movie":
        return f"m:{item_key}"
    if kind == "show":
        return f"s:{item_key}"
    return f"S:{item_key}:{int(season or 0)}"


def parse_slot_id(value: str) -> tuple[str, str, int | None]:
    parts = value.split(":")
    if parts[0] == "m":
        return "movie", parts[1], None
    if parts[0] == "s":
        return "show", parts[1], None
    if parts[0] == "S" and len(parts) == 3:
        return "season", parts[1], int(parts[2])
    raise ValueError(f"Unknown poster slot: {value}")
