"""Deterministic poster images for the simulated services."""
from __future__ import annotations

import hashlib
import io
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont


def seed_of(text: str) -> int:
    return int(hashlib.sha1(text.encode()).hexdigest()[:8], 16)


@lru_cache(maxsize=512)
def poster(seed: int, label: str = "", width: int = 1000, height: int = 1500, fmt: str = "JPEG",
           quality: int = 92) -> bytes:
    rnd = random.Random(seed)
    base = tuple(rnd.randint(20, 200) for _ in range(3))
    img = Image.new("RGB", (1000, 1500), base)
    draw = ImageDraw.Draw(img)
    for _ in range(14):
        x0, y0 = rnd.randint(-200, 1000), rnd.randint(-200, 1500)
        size = rnd.randint(120, 700)
        color = tuple(rnd.randint(0, 255) for _ in range(3))
        if rnd.random() < 0.5:
            draw.ellipse([x0, y0, x0 + size, y0 + size], fill=color)
        else:
            draw.rectangle([x0, y0, x0 + size, y0 + int(size * 0.6)], fill=color)
    if label:
        draw.rectangle([0, 1260, 1000, 1500], fill=(12, 12, 14))
        try:
            font = ImageFont.load_default(size=96)
        except TypeError:  # pragma: no cover - old Pillow
            font = ImageFont.load_default()
        draw.text((60, 1310), label[:18], fill=(245, 240, 230), font=font)
    if (width, height) != (1000, 1500):
        img = img.resize((width, height), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    if fmt == "JPEG":
        img.save(out, "JPEG", quality=quality)
    else:
        img.save(out, fmt)
    return out.getvalue()


def poster_for(name: str, label: str = "", thumb: bool = False, fmt: str = "JPEG") -> bytes:
    if thumb:
        return poster(seed_of(name), label, 300, 450, fmt, 80)
    return poster(seed_of(name), label, fmt=fmt)
