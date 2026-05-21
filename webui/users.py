"""File-backed user store for multi-user SpondBot."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

import bcrypt

log = logging.getLogger("spondbot")

DATA_DIR = Path(os.environ.get("SPONDBOT_DATA", "./data"))
USERS_PATH = DATA_DIR / "users.json"

# Dummy hash used to keep bcrypt timing consistent for non-existent usernames.
_DUMMY_HASH = bcrypt.hashpw(b"__dummy__", bcrypt.gensalt(rounds=12))


# ---------- password helpers ----------

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(plain: str, hashed: str | None) -> bool:
    """Always runs bcrypt — prevents timing-based username enumeration."""
    raw = plain.encode("utf-8")
    if hashed is None:
        bcrypt.checkpw(raw, _DUMMY_HASH)
        return False
    return bcrypt.checkpw(raw, hashed.encode("ascii"))


# ---------- persistence ----------

def _load_raw() -> list[dict]:
    try:
        return json.loads(USERS_PATH.read_text())
    except FileNotFoundError:
        return []
    except Exception as exc:
        log.error("could not load users.json: %s", exc)
        return []


def _save_raw(users: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = USERS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(users, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    tmp.replace(USERS_PATH)
    try:
        os.chmod(USERS_PATH, 0o600)
    except Exception:
        pass


# ---------- CRUD ----------

def load_users() -> list[dict]:
    return [_public(u) for u in _load_raw()]


def get_user_by_id(user_id: str) -> dict | None:
    return next((u for u in _load_raw() if u["id"] == user_id), None)


def get_user_by_username(username: str) -> dict | None:
    return next(
        (u for u in _load_raw() if u["username"].lower() == username.lower()), None
    )


def create_user(username: str, password: str, is_admin: bool = False) -> dict:
    users = _load_raw()
    if any(u["username"].lower() == username.lower() for u in users):
        raise ValueError(f"Username '{username}' already exists")
    user = {
        "id": str(uuid.uuid4()),
        "username": username,
        "hashed_password": hash_password(password),
        "is_admin": is_admin,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    users.append(user)
    _save_raw(users)
    return _public(user)


def update_user(user_id: str, **fields) -> dict:
    users = _load_raw()
    for u in users:
        if u["id"] == user_id:
            if "password" in fields:
                u["hashed_password"] = hash_password(fields.pop("password"))
            u.update(fields)
            _save_raw(users)
            return _public(u)
    raise KeyError(f"User {user_id} not found")


def delete_user(user_id: str) -> bool:
    users = _load_raw()
    filtered = [u for u in users if u["id"] != user_id]
    if len(filtered) == len(users):
        return False
    _save_raw(filtered)
    return True


def _public(u: dict) -> dict:
    """Return user dict without hashed_password."""
    return {k: v for k, v in u.items() if k != "hashed_password"}


def get_user_by_email(email: str) -> dict | None:
    return next(
        (u for u in _load_raw() if (u.get("email") or "").lower() == email.lower()),
        None,
    )


_cf_user_lock = asyncio.Lock()
_USERNAME_SAFE_RE = re.compile(r"[^a-z0-9._-]+")
_USERNAME_MAX_LEN = 32


def _safe_username_from_email(email: str) -> str:
    base = _USERNAME_SAFE_RE.sub("", email.split("@", 1)[0].lower())[:_USERNAME_MAX_LEN]
    return base or "user"


async def get_or_create_cf_user(email: str, is_admin: bool = False) -> dict:
    """Look up a user by email, or auto-provision one for Cloudflare SSO logins.

    Async-locked to prevent TOCTOU duplicate-creation when concurrent requests
    arrive for the same brand-new email.
    """
    async with _cf_user_lock:
        users = _load_raw()
        email_lower = email.lower()
        for u in users:
            if (u.get("email") or "").lower() == email_lower:
                if u.get("is_admin") != is_admin:
                    u["is_admin"] = is_admin
                    _save_raw(users)
                return _public(u)
        base = _safe_username_from_email(email)
        taken = {u["username"].lower() for u in users}
        username = base
        i = 2
        while username in taken and i < 10_000:
            username = f"{base}{i}"
            i += 1
        if username in taken:
            username = f"{base}-{uuid.uuid4().hex[:8]}"
        user = {
            "id": str(uuid.uuid4()),
            "username": username,
            "hashed_password": hash_password(secrets.token_hex(32)),
            "is_admin": is_admin,
            "email": email,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        users.append(user)
        _save_raw(users)
        log.info("auto-provisioned CF user username=%s email=%s admin=%s", username, email, is_admin)
        return _public(user)


# ---------- bootstrap ----------

def _bootstrap() -> None:
    """Create default admin account if no users exist.

    Skipped when Cloudflare SSO is configured — in that mode admins are designated
    via CF_ADMIN_EMAILS and a default admin/admin would be a trivial bypass for
    anyone able to reach /auth/login.
    """
    if USERS_PATH.exists() and _load_raw():
        return
    if os.environ.get("CF_TEAM_DOMAIN"):
        log.warning(
            "No users found. Cloudflare SSO is configured — skipping default "
            "admin/admin bootstrap. The first user listed in CF_ADMIN_EMAILS "
            "will be auto-provisioned as admin on their first login."
        )
        return
    log.warning(
        "╔══════════════════════════════════════════════════╗\n"
        "║  No users found — creating default admin account ║\n"
        "║  Username: admin   Password: admin               ║\n"
        "║  CHANGE THIS IMMEDIATELY at /settings            ║\n"
        "╚══════════════════════════════════════════════════╝"
    )
    create_user("admin", "admin", is_admin=True)


_bootstrap()
