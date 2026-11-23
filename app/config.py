"""Process-wide configuration from environment variables."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

VERSION = "0.1.0"
APP_NAME = "Programarr"

DATA_DIR = Path(os.environ.get("PROGRAMARR_DATA_DIR", "/data")).resolve()
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
LOG_LEVEL = os.environ.get("PROGRAMARR_LOG_LEVEL", "INFO").upper()
TZ = os.environ.get("TZ", "Europe/Zurich")
# The scheduler can be switched off for tests and the demo.
SCHEDULER = os.environ.get("PROGRAMARR_SCHEDULER", "1") not in ("0", "false", "no")


def _int_env(name: str, default: int | None) -> int | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value, 0) if value.startswith("0") and len(value) > 1 else int(value)
    except ValueError:
        return default


# Owner of written poster files. The container normally runs as PUID/PGID
# already; these are applied explicitly when the process runs as root.
PUID = _int_env("PUID", None)
PGID = _int_env("PGID", None)
UMASK = os.environ.get("UMASK", "002")


def file_mode() -> int:
    try:
        mask = int(UMASK, 8)
    except ValueError:
        mask = 0o002
    return 0o666 & ~mask


def ensure_dirs() -> None:
    for sub in ("", "archive", "cache", "cache/thumbs", "cache/images", "logs", "uploads"):
        (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)


def secret_key() -> str:
    """Session signing key, created once per data directory."""
    ensure_dirs()
    path = DATA_DIR / "secret.key"
    if path.exists():
        value = path.read_text().strip()
        if value:
            return value
    value = secrets.token_urlsafe(48)
    path.write_text(value)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


def set_data_dir(path: str | Path) -> None:
    """Used by tests and the demo to point the app at a temporary folder."""
    global DATA_DIR
    DATA_DIR = Path(path).resolve()
