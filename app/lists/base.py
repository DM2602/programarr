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
    type: str  # text | number | decimal | select | multi | bool | tokens (a list of free values)
    options: list[tuple[str, str]] = field(default_factory=list)
    default: Any = None
    required: bool = True
    placeholder: str | None = None
    suggestions: list[str] = field(default_factory=list)
    min: float | None = None
    max: float | None = None
    when: dict[str, list[str]] | None = None  # shown only when another field has one of these values
    step: float | None = None
    dynamic: str | None = None  # options or suggestions the web app loads, e.g. radarr.profiles

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
            "step": self.step,
            "dynamic": self.dynamic,
        }

    def shown(self, values: dict) -> bool:
        if not self.when:
            return True
        return all(str(values.get(key)) in allowed for key, allowed in self.when.items())


def _bound(spec: Field, value: float) -> str | None:
    if spec.min is not None and value < spec.min or spec.max is not None and value > spec.max:
        low = int(spec.min) if spec.min is not None and float(spec.min).is_integer() else spec.min
        high = int(spec.max) if spec.max is not None and float(spec.max).is_integer() else spec.max
        return f"{spec.label}: {low}–{high}"
    return None


def clean_values(fields: list[Field], params: dict, error: type[Exception]) -> dict:
    """Checked and normalised values for a form; raises error with a message for the form.

    Hidden fields (see Field.when) keep their value but are not required.
    """
    clean: dict = {}
    params = params or {}
    for spec in fields:
        value = params.get(spec.key, spec.default)
        shown = spec.shown({**{f.key: params.get(f.key, f.default) for f in fields}})
        required = spec.required and shown
        if spec.type in ("number", "decimal"):
            if value in (None, "") and not required:
                clean[spec.key] = None
                continue
            try:
                value = int(value) if spec.type == "number" else round(float(value), 2)
            except (TypeError, ValueError):
                raise error(f"{spec.label}: enter a number") from None
            problem = _bound(spec, value)
            if problem:
                raise error(problem)
        elif spec.type == "bool":
            value = bool(value) if not isinstance(value, str) else value.lower() in ("1", "true", "yes", "on")
        elif spec.type == "multi":
            allowed = [option for option, _ in spec.options]
            value = [option for option in allowed if option in (value or [])]
            if required and not value:
                raise error(f"{spec.label}: choose at least one")
        elif spec.type == "tokens":
            items = value if isinstance(value, list) else str(value or "").split(",")
            seen: list[str] = []
            for item in items:
                text = str(item or "").strip()
                if text and text not in seen:
                    seen.append(text)
            value = seen
            if required and not value:
                raise error(f"{spec.label}: add at least one")
        elif spec.type == "select":
            allowed = [option for option, _ in spec.options]
            if spec.dynamic:
                value = "" if value is None else str(value).strip()
                if required and not value:
                    raise error(f"{spec.label}: choose one")
            elif value not in allowed:
                if not required and value in (None, ""):
                    value = None
                else:
                    raise error(f"{spec.label}: choose one")
        else:
            value = str(value or "").strip()
            if required and not value:
                raise error(f"{spec.label} is missing")
        clean[spec.key] = value
    return clean


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
        return clean_values(self.fields, params, SourceError)

    def ref(self, params: dict) -> str:
        """Stable reference for 'the same list' across runs."""
        parts = []
        for spec in self.fields:
            value = params.get(spec.key)
            parts.append(",".join(value) if isinstance(value, list) else str(value))
        return f"{self.key}:" + "|".join(parts)

    def describe(self, params: dict) -> str:
        raise NotImplementedError

    def name(self, params: dict) -> str:
        """Which list, in a few words, for a title's path through a flow."""
        return self.describe(params)

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
