"""Polite page fetching for scraped list sources, with a Cloudflare check and FlareSolverr as fallback."""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

import httpx

from .. import http, settings
from .base import SourceError


class NotFound(SourceError):
    pass


_locks: dict[str, asyncio.Lock] = {}
_last: dict[str, float] = {}
_clearance: dict[str, tuple[dict, str]] = {}  # host -> (cookies, user agent) from FlareSolverr


def _lock(host: str) -> asyncio.Lock:
    lock = _locks.get(host)
    if lock is None:
        lock = _locks[host] = asyncio.Lock()
    return lock


async def page(url: str, delay: float, name: str) -> str:
    """HTML of a page, one request at a time per host with a pause in between."""
    host = urlsplit(url).hostname or ""
    async with _lock(host):
        wait = _last.get(host, 0.0) + max(0.0, delay) - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            status, text = await _direct(url, host)
            if http.is_challenge(status, text):
                status, text = await _flaresolverr(url, host, name)
        finally:
            _last[host] = time.monotonic()
    if status == 404:
        raise NotFound(f"{name}: page not found ({urlsplit(url).path})")
    if status >= 400:
        raise SourceError(f"{name} answered {status}")
    return text


async def _direct(url: str, host: str) -> tuple[int, str]:
    cookies, agent = _clearance.get(host, ({}, http.BROWSER_UA))
    headers = {
        "User-Agent": agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.8",
    }
    async with http.client(timeout=30.0, headers=headers, cookies=cookies) as client:
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            raise SourceError(f"Cannot reach {host}: {exc.__class__.__name__}") from exc
    return response.status_code, response.text


async def _flaresolverr(url: str, host: str, name: str) -> tuple[int, str]:
    endpoint = (settings.get("flaresolverr_url") or "").rstrip("/")
    if not endpoint:
        raise SourceError(f"{name} shows a Cloudflare check. Set up FlareSolverr under Settings › Sources.")
    body = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
    async with http.client(timeout=90.0) as client:
        try:
            response = await client.post(f"{endpoint}/v1", json=body)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"Cannot reach FlareSolverr ({endpoint})") from exc
    if data.get("status") != "ok":
        raise SourceError(f"FlareSolverr: {data.get('message', 'error')}")
    solution = data.get("solution") or {}
    cookies = {c["name"]: c["value"] for c in solution.get("cookies") or [] if c.get("name")}
    if cookies:
        _clearance[host] = (cookies, solution.get("userAgent") or http.BROWSER_UA)
    text = solution.get("response") or ""
    status = int(solution.get("status") or 200)
    if http.is_challenge(status, text):
        raise SourceError(f"{name} still shows a Cloudflare check")
    return status, text


def reset() -> None:
    """Forget pauses and clearance cookies (tests)."""
    _last.clear()
    _clearance.clear()
    _locks.clear()
