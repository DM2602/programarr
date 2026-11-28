"""Discord messages (webhook), with the poster attached as image."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from pathlib import Path

import httpx

from . import http, imaging, settings
from .config import APP_NAME

AVATAR = Path(__file__).parent / "assets" / "avatar.png"
WEBHOOK_PATH = re.compile(r"^/api(/v\d+)?/webhooks/\d+/[\w-]+$")

COLORS = {
    "auto": 0x3FA36B,
    "season": 0x3FA36B,
    "waiting": 0xE0A43A,
    "review": 0xE0A43A,
    "set_changed": 0x4F8FD6,
    "detect": 0x4F8FD6,
    "error": 0xD0533F,
    "flow": 0x6366F1,
    "flow_dry": 0x8A8F98,
    "collection": 0x3FA36B,
    "test": 0x8A8F98,
}


async def set_avatar(url: str) -> bool:
    """Gives the webhook Programarr's mark as picture. A message can only name a picture by a public
    address, which Programarr does not have, so the webhook itself gets it (once per webhook URL)."""
    parsed = httpx.URL(url)
    if not WEBHOOK_PATH.match(parsed.path):
        return False
    data = "data:image/png;base64," + base64.b64encode(AVATAR.read_bytes()).decode()
    try:
        async with http.client(timeout=15.0) as client:
            response = await client.patch(str(parsed.copy_with(query=None)), json={"avatar": data})
    except httpx.HTTPError:
        return False
    return response.status_code < 300


async def _ensure_avatar(url: str) -> None:
    mark = hashlib.sha1(url.encode()).hexdigest()[:16]
    if settings.get("discord_avatar_for") != mark and await set_avatar(url):
        settings.set("discord_avatar_for", mark)


async def send(kind: str, title: str, description: str = "", image: bytes | None = None,
               item_key: str | None = None, fields: list[tuple[str, str]] | None = None,
               webhook: str | None = None, path: str | None = None) -> bool:
    url = webhook or settings.get("discord_webhook")
    if not url:
        return False
    if kind != "test" and not settings.get("discord_events").get(kind, True):
        return False
    await _ensure_avatar(url)
    embed: dict = {
        "title": title[:250],
        "description": description[:3500],
        "color": COLORS.get(kind, 0x8A8F98),
        "footer": {"text": APP_NAME},
    }
    base = (settings.get("base_url") or "").rstrip("/")
    if base and item_key:
        embed["url"] = f"{base}/item/{item_key}"
    elif base and path:
        embed["url"] = f"{base}{path}"
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
