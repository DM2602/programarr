"""Programarr web application: JSON API, poster images and the web app (single-page app)."""
from __future__ import annotations

import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from . import config, db, jobs, settings
from .flows import store as flow_store
from .lists import runs
from .posters import refsign
from .web import auth, collections, flows, hooks, images, lists, posters, system

DIST_DIR = Path(__file__).resolve().parent / "web" / "dist"

# Paths reachable without signing in (when APP_PASSWORD is set). The web app itself
# loads freely and shows its sign-in page; only data needs a session.
PROTECTED = ("/api/", "/img/")
OPEN_API = ("/api/auth/", "/api/hooks/")
CHANGING = ("POST", "PUT", "PATCH", "DELETE")

NOT_BUILT = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Programarr</title>
<body style="font-family:system-ui;background:#111827;color:#e5e7eb;padding:40px">
<h1>Programarr</h1><p>The web app is not built. Run <code>npm run build</code> in <code>frontend/</code>
or use the Docker image. The API is at <a style="color:#a5b4fc" href="/api/docs">/api/docs</a>.</p></body></html>"""


def setup_logging() -> None:
    root = logging.getLogger()
    if any(getattr(h, "_programarr", False) for h in root.handlers):
        return
    root.setLevel(config.LOG_LEVEL)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream._programarr = True  # type: ignore[attr-defined]
    root.addHandler(stream)
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            config.DATA_DIR / "logs" / "programarr.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler._programarr = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    except OSError:
        pass
    logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    setup_logging()
    db.init(config.DATA_DIR / "programarr.db")
    settings.reset_cache()
    refsign.reset()
    runs.recover()
    flow_store.recover()
    jobs.runner.start(scheduler=config.SCHEDULER)
    logging.getLogger("programarr").info("Programarr %s started, data in %s", config.VERSION, config.DATA_DIR)
    yield
    await jobs.runner.stop()


def _dist_file(path: str) -> Path | None:
    """A file of the built web app, never outside its folder."""
    if not path or not DIST_DIR.is_dir():
        return None
    target = (DIST_DIR / path).resolve()
    if target.is_file() and target.is_relative_to(DIST_DIR.resolve()):
        return target
    return None


def create_app() -> FastAPI:
    app = FastAPI(title=config.APP_NAME, version=config.VERSION, docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json", lifespan=lifespan)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        is_api = path.startswith("/api/")
        # Changes need a header a foreign web page cannot set without CORS (CSRF protection).
        if request.method in CHANGING and is_api and not path.startswith("/api/hooks/") \
                and request.headers.get("x-programarr") != "1":
            return JSONResponse({"detail": "Missing header X-Programarr: 1"}, status_code=403)
        if config.APP_PASSWORD and path.startswith(PROTECTED) and not path.startswith(OPEN_API) \
                and not auth.signed_in(request):
            return JSONResponse({"detail": "Sign in required"}, status_code=401)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    # Added after the guard, so the session is available inside it.
    app.add_middleware(SessionMiddleware, secret_key=config.secret_key(), session_cookie="programarr_session",
                       max_age=30 * 86400, same_site="lax")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    @app.get("/health", include_in_schema=False)
    async def health():
        db.get().scalar("SELECT 1")
        return {"ok": True, "version": config.VERSION}

    app.include_router(auth.router)
    app.include_router(system.router)
    app.include_router(posters.router)
    app.include_router(lists.router)
    app.include_router(flows.router)
    app.include_router(collections.router)
    app.include_router(hooks.router)
    app.include_router(images.router)

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def web_app(path: str):
        if path.startswith(("api/", "img/")) or path in ("api", "img"):
            raise HTTPException(404, "Not found")
        found = _dist_file(path)
        if found:
            return FileResponse(found)
        if path.startswith("assets/"):
            raise HTTPException(404, "Not found")
        index = _dist_file("index.html")
        if not index:
            return HTMLResponse(NOT_BUILT)
        # Every app route (/library, /item/123, …) gets the same page; the app routes in the browser.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
