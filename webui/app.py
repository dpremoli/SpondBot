"""SpondBot web UI — auto-accept Spond event invites the moment they open."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from spond import AuthenticationError, spond

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("spondbot")

DATA_DIR = Path(os.environ.get("SPONDBOT_DATA", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"
HISTORY_PATH = DATA_DIR / "history.jsonl"

STATIC_DIR = Path(__file__).parent / "static"

POLL_INTERVAL = 30  # seconds between event-list refreshes

DEFAULT_SETTINGS = {
    "initial_delay": 0.3,
    "retry_count": 10,
    "retry_interval": 0.3,
    "response": "accepted",  # accepted | declined | unconfirmed
}


# ---------- config persistence ----------

def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text())
    else:
        cfg = {}
    cfg.setdefault("username", "")
    cfg.setdefault("password", "")
    cfg.setdefault("group_ids", [])
    cfg.setdefault("selected_event_ids", [])
    cfg.setdefault("defaults", dict(DEFAULT_SETTINGS))
    cfg.setdefault("event_settings", {})  # event_id -> partial settings dict
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def settings_for(cfg: dict[str, Any], event_id: str) -> dict[str, Any]:
    merged = dict(DEFAULT_SETTINGS)
    merged.update(cfg.get("defaults") or {})
    merged.update((cfg.get("event_settings") or {}).get(event_id) or {})
    return merged


def append_history(entry: dict[str, Any]) -> None:
    entry = {"ts": time.time(), **entry}
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def read_history(limit: int = 100) -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    lines = HISTORY_PATH.read_text().splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))


# ---------- scheduler state ----------

class Scheduler:
    """Polls Spond for scheduled events, schedules an auto-accept task per selected event."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._scheduled: dict[str, asyncio.Task] = {}
        self._accepted: set[str] = set()
        self._events_cache: list[dict] = []

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        for t in self._scheduled.values():
            t.cancel()
        self._scheduled.clear()

    @property
    def events(self) -> list[dict]:
        return self._events_cache

    @property
    def accepted(self) -> set[str]:
        return self._accepted

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(POLL_INTERVAL)

    async def _tick(self) -> None:
        cfg = load_config()
        if not cfg.get("username") or not cfg.get("password"):
            return
        group_ids = cfg.get("group_ids") or []
        selected = set(cfg.get("selected_event_ids") or [])

        s = spond.Spond(username=cfg["username"], password=cfg["password"])
        try:
            all_events: list[dict] = []
            seen_ids: set[str] = set()
            sources = group_ids if group_ids else [None]
            for gid in sources:
                evs = await s.get_events(
                    group_id=gid, include_scheduled=True, max_events=200
                ) or []
                for e in evs:
                    eid = e.get("id")
                    if eid and eid not in seen_ids:
                        seen_ids.add(eid)
                        all_events.append(e)

            self._events_cache = all_events

            now = time.time()
            for e in all_events:
                eid = e["id"]
                if eid not in selected or eid in self._accepted:
                    continue
                if eid in self._scheduled and not self._scheduled[eid].done():
                    continue
                per = settings_for(cfg, eid)
                invite_ts = _available_epoch(e)
                delay = max(0.0, invite_ts - now) + float(per["initial_delay"])
                log.info(
                    "scheduling auto-%s for %s in %.2fs (%s)",
                    per["response"], eid, delay, e.get("heading"),
                )
                self._scheduled[eid] = asyncio.create_task(
                    self._accept_later(
                        cfg["username"], cfg["password"], eid, delay, per,
                        e.get("heading", ""),
                    )
                )
        finally:
            await s.clientsession.close()

    async def _accept_later(
        self,
        username: str,
        password: str,
        event_id: str,
        delay: float,
        per: dict[str, Any],
        heading: str,
    ) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        s = spond.Spond(username=username, password=password)
        try:
            profile = await s.get_profile()
            user_id = (
                (profile.get("profile") or {}).get("id")
                or profile.get("id")
            )
            if not user_id:
                log.error("could not resolve profile id for accept")
                append_history({
                    "event_id": event_id, "heading": heading,
                    "ok": False, "error": "no profile id",
                })
                return

            response_key = per.get("response", "accepted")
            payload = {response_key: "true"}

            retries = int(per["retry_count"])
            interval = float(per["retry_interval"])
            for attempt in range(1, retries + 2):
                try:
                    result = await s.change_response(event_id, user_id, payload)
                    if isinstance(result, dict) and "error" not in result:
                        log.info(
                            "%s event %s on attempt %d",
                            response_key, event_id, attempt,
                        )
                        self._accepted.add(event_id)
                        append_history({
                            "event_id": event_id, "heading": heading,
                            "response": response_key, "attempt": attempt,
                            "ok": True,
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
                await asyncio.sleep(interval)
            log.error(
                "gave up on event %s after %d attempts", event_id, retries + 1,
            )
            append_history({
                "event_id": event_id, "heading": heading,
                "response": response_key, "ok": False,
                "error": f"gave up after {retries + 1} attempts",
            })
        finally:
            await s.clientsession.close()


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
    }


@app.post("/api/config")
async def api_save_config(body: Credentials) -> dict[str, str]:
    s = spond.Spond(username=body.username, password=body.password)
    try:
        await s.login()
    except AuthenticationError as exc:
        raise HTTPException(401, f"Spond login failed: {exc}") from exc
    finally:
        await s.clientsession.close()

    cfg = load_config()
    cfg["username"] = body.username
    cfg["password"] = body.password
    cfg["group_ids"] = [g.strip() for g in body.group_ids if g.strip()]
    save_config(cfg)
    await scheduler.start()
    return {"status": "ok"}


@app.get("/api/events")
async def api_events() -> dict[str, Any]:
    cfg = load_config()
    selected = set(cfg.get("selected_event_ids") or [])
    event_settings = cfg.get("event_settings") or {}
    events = []
    for e in scheduler.events:
        gid = (e.get("group") or {}).get("id") or e.get("groupId")
        events.append({
            "id": e["id"],
            "heading": e.get("heading"),
            "groupId": gid,
            "groupName": (e.get("group") or {}).get("name"),
            "startTimestamp": e.get("startTimestamp"),
            "endTimestamp": e.get("endTimestamp"),
            "inviteTime": e.get("inviteTime") or e.get("invitedTimestamp"),
            "selected": e["id"] in selected,
            "accepted": e["id"] in scheduler.accepted,
            "hasOverride": e["id"] in event_settings,
        })
    return {"events": events, "defaults": cfg.get("defaults") or DEFAULT_SETTINGS}


@app.post("/api/selection")
async def api_selection(body: Selection) -> dict[str, str]:
    cfg = load_config()
    cfg["selected_event_ids"] = list(dict.fromkeys(body.event_ids))
    save_config(cfg)
    return {"status": "ok"}


@app.post("/api/refresh")
async def api_refresh() -> dict[str, str]:
    await scheduler._tick()  # noqa: SLF001
    return {"status": "ok"}


@app.get("/api/settings")
async def api_get_settings() -> dict[str, Any]:
    cfg = load_config()
    return {
        "defaults": cfg.get("defaults") or DEFAULT_SETTINGS,
        "event_settings": cfg.get("event_settings") or {},
    }


@app.post("/api/settings")
async def api_save_settings(body: Settings) -> dict[str, str]:
    cfg = load_config()
    cfg["defaults"] = body.model_dump()
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
async def api_history(limit: int = 100) -> dict[str, Any]:
    return {"entries": read_history(limit=limit)}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/settings")
async def settings_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "settings.html"))
