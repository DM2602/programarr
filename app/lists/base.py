"""What every list source delivers, and the form it asks for."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class SourceError(Exception):
    """A list could not be read; the message is meant for the UI."""


@dataclass
class RawEntry:
    position: int
    title: str
    year: int | None = None
    kind: str | None = None  # movie | show | None when the source does not say
    ids: dict[str, Any] = field(default_factory=dict)  # tmdb, imdb, tvdb
    url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Fetched:
    entries: list[RawEntry]
    notes: list[str] = field(default_factory=list)  # shown with the run, e.g. a missing section


@dataclass
class Field:
    key: str
    label: str
    type: str  # text | number | select | multi
    options: list[tuple[str, str]] = field(default_factory=list)
    default: Any = None
    required: bool = True
    placeholder: str | None = None
    suggestions: list[str] = field(default_factory=list)
    min: int | None = None
    max: int | None = None
    when: dict[str, list[str]] | None = None  # shown only when another field has one of these values

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "options": [{"value": value, "label": label} for value, label in self.options],
            "default": self.default,
            "required": self.required,
            "placeholder": self.placeholder,
            "suggestions": self.suggestions,
            "min": self.min,
            "max": self.max,
            "when": self.when,
        }


class Adapter:
    """One list source. Subclasses fill in the form, the checks and the reading."""

    key = ""
    label = ""
    fields: ClassVar[list[Field]] = []

    def missing(self) -> str | None:
        """What has to be set up first, or None."""
        return None

    def clean(self, params: dict) -> dict:
        """Checked and normalised parameters; raises SourceError with a message for the form."""
        clean: dict = {}
        for spec in self.fields:
            value = params.get(spec.key, spec.default)
            if spec.type == "number":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise SourceError(f"{spec.label}: enter a number") from None
                if spec.min is not None and value < spec.min or spec.max is not None and value > spec.max:
                    raise SourceError(f"{spec.label}: {spec.min}–{spec.max}")
            elif spec.type == "multi":
                allowed = [option for option, _ in spec.options]
                value = [option for option in allowed if option in (value or [])]
                if spec.required and not value:
                    raise SourceError(f"{spec.label}: choose at least one")
            elif spec.type == "select":
                allowed = [option for option, _ in spec.options]
                if value not in allowed:
                    raise SourceError(f"{spec.label}: choose one")
            else:
                value = str(value or "").strip()
                if spec.required and not value:
                    raise SourceError(f"{spec.label} is missing")
            clean[spec.key] = value
        return clean

    def ref(self, params: dict) -> str:
        """Stable reference for 'the same list' across runs."""
        parts = []
        for spec in self.fields:
            value = params.get(spec.key)
            parts.append(",".join(value) if isinstance(value, list) else str(value))
        return f"{self.key}:" + "|".join(parts)

    def describe(self, params: dict) -> str:
        raise NotImplementedError

    async def fetch(self, params: dict) -> Fetched:
        raise NotImplementedError

    def info(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "fields": [spec.as_dict() for spec in self.fields],
            "missing": self.missing(),
        }


def slug(value: str, label: str) -> str:
    text = str(value or "").strip().lower().strip("/")
    if not SLUG.match(text):
        raise SourceError(f"{label}: use the name from the address, e.g. united-states")
    return text


def int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
