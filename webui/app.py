"""SpondBot web UI — auto-accept Spond event invites the moment they open."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiohttp
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from spond import AuthenticationError, spond

VERSION = "0.5.0"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("spondbot")

DATA_DIR = Path(os.environ.get("SPONDBOT_DATA", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"
HISTORY_PATH = DATA_DIR / "history.jsonl"
KEY_PATH = DATA_DIR / ".key"

STATIC_DIR = Path(__file__).parent / "static"

# Poll cadence (seconds). Deliberately long — the actual auto-accept runs off a
# local timer scheduled at tick time, so we don't need to poll frequently just
# to know when to fire. Polling is only for discovering *new* scheduled events
# or reflecting selection changes. Env var lets you tune without rebuilding.
POLL_INTERVAL = int(os.environ.get("SPONDBOT_POLL_INTERVAL", "900"))  # 15 min
# Tighten (but don't eliminate) polling when there's an event arming soon.
POLL_INTERVAL_NEAR_FIRE = int(
    os.environ.get("SPONDBOT_POLL_INTERVAL_NEAR_FIRE", "120")
)
# "Soon" window — if any armed accept is within this many seconds, use the
# tighter interval above.
NEAR_FIRE_WINDOW = 300

# Manual /api/refresh is throttled to this many seconds.
MANUAL_REFRESH_MIN_INTERVAL = int(
    os.environ.get("SPONDBOT_MANUAL_REFRESH_MIN", "30")
)

DEFAULT_SETTINGS = {
    "initial_delay": 0.3,
    "retry_count": 10,
    "retry_interval": 0.3,
    "response": "accepted",  # accepted | declined | unconfirmed
}


# ---------- password-at-rest encryption ----------

def _load_or_create_key() -> bytes:
    """Load or create the Fernet key used to encrypt the Spond password.

    Env var `SPONDBOT_SECRET` (any string) overrides the on-disk key — useful
    when you want the key to live somewhere other than the data volume.
    """
    env = os.environ.get("SPONDBOT_SECRET")
    if env:
        # Derive a 32-byte key from the passphrase.
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
        # legacy plaintext — migrate on next save
        return stored
    try:
        return _fernet.decrypt(stored[4:].encode("ascii")).decode("utf-8")
    except InvalidToken:
        log.error("stored password could not be decrypted — key mismatch")
        return ""


# ---------- config persistence ----------

_config_cache: dict[str, Any] | None = None


def load_config() -> dict[str, Any]:
    """Return config with the password decrypted. Reads disk only once."""
    global _config_cache
    if _config_cache is not None:
        return dict(_config_cache)
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text())
    else:
        cfg = {}
    cfg.setdefault("username", "")
    cfg["password"] = decrypt_password(cfg.get("password", ""))
    cfg.setdefault("group_ids", [])
    cfg.setdefault("selected_event_ids", [])
    cfg.setdefault("defaults", dict(DEFAULT_SETTINGS))
    cfg.setdefault("event_settings", {})
    cfg.setdefault("dry_run", False)
    cfg.setdefault("group_by", "heading")  # heading | day | week | year
    _config_cache = dict(cfg)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    """Persist config; the password is encrypted before writing. Invalidates cache."""
    global _config_cache
    stored = dict(cfg)
    stored["password"] = encrypt_password(cfg.get("password", ""))
    CONFIG_PATH.write_text(json.dumps(stored, indent=2))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    _config_cache = dict(cfg)  # update cache with the just-saved values


def settings_for(cfg: dict[str, Any], event_id: str) -> dict[str, Any]:
    merged = dict(DEFAULT_SETTINGS)
    merged.update(cfg.get("defaults") or {})
    merged.update((cfg.get("event_settings") or {}).get(event_id) or {})
    return merged


def append_history(entry: dict[str, Any]) -> None:
    entry = {"ts": time.time(), **entry}
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def read_history(limit: int = 100, event_id: str | None = None) -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    lines = HISTORY_PATH.read_text().splitlines()
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event_id is None or entry.get("event_id") == event_id:
            out.append(entry)
    if event_id is None:
        out = out[-limit:]
    return list(reversed(out))


# ---------- scheduler state ----------

class Scheduler:
    """Polls Spond sparingly; schedules local timer tasks to auto-respond."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._scheduled: dict[str, asyncio.Task] = {}
        self._scheduled_fire_ts: dict[str, float] = {}  # event_id -> epoch fire time
        self._accepted: set[str] = set()
        self._waitlisted: set[str] = set()
        self._events_cache: list[dict] = []
        self._client: spond.Spond | None = None
        self._client_key: tuple[str, str] | None = None
        self._client_lock = asyncio.Lock()
        self._last_error: str | None = None
        self._last_tick_ts: float | None = None
        self._last_manual_refresh_ts: float = 0.0
        self._cached_user_id: str | None = None
        self._wake_event = asyncio.Event()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def last_tick_ts(self) -> float | None:
        return self._last_tick_ts

    def status(self) -> dict[str, Any]:
        cfg = load_config()
        now = time.time()
        pending = [
            (eid, ts) for eid, ts in self._scheduled_fire_ts.items()
            if eid not in self._accepted and ts >= now
        ]
        pending.sort(key=lambda p: p[1])
        next_id, next_ts = (pending[0] if pending else (None, None))
        next_heading = None
        if next_id:
            for e in self._events_cache:
                if e.get("id") == next_id:
                    next_heading = e.get("heading")
                    break
        return {
            "last_tick_ts": self._last_tick_ts,
            "last_error": self._last_error,
            "events_cached": len(self._events_cache),
            "scheduled_count": len(pending),
            "next_fire_ts": next_ts,
            "next_event_heading": next_heading,
            "accepted_count": len(self._accepted),
            "dry_run": bool(cfg.get("dry_run")),
            "logged_in": self._client is not None,
            "poll_interval": self._current_poll_interval(),
            "version": VERSION,
        }

    def _current_poll_interval(self) -> int:
        """Tighter cadence if something is armed to fire within NEAR_FIRE_WINDOW."""
        now = time.time()
        for ts in self._scheduled_fire_ts.values():
            if 0 < ts - now <= NEAR_FIRE_WINDOW:
                return POLL_INTERVAL_NEAR_FIRE
        return POLL_INTERVAL

    def wake(self) -> None:
        """Nudge the loop so it ticks on the next iteration instead of sleeping."""
        self._wake_event.set()

    async def get_client(self, username: str, password: str) -> spond.Spond:
        """Return a Spond client, reusing the existing one across calls.

        Spond rate-limits aggressively; every `Spond(...)` call does a fresh
        login. We share one instance keyed by (username, password) so its
        cached `token` is reused for all subsequent API calls.
        """
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
                log.exception("scheduler tick failed")
            interval = self._current_poll_interval()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            self._wake_event.clear()

    async def manual_refresh(self) -> None:
        """Throttled manual tick — rate-limit guard against UI spam."""
        now = time.time()
        if now - self._last_manual_refresh_ts < MANUAL_REFRESH_MIN_INTERVAL:
            remaining = int(
                MANUAL_REFRESH_MIN_INTERVAL - (now - self._last_manual_refresh_ts)
            )
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
        cfg = load_config()
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
            # Re-raise the first real exception so the existing handler catches it.
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
                f"Spond returned non-JSON ({exc.status}) — likely rate-limited. "
                "Backing off."
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
            log.exception("tick failure")
            return

        # Pre-fetch user profile so _accept_later doesn't need an API call at fire time.
        if self._cached_user_id is None:
            try:
                profile = await s.get_profile()
                uid = (
                    (profile.get("profile") or {}).get("id")
                    or profile.get("id")
                )
                if uid:
                    self._cached_user_id = uid
                else:
                    log.warning("could not resolve profile id from profile response")
            except Exception as exc:
                log.warning("profile pre-fetch failed: %s", exc)

        now = time.time()
        for e in all_events:
            eid = e["id"]
            if eid not in selected or eid in self._accepted:
                continue
            if eid in self._scheduled and not self._scheduled[eid].done():
                continue
            per = settings_for(cfg, eid)
            invite_ts = _available_epoch(e)
            fire_ts = invite_ts + float(per["initial_delay"])
            delay = max(0.0, fire_ts - now)
            log.info(
                "arming auto-%s for %s in %.1fs (%s)%s",
                per["response"], eid, delay, e.get("heading"),
                " [dry-run]" if cfg.get("dry_run") else "",
            )
            self._scheduled_fire_ts[eid] = fire_ts
            self._scheduled[eid] = asyncio.create_task(
                self._accept_later(
                    cfg["username"], cfg["password"], eid, delay, per,
                    e.get("heading", ""), bool(cfg.get("dry_run")),
                    self._cached_user_id,
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
    ) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        response_key = per.get("response", "accepted")

        if dry_run:
            log.info(
                "[dry-run] would %s event %s (%s)", response_key, event_id, heading,
            )
            self._accepted.add(event_id)
            append_history({
                "event_id": event_id, "heading": heading,
                "response": response_key, "attempt": 0,
                "ok": True, "dry_run": True,
            })
            return

        s = await self.get_client(username, password)

        # Fall back to a live profile fetch if the pre-cached id is missing.
        if not user_id:
            try:
                profile = await s.get_profile()
                user_id = (
                    (profile.get("profile") or {}).get("id")
                    or profile.get("id")
                )
            except Exception as exc:
                log.error("could not fetch profile for accept: %s", exc)
                append_history({
                    "event_id": event_id, "heading": heading,
                    "ok": False, "error": f"profile fetch: {exc}",
                })
                return

        if not user_id:
            log.error("could not resolve profile id for accept")
            append_history({
                "event_id": event_id, "heading": heading,
                "ok": False, "error": "no profile id",
            })
            return

        payload = {response_key: "true"}
        retries = int(per["retry_count"])
        interval = float(per["retry_interval"])
        for attempt in range(1, retries + 2):
            try:
                result = await s.change_response(event_id, user_id, payload)
                # Error responses contain "errorCode" (e.g. {"errorCode": 404, ...})
                if isinstance(result, dict) and "errorCode" not in result:
                    waitlisted = user_id in result.get("waitinglistIds", [])
                    if waitlisted:
                        log.info("waitlisted for event %s on attempt %d", event_id, attempt)
                        self._waitlisted.add(event_id)
                        self._accepted.discard(event_id)
                    else:
                        log.info("%s event %s on attempt %d", response_key, event_id, attempt)
                        self._accepted.add(event_id)
                        self._waitlisted.discard(event_id)
                    append_history({
                        "event_id": event_id, "heading": heading,
                        "response": response_key, "attempt": attempt,
                        "ok": True, "waitlisted": waitlisted,
                    })
                    return
                log.warning(
                    "attempt %d for %s returned: %s",
                    attempt, event_id, result,
                )
            except Exception as exc:
                log.warning(
                    "attempt %d for %s failed: %s", attempt, event_id, exc,
                )
            if attempt < retries + 1:
                await asyncio.sleep(interval)
        log.error(
            "gave up on event %s after %d attempts", event_id, retries + 1,
        )
        append_history({
            "event_id": event_id, "heading": heading,
            "response": response_key, "ok": False,
            "error": f"gave up after {retries + 1} attempts",
        })


def _available_epoch(event: dict) -> float:
    """Best-effort guess of the UTC epoch when an invite becomes responsive."""
    import datetime as dt

    for key in ("inviteTime", "invitedTimestamp", "startTimestamp"):
        val = event.get(key)
        if val:
            try:
                return dt.datetime.fromisoformat(
                    val.replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, AttributeError):
                continue
    return time.time()


scheduler = Scheduler()


# ---------- FastAPI ----------

@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    await scheduler.start()
    yield
    await scheduler.stop()


app = FastAPI(title="SpondBot", lifespan=lifespan)


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
    response: str | None = Field(
        default=None, pattern="^(accepted|declined|unconfirmed)$",
    )


@app.get("/api/config")
async def api_get_config() -> dict[str, Any]:
    cfg = load_config()
    return {
        "username": cfg.get("username", ""),
        "has_password": bool(cfg.get("password")),
        "group_ids": cfg.get("group_ids", []),
        "selected_event_ids": cfg.get("selected_event_ids", []),
        "dry_run": bool(cfg.get("dry_run")),
        "group_by": cfg.get("group_by", "heading"),
    }


@app.post("/api/config")
async def api_save_config(body: Credentials) -> dict[str, str]:
    cfg = load_config()
    creds_changed = (
        body.username != cfg.get("username")
        or body.password != cfg.get("password")
    )
    cfg["username"] = body.username
    cfg["password"] = body.password
    cfg["group_ids"] = [g.strip() for g in body.group_ids if g.strip()]
    save_config(cfg)

    if creds_changed:
        await scheduler.reset_client()
        try:
            s = await scheduler.get_client(body.username, body.password)
            await s.login()
        except AuthenticationError as exc:
            raise HTTPException(401, f"Spond login failed: {exc}") from exc
        except aiohttp.ContentTypeError as exc:
            raise HTTPException(
                429 if exc.status == 429 else 502,
                "Spond rate-limited the login. Wait a few minutes and try again.",
            ) from exc

    await scheduler.start()
    scheduler.wake()
    return {"status": "ok"}


@app.get("/api/events")
async def api_events() -> dict[str, Any]:
    cfg = load_config()
    selected = set(cfg.get("selected_event_ids") or [])
    event_settings = cfg.get("event_settings") or {}
    user_id = scheduler._cached_user_id
    events = []
    for e in scheduler.events:
        gid = (e.get("group") or {}).get("id") or e.get("groupId")
        responses = e.get("responses") or {}
        # Derive status from Spond response data (accurate across restarts) and
        # fall back to in-memory tracking for events accepted in this session
        # before the next poll refreshes the cache.
        in_accepted = user_id and user_id in (responses.get("acceptedIds") or [])
        in_waitlist = user_id and user_id in (responses.get("waitinglistIds") or [])
        accepted = bool(in_accepted) or e["id"] in scheduler.accepted
        waitlisted = bool(in_waitlist) or e["id"] in scheduler.waitlisted
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
            "hasOverride": e["id"] in event_settings,
        })
    return {
        "events": events,
        "defaults": cfg.get("defaults") or DEFAULT_SETTINGS,
        "group_by": cfg.get("group_by", "heading"),
    }


@app.post("/api/selection")
async def api_selection(body: Selection) -> dict[str, str]:
    cfg = load_config()
    cfg["selected_event_ids"] = list(dict.fromkeys(body.event_ids))
    save_config(cfg)
    return {"status": "ok"}


@app.post("/api/refresh")
async def api_refresh() -> dict[str, str]:
    await scheduler.manual_refresh()
    return {"status": "ok"}


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    """Cheap endpoint — no Spond calls, just internal scheduler state."""
    return scheduler.status()


@app.get("/api/settings")
async def api_get_settings() -> dict[str, Any]:
    cfg = load_config()
    return {
        "defaults": cfg.get("defaults") or DEFAULT_SETTINGS,
        "event_settings": cfg.get("event_settings") or {},
        "dry_run": bool(cfg.get("dry_run")),
        "group_by": cfg.get("group_by", "heading"),
    }


@app.post("/api/settings")
async def api_save_settings(body: Settings) -> dict[str, str]:
    cfg = load_config()
    cfg["defaults"] = {
        "initial_delay": body.initial_delay,
        "retry_count": body.retry_count,
        "retry_interval": body.retry_interval,
        "response": body.response,
    }
    cfg["dry_run"] = body.dry_run
    cfg["group_by"] = body.group_by
    save_config(cfg)
    return {"status": "ok"}


@app.get("/api/event-settings/{event_id}")
async def api_get_event_settings(event_id: str) -> dict[str, Any]:
    cfg = load_config()
    override = (cfg.get("event_settings") or {}).get(event_id)
    return {
        "override": override,
        "effective": settings_for(cfg, event_id),
    }


@app.post("/api/event-settings/{event_id}")
async def api_set_event_settings(
    event_id: str, body: EventSettings,
) -> dict[str, str]:
    cfg = load_config()
    overrides = cfg.get("event_settings") or {}
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if payload:
        overrides[event_id] = payload
    else:
        overrides.pop(event_id, None)
    cfg["event_settings"] = overrides
    save_config(cfg)
    return {"status": "ok"}


@app.delete("/api/event-settings/{event_id}")
async def api_clear_event_settings(event_id: str) -> dict[str, str]:
    cfg = load_config()
    overrides = cfg.get("event_settings") or {}
    overrides.pop(event_id, None)
    cfg["event_settings"] = overrides
    save_config(cfg)
    return {"status": "ok"}


@app.get("/api/history")
async def api_history(limit: int = 100, event_id: str | None = None) -> dict[str, Any]:
    return {"entries": read_history(limit=limit, event_id=event_id)}


_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


@app.get("/static/{path:path}")
async def static_files(path: str) -> FileResponse:
    full = STATIC_DIR / path
    if not full.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(str(full), headers=_NO_CACHE)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"), headers=_NO_CACHE)


@app.get("/settings")
async def settings_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "settings.html"), headers=_NO_CACHE)


@app.get("/logs")
async def logs_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "logs.html"), headers=_NO_CACHE)


# Silence secrets-unused warning without adding runtime cost.
_ = secrets
