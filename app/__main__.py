"""python -m app: start Programarr with uvicorn (one worker; jobs and SQLite share the process)."""
import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("PROGRAMARR_INTERNAL_PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, workers=1, proxy_headers=True,
                forwarded_allow_ips="*", log_level=os.environ.get("PROGRAMARR_LOG_LEVEL", "info").lower(),
                access_log=False)


if __name__ == "__main__":
    main()
