"""SpondBot web UI — auto-accept Spond event invites the moment they open."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiohttp
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from spond import AuthenticationError, spond
from webui.auth import (
    COOKIE_KWARGS,
    create_access_token,
    get_admin_user,
    get_current_user,
)
from webui.users import (
    create_user,
    delete_user,
    get_user_by_username,
    load_users,
    update_user,
    verify_password,
)

VERSION = "1.0.0"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("spondbot")

# Suppress uvicorn noise from TLS handshakes hitting the plain-HTTP port
# (phones/browsers sending HTTPS to an HTTP endpoint — harmless but spammy).
class _SuppressTLSNoise(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "Invalid HTTP request received" not in record.getMessage()

logging.getLogger("uvicorn.error").addFilter(_SuppressTLSNoise())

DATA_DIR = Path(os.environ.get("SPONDBOT_DATA", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
KEY_PATH = DATA_DIR / ".key"

STATIC_DIR = Path(__file__).parent / "static"

POLL_INTERVAL = int(os.environ.get("SPONDBOT_POLL_INTERVAL", "900"))
POLL_INTERVAL_NEAR_FIRE = int(os.environ.get("SPONDBOT_POLL_INTERVAL_NEAR_FIRE", "120"))
NEAR_FIRE_WINDOW = 300
MANUAL_REFRESH_MIN_INTERVAL = int(os.environ.get("SPONDBOT_MANUAL_REFRESH_MIN", "30"))

DEFAULT_SETTINGS = {
    "initial_delay": 0.3,
    "retry_count": 10,
    "retry_interval": 0.3,
    "response": "accepted",
}


# ---------- password-at-rest encryption ----------

def _load_or_create_key() -> bytes:
    env = os.environ.get("SPONDBOT_SECRET")
    if env:
        import hashlib
        raw = hashlib.sha256(env.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(raw)
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass
    return key


_fernet = Fernet(_load_or_create_key())


def encrypt_password(plain: str) -> str:
    if not plain:
        return ""
    return "enc:" + _fernet.encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_password(stored: str) -> str:
    if not stored:
        return ""
    if not stored.startswith("enc:"):
        return stored
    try:
        return _fernet.decrypt(stored[4:].encode("ascii")).decode("utf-8")
    except InvalidToken:
        log.error("stored password could not be decrypted — key mismatch")
        return ""


# ---------- per-user data paths ----------

def user_data_dir(user_id: str) -> Path:
    base = (DATA_DIR / "users").resolve()
    d = (base / user_id).resolve()
    if not d.is_relative_to(base):
        raise HTTPException(status_code=400, detail="Invalid user id")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _config_path(user_id: str) -> Path:
    return user_data_dir(user_id) / "config.json"


def _history_path(user_id: str) -> Path:
    return user_data_dir(user_id) / "history.jsonl"


def _accepted_path(user_id: str) -> Path:
    return user_data_dir(user_id) / "accepted_ids.json"


def _payment_path(user_id: str) -> Path:
    return user_data_dir(user_id) / "payment_required_ids.json"


def _failed_path(user_id: str) -> Path:
    return user_data_dir(user_id) / "failed_ids.json"


# ---------- config persistence ----------

_config_cache: dict[str, dict[str, Any]] = {}  # user_id → config


def load_config(user_id: str) -> dict[str, Any]:
    if user_id in _config_cache:
        return dict(_config_cache[user_id])
    path = _config_path(user_id)
    cfg = json.loads(path.read_text()) if path.exists() else {}
    cfg.setdefault("username", "")
    cfg["password"] = decrypt_password(cfg.get("password", ""))
    cfg.setdefault("group_ids", [])
    cfg.setdefault("selected_event_ids", [])
    cfg.setdefault("defaults", dict(DEFAULT_SETTINGS))
    cfg.setdefault("event_settings", {})
    cfg.setdefault("dry_run", False)
    cfg.setdefault("group_by", "heading")
    _config_cache[user_id] = dict(cfg)
    return cfg


def save_config(cfg: dict[str, Any], user_id: str) -> None:
    stored = dict(cfg)
    stored["password"] = encrypt_password(cfg.get("password", ""))
    path = _config_path(user_id)
    path.write_text(json.dumps(stored, indent=2))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    _config_cache[user_id] = dict(cfg)


def settings_for(cfg: dict[str, Any], event_id: str) -> dict[str, Any]:
    merged = dict(DEFAULT_SETTINGS)
    merged.update(cfg.get("defaults") or {})
    merged.update((cfg.get("event_settings") or {}).get(event_id) or {})
    return merged


# ---------- history ----------

def append_history(entry: dict[str, Any], user_id: str) -> None:
    entry = {"ts": time.time(), **entry}
    with _history_path(user_id).open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _parse_history_lines(lines: list[str]) -> list[dict[str, Any]]:
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def read_history(
    user_id: str, limit: int = 100, event_id: str | None = None
) -> list[dict[str, Any]]:
    path = _history_path(user_id)
    if not path.exists():
        return []
    entries = _parse_history_lines(path.read_text().splitlines())
    if event_id is not None:
        entries = [e for e in entries if e.get("event_id") == event_id]
    entries = entries[-limit:]
    return list(reversed(entries))


def clear_history(user_id: str, event_id: str | None = None) -> int:
    path = _history_path(user_id)
    if not path.exists():
        return 0
    if event_id is None:
        raw = path.read_text()
        path.write_text("")
        return sum(1 for ln in raw.splitlines() if ln.strip())
    entries = _parse_history_lines(path.read_text().splitlines())
    kept = [e for e in entries if e.get("event_id") != event_id]
    removed = len(entries) - len(kept)
    path.write_text("\n".join(json.dumps(e) for e in kept) + ("\n" if kept else ""))
    return removed


# ---------- persistent sets ----------

def _load_id_set(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text()))
    except Exception:
        return set()


def _save_id_set(path: Path, ids: set[str]) -> None:
    try:
        path.write_text(json.dumps(sorted(ids)))
    except Exception as exc:
        log.warning("could not save %s: %s", path.name, exc)


# ---------- scheduler ----------

class Scheduler:
    def __init__(self, user_id: str) -> None:
        self._user_id = user_id
        self._task: asyncio.Task | None = None
        self._scheduled: dict[str, asyncio.Task] = {}
        self._scheduled_fire_ts: dict[str, float] = {}
        self._accepted: set[str] = _load_id_set(_accepted_path(user_id))
        self._waitlisted: set[str] = set()
        self._permanently_failed: set[str] = _load_id_set(_failed_path(user_id))
        self._payment_required: set[str] = _load_id_set(_payment_path(user_id))
        self._events_cache: list[dict] = []
        self._client: spond.Spond | None = None
        self._client_key: tuple[str, str] | None = None
        self._client_lock = asyncio.Lock()
        self._id_set_lock = asyncio.Lock()
        self._last_error: str | None = None
        self._last_tick_ts: float | None = None
        self._last_manual_refresh_ts: float = 0.0
        self._cached_user_id: str | None = None
        self._cached_member_ids: dict[str, str] = {}
        self._wake_event = asyncio.Event()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def last_tick_ts(self) -> float | None:
        return self._last_tick_ts

    def status(self) -> dict[str, Any]:
        cfg = load_config(self._user_id)
        now = time.time()
        pending = [
            (eid, ts) for eid, ts in self._scheduled_fire_ts.items()
            if eid not in self._accepted and ts >= now
        ]
        pending.sort(key=lambda p: p[1])
        event_headings = {e.get("id"): e.get("heading", e.get("id")) for e in self._events_cache}
        pending_events = [
            {"event_id": eid, "heading": event_headings.get(eid, eid), "fire_ts": ts}
            for eid, ts in pending[:8]
        ]
        next_id, next_ts = (pending[0] if pending else (None, None))
        next_heading = pending_events[0]["heading"] if pending_events else None
        return {
            "user_id": self._user_id,
            "last_tick_ts": self._last_tick_ts,
            "last_error": self._last_error,
            "events_cached": len(self._events_cache),
            "scheduled_count": len(pending),
            "next_fire_ts": next_ts,
            "next_event_heading": next_heading,
            "pending_events": pending_events,
            "accepted_count": len(self._accepted),
            "dry_run": bool(cfg.get("dry_run")),
            "logged_in": self._client is not None,
            "poll_interval": self._current_poll_interval(),
            "version": VERSION,
            "failed_count": len(self._permanently_failed),
        }

    def _current_poll_interval(self) -> int:
        now = time.time()
        for ts in self._scheduled_fire_ts.values():
            if 0 < ts - now <= NEAR_FIRE_WINDOW:
                return POLL_INTERVAL_NEAR_FIRE
        return POLL_INTERVAL

    def wake(self) -> None:
        self._wake_event.set()

    async def get_client(self, username: str, password: str) -> spond.Spond:
        async with self._client_lock:
            key = (username, password)
            if self._client is None or self._client_key != key:
                if self._client is not None:
                    try:
                        await self._client.clientsession.close()
                    except Exception:
                        pass
                self._client = spond.Spond(username=username, password=password)
                self._client_key = key
            return self._client

    async def reset_client(self) -> None:
        async with self._client_lock:
            if self._client is not None:
                try:
                    await self._client.clientsession.close()
                except Exception:
                    pass
            self._client = None
            self._client_key = None
            self._cached_user_id = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        for t in self._scheduled.values():
            t.cancel()
        self._scheduled.clear()
        self._scheduled_fire_ts.clear()
        await self.reset_client()

    @property
    def events(self) -> list[dict]:
        return self._events_cache

    @property
    def accepted(self) -> set[str]:
        return self._accepted

    @property
    def waitlisted(self) -> set[str]:
        return self._waitlisted

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler tick failed (user=%s)", self._user_id)
            interval = self._current_poll_interval()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            self._wake_event.clear()

    async def manual_refresh(self) -> None:
        now = time.time()
        if now - self._last_manual_refresh_ts < MANUAL_REFRESH_MIN_INTERVAL:
            remaining = int(MANUAL_REFRESH_MIN_INTERVAL - (now - self._last_manual_refresh_ts))
            raise HTTPException(
                429,
                f"Refresh throttled — try again in {remaining}s. "
                "(Spond rate-limits aggressively; the bot polls automatically.)",
            )
        self._last_manual_refresh_ts = now
        await self._tick()
        if self._last_error:
            raise HTTPException(502, self._last_error)

    async def _tick(self) -> None:
        cfg = load_config(self._user_id)
        if not cfg.get("username") or not cfg.get("password"):
            return
        group_ids = cfg.get("group_ids") or []
        selected = set(cfg.get("selected_event_ids") or [])

        try:
            s = await self.get_client(cfg["username"], cfg["password"])
            sources = group_ids if group_ids else [None]

            async def _fetch_group(gid: str | None) -> list[dict]:
                return await s.get_events(
                    group_id=gid, include_scheduled=True, max_events=200
                ) or []

            results = await asyncio.gather(
                *[_fetch_group(gid) for gid in sources],
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, Exception):
                    raise r

            all_events: list[dict] = []
            seen_ids: set[str] = set()
            for evs in results:
                for e in evs:  # type: ignore[union-attr]
                    eid = e.get("id")
                    if eid and eid not in seen_ids:
                        seen_ids.add(eid)
                        all_events.append(e)
            self._events_cache = all_events
            self._last_error = None
            self._last_tick_ts = time.time()
        except aiohttp.ContentTypeError as exc:
            self._last_error = (
                f"Spond returned non-JSON ({exc.status}) — likely rate-limited. Backing off."
            )
            log.warning(self._last_error)
            await self.reset_client()
            return
        except AuthenticationError as exc:
            self._last_error = f"Spond auth failed: {exc}"
            log.error(self._last_error)
            await self.reset_client()
            return
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            log.exception("tick failure (user=%s)", self._user_id)
            return

        if self._cached_user_id is None:
            try:
                profile = await s.get_profile()
                uid = (profile.get("profile") or {}).get("id") or profile.get("id")
                if uid:
                    self._cached_user_id = uid
                else:
                    log.warning("could not resolve profile id (user=%s)", self._user_id)
            except Exception as exc:
                log.warning("profile pre-fetch failed (user=%s): %s", self._user_id, exc)

        profile_id = self._cached_user_id
        if profile_id:
            try:
                groups = await s.get_groups() or []
                for g in groups:
                    gid = g.get("id")
                    if gid and gid not in self._cached_member_ids:
                        for m in g.get("members", []):
                            mpid = (m.get("profile") or {}).get("id")
                            if mpid == profile_id:
                                self._cached_member_ids[gid] = m["id"]
                                log.info(
                                    "resolved member_id=%s in group %s (%s) for user=%s",
                                    m["id"], g.get("name", gid), gid, self._user_id,
                                )
                                break
                        else:
                            log.warning(
                                "profile_id=%s not found as member in group %s (%s) — "
                                "will fall back to profile ID (user=%s)",
                                profile_id, g.get("name", gid), gid, self._user_id,
                            )
            except Exception as exc:
                log.warning("group member ID pre-fetch failed (user=%s): %s", self._user_id, exc)

        now = time.time()
        for e in all_events:
            eid = e["id"]
            if eid not in selected:
                continue

            group_id = ((e.get("recipients") or {}).get("group") or {}).get("id")
            uid = self._cached_member_ids.get(group_id) if group_id else None
            uid = uid or profile_id

            responses = e.get("responses") or {}
            if uid and uid in (responses.get("acceptedIds") or []):
                if eid not in self._accepted:
                    log.info("sync: marking %s (%s) as accepted from Spond", eid, e.get("heading"))
                self._accepted.add(eid)
            if uid and uid in (responses.get("waitinglistIds") or []):
                if eid not in self._waitlisted:
                    log.info("sync: marking %s (%s) as waitlisted from Spond", eid, e.get("heading"))
                self._waitlisted.add(eid)

            if _is_payment_required(e):
                if eid not in self._payment_required:
                    log.warning(
                        "event %s (%s) requires payment — cannot auto-accept, "
                        "accept manually in the Spond app",
                        eid, e.get("heading"),
                    )
                    self._payment_required.add(eid)
                    _save_id_set(_payment_path(self._user_id), self._payment_required)
                continue

            if eid in self._accepted or eid in self._waitlisted or eid in self._permanently_failed:
                continue
            if eid in self._scheduled and not self._scheduled[eid].done():
                continue
            per = settings_for(cfg, eid)
            invite_ts = _available_epoch(e)
            fire_ts = invite_ts + float(per["initial_delay"])
            delay = max(0.0, fire_ts - now)
            log.info(
                "arming auto-%s for %s in %.1fs (%s) using member_id=%s group=%s%s (user=%s)",
                per["response"], eid, delay, e.get("heading"), uid, group_id,
                " [dry-run]" if cfg.get("dry_run") else "", self._user_id,
            )
            self._scheduled_fire_ts[eid] = fire_ts
            self._scheduled[eid] = asyncio.create_task(
                self._accept_later(
                    cfg["username"], cfg["password"], eid, delay, per,
                    e.get("heading", ""), bool(cfg.get("dry_run")),
                    uid, e.get("startTimestamp"),
                )
            )
        _save_id_set(_accepted_path(self._user_id), self._accepted)

    async def _tick_one(self, e: dict, cfg: dict) -> None:
        eid = e["id"]
        selected = set(cfg.get("selected_event_ids") or [])
        if eid not in selected:
            return
        group_id = ((e.get("recipients") or {}).get("group") or {}).get("id")
        uid = self._cached_member_ids.get(group_id) if group_id else None
        uid = uid or self._cached_user_id
        responses = e.get("responses") or {}
        if uid and uid in (responses.get("acceptedIds") or []):
            log.info("refresh: %s (%s) already accepted on Spond", eid, e.get("heading"))
            self._accepted.add(eid)
        if uid and uid in (responses.get("waitinglistIds") or []):
            log.info("refresh: %s (%s) already waitlisted on Spond", eid, e.get("heading"))
            self._waitlisted.add(eid)
        if eid in self._accepted or eid in self._waitlisted:
            return
        self._permanently_failed.discard(eid)
        existing = self._scheduled.get(eid)
        if existing and not existing.done():
            existing.cancel()
        per = settings_for(cfg, eid)
        invite_ts = _available_epoch(e)
        fire_ts = invite_ts + float(per["initial_delay"])
        delay = max(0.0, fire_ts - time.time())
        log.info(
            "refresh: rearming auto-%s for %s in %.1fs (%s) using member_id=%s (user=%s)",
            per["response"], eid, delay, e.get("heading"), uid, self._user_id,
        )
        self._scheduled_fire_ts[eid] = fire_ts
        self._scheduled[eid] = asyncio.create_task(
            self._accept_later(
                cfg["username"], cfg["password"], eid, delay, per,
                e.get("heading", ""), bool(cfg.get("dry_run")),
                uid, e.get("startTimestamp"),
            )
        )

    async def _accept_later(
        self,
        username: str,
        password: str,
        event_id: str,
        delay: float,
        per: dict[str, Any],
        heading: str,
        dry_run: bool,
        user_id: str | None = None,
        start_ts: str | None = None,
    ) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        response_key = per.get("response", "accepted")
        _base = {"event_id": event_id, "heading": heading, "startTimestamp": start_ts}

        if dry_run:
            log.info("[dry-run] would %s event %s (%s)", response_key, event_id, heading)
            self._accepted.add(event_id)
            _save_id_set(_accepted_path(self._user_id), self._accepted)
            append_history({**_base, "response": response_key, "attempt": 0,
                            "ok": True, "dry_run": True}, self._user_id)
            return

        s = await self.get_client(username, password)

        if not user_id:
            try:
                profile = await s.get_profile()
                user_id = (profile.get("profile") or {}).get("id") or profile.get("id")
            except Exception as exc:
                log.error("could not fetch profile for accept: %s", exc)
                append_history({**_base, "ok": False, "error": f"profile fetch: {exc}"}, self._user_id)
                return

        if not user_id:
            log.error("could not resolve user/member ID for event %s (%s)", event_id, heading)
            append_history({**_base, "ok": False, "error": "no user/member id"}, self._user_id)
            return

        payload = {response_key: "true"}
        retries = int(per["retry_count"])
        interval = float(per["retry_interval"])
        log.info(
            "responding to %s (%s) as user_id=%s retries=%d interval=%.1fs",
            event_id, heading, user_id, retries, interval,
        )
        consecutive_404s = 0
        for attempt in range(1, retries + 2):
            try:
                result = await s.change_response(event_id, user_id, payload)
                if isinstance(result, dict) and "errorCode" not in result:
                    waitlisted = user_id in result.get("waitinglistIds", [])
                    if waitlisted:
                        log.info(
                            "waitlisted for %s (%s) on attempt %d — waitlist size: %d",
                            event_id, heading, attempt, len(result.get("waitinglistIds", [])),
                        )
                        self._waitlisted.add(event_id)
                        self._accepted.discard(event_id)
                    else:
                        log.info(
                            "%s %s (%s) on attempt %d — accepted count: %d",
                            response_key, event_id, heading, attempt, len(result.get("acceptedIds", [])),
                        )
                        self._accepted.add(event_id)
                        self._waitlisted.discard(event_id)
                    async with self._id_set_lock:
                        _save_id_set(_accepted_path(self._user_id), self._accepted)
                    append_history({**_base, "response": response_key, "attempt": attempt,
                                    "ok": True, "waitlisted": waitlisted}, self._user_id)
                    return
                error_code = result.get("errorCode")
                result_str = str(result).lower()
                is_payment_err = (
                    error_code == 402
                    or "payment" in result_str
                    or "fee" in result_str
                    or "registrationfee" in result_str
                )
                if is_payment_err:
                    log.error(
                        "event %s (%s) requires payment (response: %s) — "
                        "cannot auto-accept, accept manually in the Spond app",
                        event_id, heading, result,
                    )
                    self._payment_required.add(event_id)
                    async with self._id_set_lock:
                        _save_id_set(_payment_path(self._user_id), self._payment_required)
                    append_history({**_base, "response": response_key, "ok": False,
                                    "error": "payment required — accept manually in Spond"}, self._user_id)
                    return
                if error_code == 404:
                    consecutive_404s += 1
                    log.warning(
                        "attempt %d for %s: error response (full body): %s — %s",
                        attempt, event_id, result,
                        "retrying" if attempt < retries + 1 else "giving up",
                    )
                else:
                    consecutive_404s = 0
                    log.warning(
                        "attempt %d for %s: error response (full body): %s",
                        attempt, event_id, result,
                    )
            except Exception as exc:
                consecutive_404s = 0
                log.warning("attempt %d for %s failed: %s", attempt, event_id, exc)
            if attempt < retries + 1:
                await asyncio.sleep(interval)
        total = retries + 1
        log.error("gave up on event %s after %d attempts", event_id, total)
        self._permanently_failed.add(event_id)
        async with self._id_set_lock:
            _save_id_set(_failed_path(self._user_id), self._permanently_failed)
        error_msg = (
            "not invited to this occurrence"
            if consecutive_404s == total
            else f"gave up after {total} attempts"
        )
        append_history({**_base, "response": response_key, "ok": False, "error": error_msg}, self._user_id)


def _extract_location(event: dict) -> str | None:
    loc = event.get("location") or {}
    if not isinstance(loc, dict):
        loc = {}
    feature = loc.get("feature") or {}
    if not isinstance(feature, dict):
        feature = {}
    return (
        (feature.get("properties") or {}).get("name")
        or loc.get("address")
        or loc.get("name")
    )


def _is_payment_required(event: dict) -> bool:
    """Return True if this event requires upfront payment to register."""
    if event.get("requiresPayment"):
        return True
    reg = event.get("registrationInfo") or {}
    if reg.get("requiresPayment") or reg.get("paymentInfo") or reg.get("price"):
        return True
    return False


def _available_epoch(event: dict) -> float:
    import datetime as dt
    for key in ("inviteTime", "invitedTimestamp", "startTimestamp"):
        val = event.get(key)
        if val:
            try:
                return dt.datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()
            except (ValueError, AttributeError):
                continue
    return time.time()


# ---------- scheduler manager ----------

class SchedulerManager:
    def __init__(self) -> None:
        self._schedulers: dict[str, Scheduler] = {}

    async def get(self, user_id: str) -> Scheduler:
        if user_id not in self._schedulers:
            s = Scheduler(user_id)
            self._schedulers[user_id] = s
            await s.start()
        return self._schedulers[user_id]

    async def remove(self, user_id: str) -> None:
        s = self._schedulers.pop(user_id, None)
        if s:
            await s.stop()

    async def stop_all(self) -> None:
        for s in list(self._schedulers.values()):
            await s.stop()
        self._schedulers.clear()

    def all_status(self) -> list[dict[str, Any]]:
        return [s.status() for s in self._schedulers.values()]


manager = SchedulerManager()


# ---------- FastAPI ----------

limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    from webui.auth import DEBUG as _AUTH_DEBUG
    if _AUTH_DEBUG:
        log.warning(
            "DEBUG=1: session cookie Secure flag is OFF — suitable for HTTP/LAN only. "
            "Remove DEBUG=1 when serving over HTTPS."
        )
    else:
        log.info(
            "Session cookie Secure flag is ON — login requires HTTPS. "
            "Set DEBUG=1 if accessing over plain HTTP."
        )
    users = load_users()
    log.info("Starting up with %d user(s) registered", len(users))
    for user in users:
        cfg = load_config(user["id"])
        if cfg.get("username") and cfg.get("password"):
            log.info("Auto-starting scheduler for user=%s", user["username"])
            await manager.get(user["id"])
    yield
    await manager.stop_all()


app = FastAPI(title="SpondBot", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------- auth routes ----------

class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
@limiter.limit("5/minute")
async def auth_login(request: Request, response: Response, body: LoginBody) -> dict:
    from webui.auth import DEBUG as _AUTH_DEBUG
    user = get_user_by_username(body.username)
    ok = verify_password(body.password, user["hashed_password"] if user else None)
    ip = request.client.host if request.client else "unknown"
    if not ok:
        log.warning("login failed for username=%r from %s", body.username, ip)
        raise HTTPException(401, "Invalid username or password")
    log.info(
        "login ok: username=%s id=%s admin=%s from %s (secure_cookie=%s)",
        user["username"], user["id"], user["is_admin"], ip, not _AUTH_DEBUG,
    )
    token = create_access_token(user["id"], user["username"], user["is_admin"])
    response.set_cookie("sb_session", token, **COOKIE_KWARGS)
    return {"username": user["username"], "is_admin": user["is_admin"]}


@app.post("/auth/logout")
async def auth_logout(response: Response) -> dict:
    response.delete_cookie("sb_session", path="/")
    return {"status": "ok"}


@app.get("/auth/me")
async def auth_me(user: dict = Depends(get_current_user)) -> dict:
    return user


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


@app.patch("/auth/me/password")
async def auth_change_password(
    body: ChangePasswordBody, user: dict = Depends(get_current_user)
) -> dict:
    from webui.users import get_user_by_id
    full = get_user_by_id(user["id"])
    if not full or not verify_password(body.current_password, full.get("hashed_password")):
        raise HTTPException(400, "Current password is incorrect")
    update_user(user["id"], password=body.new_password)
    return {"status": "ok"}


# ---------- admin routes ----------

class CreateUserBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=8)
    is_admin: bool = False


class UpdateUserBody(BaseModel):
    is_admin: bool | None = None
    password: str | None = Field(default=None, min_length=8)


@app.get("/admin/users")
async def admin_list_users(_: dict = Depends(get_admin_user)) -> list:
    return load_users()


@app.post("/admin/users")
async def admin_create_user(
    body: CreateUserBody, _: dict = Depends(get_admin_user)
) -> dict:
    try:
        return create_user(body.username, body.password, body.is_admin)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.patch("/admin/users/{uid}")
async def admin_update_user(
    uid: str, body: UpdateUserBody, admin: dict = Depends(get_admin_user)
) -> dict:
    fields: dict[str, Any] = {}
    if body.is_admin is not None:
        fields["is_admin"] = body.is_admin
    if body.password is not None:
        fields["password"] = body.password
    if not fields:
        raise HTTPException(400, "Nothing to update")
    try:
        return update_user(uid, **fields)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/admin/users/{uid}")
async def admin_delete_user(
    uid: str, admin: dict = Depends(get_admin_user)
) -> dict:
    if uid == admin["id"]:
        raise HTTPException(400, "Cannot delete your own account")
    if not delete_user(uid):
        raise HTTPException(404, f"User {uid} not found")
    await manager.remove(uid)
    return {"status": "ok"}


@app.get("/admin/activity")
async def admin_activity(
    limit: int = Query(default=200, ge=1, le=1000), _: dict = Depends(get_admin_user)
) -> dict:
    users = {u["id"]: u["username"] for u in load_users()}
    all_entries: list[dict] = []
    for uid, username in users.items():
        for entry in read_history(uid, limit=limit):
            all_entries.append({**entry, "username": username})
    all_entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return {"entries": all_entries[:limit]}


@app.get("/admin/status")
async def admin_status(_: dict = Depends(get_admin_user)) -> list:
    uid_to_name = {u["id"]: u["username"] for u in load_users()}
    active = {s["user_id"]: {**s, "username": uid_to_name.get(s["user_id"], s["user_id"])}
              for s in manager.all_status()}
    # Include registered users who have no active scheduler (no Spond creds configured yet)
    for uid, username in uid_to_name.items():
        if uid not in active:
            active[uid] = {
                "user_id": uid, "username": username,
                "last_tick_ts": None, "last_error": None,
                "events_cached": 0, "scheduled_count": 0,
                "next_fire_ts": None, "next_event_heading": None,
                "pending_events": [],
                "accepted_count": 0, "dry_run": False,
                "logged_in": False, "poll_interval": None,
                "version": VERSION, "failed_count": 0,
            }
    return list(active.values())


# ---------- existing API routes (now user-scoped) ----------

class Credentials(BaseModel):
    username: str
    password: str
    group_ids: list[str] = []


class Selection(BaseModel):
    event_ids: list[str]


class Settings(BaseModel):
    initial_delay: float = Field(ge=0, le=60)
    retry_count: int = Field(ge=0, le=100)
    retry_interval: float = Field(ge=0.05, le=60)
    response: str = Field(pattern="^(accepted|declined|unconfirmed)$")
    dry_run: bool = False
    group_by: str = Field(default="heading", pattern="^(heading|day|week|year)$")


class EventSettings(BaseModel):
    initial_delay: float | None = Field(default=None, ge=0, le=60)
    retry_count: int | None = Field(default=None, ge=0, le=100)
    retry_interval: float | None = Field(default=None, ge=0.05, le=60)
    response: str | None = Field(default=None, pattern="^(accepted|declined|unconfirmed)$")


@app.get("/api/config")
async def api_get_config(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    cfg = load_config(user["id"])
    return {
        "username": cfg.get("username", ""),
        "has_password": bool(cfg.get("password")),
        "group_ids": cfg.get("group_ids", []),
        "selected_event_ids": cfg.get("selected_event_ids", []),
        "dry_run": bool(cfg.get("dry_run")),
        "group_by": cfg.get("group_by", "heading"),
    }


@app.post("/api/config")
async def api_save_config(
    body: Credentials, user: dict = Depends(get_current_user)
) -> dict[str, str]:
    uid = user["id"]
    cfg = load_config(uid)
    creds_changed = body.username != cfg.get("username") or body.password != cfg.get("password")
    cfg["username"] = body.username
    cfg["password"] = body.password
    cfg["group_ids"] = [g.strip() for g in body.group_ids if g.strip()]
    save_config(cfg, uid)

    sch = await manager.get(uid)
    if creds_changed:
        await sch.reset_client()
        try:
            s = await sch.get_client(body.username, body.password)
            await s.login()
        except AuthenticationError as exc:
            raise HTTPException(401, f"Spond login failed: {exc}") from exc
        except aiohttp.ContentTypeError as exc:
            raise HTTPException(
                429 if exc.status == 429 else 502,
                "Spond rate-limited the login. Wait a few minutes and try again.",
            ) from exc

    await sch.start()
    sch.wake()
    return {"status": "ok"}


@app.get("/api/events")
async def api_events(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    uid = user["id"]
    cfg = load_config(uid)
    sch = await manager.get(uid)
    selected = set(cfg.get("selected_event_ids") or [])
    event_settings = cfg.get("event_settings") or {}
    spond_uid = sch._cached_user_id
    events = []
    for e in sch.events:
        gid = (e.get("group") or {}).get("id") or e.get("groupId")
        responses = e.get("responses") or {}
        in_accepted = spond_uid and spond_uid in (responses.get("acceptedIds") or [])
        in_waitlist = spond_uid and spond_uid in (responses.get("waitinglistIds") or [])
        accepted = bool(in_accepted) or e["id"] in sch.accepted
        waitlisted = bool(in_waitlist) or e["id"] in sch.waitlisted
        loc_name = _extract_location(e)
        events.append({
            "id": e["id"],
            "heading": e.get("heading"),
            "groupId": gid,
            "groupName": (e.get("group") or {}).get("name"),
            "startTimestamp": e.get("startTimestamp"),
            "endTimestamp": e.get("endTimestamp"),
            "inviteTime": e.get("inviteTime") or e.get("invitedTimestamp"),
            "selected": e["id"] in selected,
            "accepted": accepted and not waitlisted,
            "waitlisted": waitlisted,
            "failed": e["id"] in sch._permanently_failed,
            "paymentRequired": e["id"] in sch._payment_required or _is_payment_required(e),
            "hasOverride": e["id"] in event_settings,
            "armed_ts": sch._scheduled_fire_ts.get(e["id"]),
            "location": loc_name,
            "acceptedCount": len(responses.get("acceptedIds") or []),
            "declinedCount": len(responses.get("declinedIds") or []),
            "waitinglistCount": len(responses.get("waitinglistIds") or []),
            "unansweredCount": len(responses.get("unansweredIds") or []),
            "maxAccepted": e.get("maxAccepted"),
            "isFull": bool(e.get("responses", {}).get("waitinglistIds")),
        })
    return {
        "events": events,
        "defaults": cfg.get("defaults") or DEFAULT_SETTINGS,
        "group_by": cfg.get("group_by", "heading"),
    }


@app.post("/api/events/{event_id}/accept")
async def api_accept_now(
    event_id: str, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    uid = user["id"]
    cfg = load_config(uid)
    if not cfg.get("username") or not cfg.get("password"):
        raise HTTPException(400, "No credentials configured")
    sch = await manager.get(uid)
    event = next((e for e in sch.events if e["id"] == event_id), None)
    if not event:
        raise HTTPException(404, "Event not found in cache — try refreshing")
    if event_id in sch._permanently_failed:
        sch._permanently_failed.discard(event_id)
        async with sch._id_set_lock:
            _save_id_set(_failed_path(uid), sch._permanently_failed)
    per = settings_for(cfg, event_id)
    group_id = ((event.get("recipients") or {}).get("group") or {}).get("id")
    member_id = sch._cached_member_ids.get(group_id) if group_id else None
    member_id = member_id or sch._cached_user_id
    existing = sch._scheduled.get(event_id)
    if existing and not existing.done():
        existing.cancel()
    sch._scheduled[event_id] = asyncio.create_task(
        sch._accept_later(
            cfg["username"], cfg["password"], event_id, 0.0, per,
            event.get("heading", ""), bool(cfg.get("dry_run")),
            member_id, event.get("startTimestamp"),
        )
    )
    return {"status": "fired", "event_id": event_id, "user_id": member_id}


@app.post("/api/events/{event_id}/decline")
async def api_decline_now(
    event_id: str, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    uid = user["id"]
    cfg = load_config(uid)
    if not cfg.get("username") or not cfg.get("password"):
        raise HTTPException(400, "No credentials configured")
    sch = await manager.get(uid)
    event = next((e for e in sch.events if e["id"] == event_id), None)
    if not event:
        raise HTTPException(404, "Event not found in cache — try refreshing")
    per = {**settings_for(cfg, event_id), "response": "declined", "retry_count": 0}
    group_id = ((event.get("recipients") or {}).get("group") or {}).get("id")
    member_id = sch._cached_member_ids.get(group_id) if group_id else None
    member_id = member_id or sch._cached_user_id
    existing = sch._scheduled.get(event_id)
    if existing and not existing.done():
        existing.cancel()
    sch._scheduled[event_id] = asyncio.create_task(
        sch._accept_later(
            cfg["username"], cfg["password"], event_id, 0.0, per,
            event.get("heading", ""), bool(cfg.get("dry_run")),
            member_id, event.get("startTimestamp"),
        )
    )
    return {"status": "fired", "event_id": event_id, "user_id": member_id}


@app.post("/api/events/{event_id}/refresh")
async def api_refresh_event(
    event_id: str, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    uid = user["id"]
    cfg = load_config(uid)
    if not cfg.get("username") or not cfg.get("password"):
        raise HTTPException(400, "No credentials configured")
    sch = await manager.get(uid)
    cached = next((e for e in sch.events if e["id"] == event_id), None)
    if not cached:
        raise HTTPException(404, "Event not found in cache")
    group_id = ((cached.get("recipients") or {}).get("group") or {}).get("id")
    try:
        s = await sch.get_client(cfg["username"], cfg["password"])
        fresh_events = await s.get_events(group_id=group_id, include_scheduled=True, max_events=200) or []
    except Exception as exc:
        raise HTTPException(502, f"Spond fetch failed: {exc}") from exc
    fresh = next((e for e in fresh_events if e["id"] == event_id), None)
    if not fresh:
        raise HTTPException(404, "Event not returned by Spond — it may have been removed")
    for i, e in enumerate(sch._events_cache):
        if e["id"] == event_id:
            sch._events_cache[i] = fresh
            break
    await sch._tick_one(fresh, cfg)
    return {"status": "refreshed", "event_id": event_id, "heading": fresh.get("heading")}


@app.post("/api/selection")
async def api_selection(
    body: Selection, user: dict = Depends(get_current_user)
) -> dict[str, str]:
    uid = user["id"]
    cfg = load_config(uid)
    cfg["selected_event_ids"] = list(dict.fromkeys(body.event_ids))
    save_config(cfg, uid)
    return {"status": "ok"}


@app.post("/api/refresh")
async def api_refresh(user: dict = Depends(get_current_user)) -> dict[str, str]:
    sch = await manager.get(user["id"])
    await sch.manual_refresh()
    return {"status": "ok"}


@app.get("/api/status")
async def api_status(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    sch = await manager.get(user["id"])
    return sch.status()


@app.get("/api/settings")
async def api_get_settings(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    cfg = load_config(user["id"])
    return {
        "defaults": cfg.get("defaults") or DEFAULT_SETTINGS,
        "event_settings": cfg.get("event_settings") or {},
        "dry_run": bool(cfg.get("dry_run")),
        "group_by": cfg.get("group_by", "heading"),
    }


@app.post("/api/settings")
async def api_save_settings(
    body: Settings, user: dict = Depends(get_admin_user)
) -> dict[str, str]:
    uid = user["id"]
    cfg = load_config(uid)
    cfg["defaults"] = {
        "initial_delay": body.initial_delay,
        "retry_count": body.retry_count,
        "retry_interval": body.retry_interval,
        "response": body.response,
    }
    cfg["dry_run"] = body.dry_run
    cfg["group_by"] = body.group_by
    save_config(cfg, uid)
    return {"status": "ok"}


@app.get("/api/event-settings/{event_id}")
async def api_get_event_settings(
    event_id: str, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    cfg = load_config(user["id"])
    override = (cfg.get("event_settings") or {}).get(event_id)
    return {"override": override, "effective": settings_for(cfg, event_id)}


@app.post("/api/event-settings/{event_id}")
async def api_set_event_settings(
    event_id: str, body: EventSettings, user: dict = Depends(get_admin_user)
) -> dict[str, str]:
    uid = user["id"]
    cfg = load_config(uid)
    overrides = cfg.get("event_settings") or {}
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if payload:
        overrides[event_id] = payload
    else:
        overrides.pop(event_id, None)
    cfg["event_settings"] = overrides
    save_config(cfg, uid)
    return {"status": "ok"}


@app.delete("/api/event-settings/{event_id}")
async def api_clear_event_settings(
    event_id: str, user: dict = Depends(get_admin_user)
) -> dict[str, str]:
    uid = user["id"]
    cfg = load_config(uid)
    overrides = cfg.get("event_settings") or {}
    overrides.pop(event_id, None)
    cfg["event_settings"] = overrides
    save_config(cfg, uid)
    return {"status": "ok"}


@app.get("/api/history")
async def api_history(
    user: dict = Depends(get_current_user),
    limit: int = Query(default=100, ge=1, le=1000),
    event_id: str | None = None,
) -> dict[str, Any]:
    return {"entries": read_history(user["id"], limit=limit, event_id=event_id)}


@app.delete("/api/history")
async def api_clear_history(
    user: dict = Depends(get_current_user), event_id: str | None = None
) -> dict[str, Any]:
    return {"cleared": clear_history(user["id"], event_id=event_id)}


# ---------- static files + pages ----------

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


@app.get("/static/{path:path}")
async def static_files(path: str) -> FileResponse:
    full = (STATIC_DIR / path).resolve()
    if not full.is_relative_to(STATIC_DIR.resolve()) or not full.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(str(full), headers=_NO_CACHE)


@app.get("/login")
async def login_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "login.html"), headers=_NO_CACHE)


@app.get("/admin")
async def admin_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "admin.html"), headers=_NO_CACHE)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"), headers=_NO_CACHE)


@app.get("/settings")
async def settings_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "settings.html"), headers=_NO_CACHE)


@app.get("/logs")
async def logs_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "logs.html"), headers=_NO_CACHE)
