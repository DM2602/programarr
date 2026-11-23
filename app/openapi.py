"""Print the API's OpenAPI document: python -m app.openapi > frontend/openapi.json

The web app generates its TypeScript types from that file (npm run types).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile


def main() -> None:
    # Building the app touches the data folder (session key); keep that out of the real one.
    os.environ.setdefault("PROGRAMARR_DATA_DIR", tempfile.mkdtemp(prefix="programarr-openapi-"))
    os.environ.setdefault("PROGRAMARR_SCHEDULER", "0")
    from .main import create_app

    json.dump(create_app().openapi(), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
