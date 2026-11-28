"""Optional password sign-in (APP_PASSWORD). Without it, the proxy in front (e.g. Pangolin SSO) guards access."""
from __future__ import annotations

import asyncio
import hmac

from fastapi import APIRouter, HTTPException, Request

from .. import config
from .schemas import AuthState, LoginBody

router = APIRouter(prefix="/api/auth", tags=["auth"])


def signed_in(request: Request) -> bool:
    return not config.APP_PASSWORD or request.session.get("auth") == "ok"


def _state(request: Request) -> AuthState:
    return AuthState(password_required=bool(config.APP_PASSWORD), signed_in=signed_in(request),
                     version=config.VERSION)


@router.get("/state", response_model=AuthState)
async def state(request: Request):
    return _state(request)


@router.post("/login", response_model=AuthState)
async def login(request: Request, body: LoginBody):
    if not config.APP_PASSWORD:
        return _state(request)
    if not hmac.compare_digest(body.password.encode(), config.APP_PASSWORD.encode()):
        await asyncio.sleep(0.6)  # slows down guessing
        raise HTTPException(401, "Incorrect password")
    request.session["auth"] = "ok"
    return _state(request)


@router.post("/logout", response_model=AuthState)
async def logout(request: Request):
    request.session.clear()
    return _state(request)
