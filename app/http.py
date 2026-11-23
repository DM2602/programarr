"""HTTP client factory. Tests replace the transport to talk to simulated services."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from .config import VERSION

USER_AGENT = f"Programarr/{VERSION}"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
MAX_IMAGE_BYTES = 40 * 1024 * 1024

_transport: httpx.AsyncBaseTransport | None = None


class FetchError(Exception):
    """A download failed; the message is meant for the UI."""


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


def client(
    timeout: float = 30.0,
    headers: dict | None = None,
    follow_redirects: bool = True,
    cookies: httpx.Cookies | dict | None = None,
) -> httpx.AsyncClient:
    base_headers = {"User-Agent": USER_AGENT}
    if headers:
        base_headers.update(headers)
    return httpx.AsyncClient(
        transport=_transport,
        timeout=httpx.Timeout(timeout, connect=min(timeout, 15.0)),
        headers=base_headers,
        follow_redirects=follow_redirects,
        cookies=cookies,
    )


async def read_limited(response: httpx.Response, limit: int = MAX_IMAGE_BYTES) -> bytes:
    chunks = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            raise FetchError(f"File larger than {limit // (1024 * 1024)} MB")
        chunks.append(chunk)
    return b"".join(chunks)


def _is_private(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def _check_host(host: str, port: int) -> None:
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"Cannot resolve address: {host}") from exc
    for info in infos:
        if _is_private(info[4][0]):
            raise FetchError(
                "The address points into your own network. Allow private addresses under Settings › Sources."
            )


async def fetch_public(url: str, allow_private: bool = False, max_redirects: int = 5) -> tuple[bytes, str]:
    """Download an image from a URL the user pasted, checking every redirect hop."""
    current = url.strip()
    async with client(timeout=40.0, follow_redirects=False, headers={"User-Agent": BROWSER_UA}) as http:
        for _ in range(max_redirects + 1):
            parts = urlsplit(current)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise FetchError("Only http and https addresses are allowed")
            port = parts.port or (443 if parts.scheme == "https" else 80)
            if not allow_private and _transport is None:
                if port not in (80, 443):
                    raise FetchError("Only the standard ports 80 and 443 are allowed")
                await _check_host(parts.hostname, port)
            async with http.stream("GET", current) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError("Redirect without a target")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise FetchError(f"Server answered {response.status_code}")
                data = await read_limited(response)
                return data, response.headers.get("content-type", "")
    raise FetchError("Too many redirects")
