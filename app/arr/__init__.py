"""Radarr and Sonarr: links on title pages (phase 2) and flow outputs (phase 3)."""
from .client import (  # noqa: F401  (the package's API)
    SERVICES,
    ArrError,
    configured,
    for_kind,
    get,
    label,
    link_base,
    lookup,
    request,
    test,
)
