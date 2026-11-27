"""Signed image and set references: the browser only hands back what the server offered."""
from __future__ import annotations

from itsdangerous import BadSignature, URLSafeSerializer

from .. import config
from .sources.base import ImageRef, PosterSet, SourceError

_serializer: URLSafeSerializer | None = None


def _s() -> URLSafeSerializer:
    global _serializer
    if _serializer is None:
        _serializer = URLSafeSerializer(config.secret_key(), salt="programarr-ref")
    return _serializer


def reset() -> None:
    global _serializer
    _serializer = None


def sign_ref(ref: ImageRef) -> str:
    return _s().dumps(ref.to_dict())


def sign_set(poster_set: PosterSet) -> str:
    return _s().dumps({"set": poster_set.to_dict()})


def ref(token: str) -> ImageRef:
    try:
        return ImageRef.from_dict(_s().loads(token))
    except (BadSignature, TypeError, ValueError) as exc:
        raise SourceError("Invalid image reference") from exc


def poster_set(token: str) -> PosterSet:
    try:
        return PosterSet.from_dict(_s().loads(token)["set"])
    except (BadSignature, TypeError, ValueError, KeyError) as exc:
        raise SourceError("Invalid set reference") from exc


def image_dict(image: ImageRef) -> dict:
    """What the picker needs about one image, with its signed token."""
    token = sign_ref(image)
    return {
        "token": token,
        "thumb": f"/img/ref/{token}",
        "large": f"/img/ref/{token}?size=large",
        "kind": image.kind,
        "season": image.season,
        "label": image.label,
        "language": image.language,
        "designer": image.designer,
        "likes": image.likes,
        "width": image.width,
        "height": image.height,
        "source": image.source,
        "selected": bool(image.extra.get("selected")),
        "local": bool(image.extra.get("local")),
    }
