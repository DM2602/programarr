"""Discord messages (webhook), with the poster attached as image."""
from __future__ import annotations

import asyncio
import json

import httpx

from . import http, imaging, settings
from .config import APP_NAME

COLORS = {
    "auto": 0x3FA36B,
    "season": 0x3FA36B,
    "waiting": 0xE0A43A,
    "review": 0xE0A43A,
    "set_changed": 0x4F8FD6,
    "detect": 0x4F8FD6,
    "error": 0xD0533F,
    "test": 0x8A8F98,
}


async def send(kind: str, title: str, description: str = "", image: bytes | None = None,
               item_key: str | None = None, fields: list[tuple[str, str]] | None = None,
               webhook: str | None = None) -> bool:
    url = webhook or settings.get("discord_webhook")
    if not url:
        return False
    if kind != "test" and not settings.get("discord_events").get(kind, True):
        return False
    embed: dict = {
        "title": title[:250],
        "description": description[:3500],
        "color": COLORS.get(kind, 0x8A8F98),
        "footer": {"text": APP_NAME},
    }
    base = (settings.get("base_url") or "").rstrip("/")
    if base and item_key:
        embed["url"] = f"{base}/item/{item_key}"
    if fields:
        embed["fields"] = [{"name": name[:250], "value": value[:1000] or "–", "inline": True} for name, value in fields]
    files = None
    if image:
        try:
            thumb = imaging.thumbnail(image, 300)
            files = {"files[0]": ("poster.jpg", thumb, "image/jpeg")}
            embed["thumbnail"] = {"url": "attachment://poster.jpg"}
        except imaging.ImageError:
            files = None
    payload = {"username": APP_NAME, "embeds": [embed]}
    async with http.client(timeout=20.0) as client:
        for attempt in range(2):
            try:
                if files:
                    response = await client.post(url, data={"payload_json": json.dumps(payload)}, files=files)
                else:
                    response = await client.post(url, json=payload)
            except httpx.HTTPError:
                return False
            if response.status_code == 429 and attempt == 0:
                try:
                    wait = float(response.json().get("retry_after", 2))
                except ValueError:
                    wait = 2.0
                await asyncio.sleep(min(wait, 10.0))
                continue
            return response.status_code < 300
    return False
