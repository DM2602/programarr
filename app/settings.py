"""Settings live in the database as JSON values, with defaults here."""
from __future__ import annotations

import copy
import secrets
from typing import Any

from . import db

DISCORD_EVENTS = {
    "auto": "Poster set automatically (new title)",
    "season": "New season: poster set from the linked set",
    "waiting": "New season is waiting for its set",
    "review": "Something needs your review",
    "set_changed": "Linked set changed",
    "detect": "Match existing finished",
    "flow": "Flow added titles to Radarr or Sonarr",
    "flow_dry": "Dry run found titles to add",
    "error": "Errors (service unreachable, write or flow failed)",
}

DEFAULTS: dict[str, Any] = {
    "setup_done": False,
    # Plex
    "plex_url": "",
    "plex_token": "",
    "path_maps": [],
    # Files
    "naming_movie": "auto",
    "naming_show": "show",
    "naming_season": "folder",
    "keep_png": True,
    "conflicts": "archive",
    "unlock_fields": True,
    "verify_plex": True,
    "archive_keep": 10,
    "backup_posters": True,  # a copy of every poster file in /data/backup, put back when a file goes missing
    "plex_poster_files": "movies",  # titles without a file: save Plex' poster as file (off, movies, all)
    # MediUX
    "mediux_token": "",
    "mediux_quality": "original",
    "mediux_api": "https://images.mediux.io",
    "mediux_site": "https://mediux.pro",
    # ThePosterDB
    "tpdb_email": "",
    "tpdb_password": "",
    "tpdb_cookies": {},
    "tpdb_base": "https://theposterdb.com",
    "tpdb_delay": 1.5,
    "tpdb_pages": 2,
    "flaresolverr_url": "",
    # Other online sources
    "tmdb_key": "",
    "tmdb_api": "https://api.themoviedb.org/3",
    "tmdb_image": "https://image.tmdb.org/t/p",
    "tmdb_languages": "de,en,null",
    "fanart_key": "",
    "fanart_client_key": "",
    "fanart_api": "https://webservice.fanart.tv/v3",
    "tvmaze_enabled": True,
    "tvmaze_api": "https://api.tvmaze.com",
    "allow_private_urls": False,
    # Lists
    "mdblist_key": "",
    "mdblist_api": "https://api.mdblist.com",
    "trakt_client_id": "",
    "trakt_api": "https://api.trakt.tv",
    "flixpatrol_base": "https://flixpatrol.com",
    "flixpatrol_delay": 1.0,
    "ratings_days": 7,
    "runs_keep_days": 60,
    # Radarr and Sonarr (links now, flow outputs in phase 3)
    "radarr_url": "",
    "radarr_external_url": "",
    "radarr_key": "",
    "sonarr_url": "",
    "sonarr_external_url": "",
    "sonarr_key": "",
    # Automation. Off until switched on: Litfass keeps writing posters during the parallel run.
    "automation": False,
    "designers": [],
    "use_mediux_follows": True,
    "source_order": ["mediux", "tpdb"],
    "auto_new": True,
    "auto_require_complete": True,
    "auto_count_specials": False,
    "auto_collection_consistency": True,
    "poll_minutes": 15,
    "folder_check_hours": 24,
    "set_check_hours": 24,
    "wait_max_days": 180,
    "set_changes": "notify",
    "detect_movies": True,
    # Notifications and interfaces
    "discord_webhook": "",
    "discord_events": {key: key != "flow_dry" for key in DISCORD_EVENTS},
    "base_url": "",
    "hook_token": "",
    # Internal state
    "naming_detected": {},
    "mediux_follows": {"follow": [], "hide": [], "at": 0},
    "last_sync": 0,
    "last_set_check": 0,
    "last_folder_check": 0,
    "discord_avatar_for": "",
}

SECRETS = {
    "plex_token",
    "mediux_token",
    "tpdb_password",
    "tmdb_key",
    "fanart_key",
    "fanart_client_key",
    "discord_webhook",
    "mdblist_key",
    "trakt_client_id",
    "radarr_key",
    "sonarr_key",
}

INTERNAL = {"naming_detected", "mediux_follows", "last_sync", "last_set_check", "last_folder_check", "tpdb_cookies",
            "discord_avatar_for"}

_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is None:
        values = copy.deepcopy(DEFAULTS)
        for row in db.get().query("SELECT key, value FROM settings"):
            if row["key"] in DEFAULTS:
                values[row["key"]] = db.loads(row["value"], DEFAULTS[row["key"]])
        if not values.get("hook_token"):
            values["hook_token"] = secrets.token_urlsafe(24)
            db.get().upsert("settings", ("key",), {"key": "hook_token", "value": db.dumps(values["hook_token"])})
        if isinstance(values.get("discord_events"), dict):
            merged = dict(DEFAULTS["discord_events"])
            merged.update({k: bool(v) for k, v in values["discord_events"].items() if k in DISCORD_EVENTS})
            values["discord_events"] = merged
        _cache = values
    return _cache


def reset_cache() -> None:
    global _cache
    _cache = None


def get(key: str) -> Any:
    if key not in DEFAULTS:
        raise KeyError(key)
    return copy.deepcopy(_load()[key])


def all_values() -> dict[str, Any]:
    return copy.deepcopy(_load())


def set(key: str, value: Any) -> None:  # noqa: A001 - mirrors get()
    if key not in DEFAULTS:
        raise KeyError(key)
    db.get().upsert("settings", ("key",), {"key": key, "value": db.dumps(value)})
    _load()[key] = copy.deepcopy(value)


def update(values: dict[str, Any]) -> None:
    for key, value in values.items():
        set(key, value)


def public_view() -> dict[str, Any]:
    """Settings for the UI: secrets are reported as set/unset only."""
    view = {}
    for key, value in _load().items():
        if key in INTERNAL:
            continue
        if key in SECRETS:
            view[key] = bool(value)
        else:
            view[key] = copy.deepcopy(value)
    return view
