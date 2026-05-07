"""JWT authentication helpers and FastAPI dependencies."""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt

log = logging.getLogger("spondbot")

ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 8
DEBUG = os.environ.get("DEBUG", "0") == "1"

_SECRET_KEY: str | None = os.environ.get("SPONDBOT_SECRET")
if not _SECRET_KEY:
    _SECRET_KEY = secrets.token_hex(32)
    log.warning(
        "SPONDBOT_SECRET not set — using a random key. "
        "All sessions will be invalidated on restart. "
        "Set SPONDBOT_SECRET in your environment for persistent sessions."
    )

COOKIE_KWARGS = dict(
    httponly=True,
    secure=not DEBUG,
    samesite="strict",
    path="/",
    max_age=TOKEN_EXPIRE_HOURS * 3600,
)


def create_access_token(user_id: str, username: str, is_admin: bool) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": user_id, "username": username, "is_admin": is_admin, "exp": expire},
        _SECRET_KEY,
        algorithm=ALGORITHM,
    )


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, _SECRET_KEY, algorithms=[ALGORITHM])
        if not payload.get("sub"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return payload
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("sb_session")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    payload = decode_token(token)
    return {
        "id": payload["sub"],
        "username": payload["username"],
        "is_admin": payload.get("is_admin", False),
    }


async def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
