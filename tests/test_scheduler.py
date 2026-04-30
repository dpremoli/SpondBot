"""Tests for Scheduler._accept_later event-response logic."""
import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_tmp = tempfile.mkdtemp()
os.environ.setdefault("SPONDBOT_DATA", _tmp)

from webui.app import (  # noqa: E402
    ACCEPTED_PATH,
    DEFAULT_SETTINGS,
    FAILED_PATH,
    Scheduler,
)

PER = {**DEFAULT_SETTINGS, "retry_count": 2, "retry_interval": 0.0}
USER = "uid-abc"
EVENT = "event-001"
HEADING = "Sunday Social"


def make_scheduler(tmp_path: Path) -> Scheduler:
    with patch("webui.app.ACCEPTED_PATH", tmp_path / "accepted_ids.json"), \
         patch("webui.app.FAILED_PATH", tmp_path / "failed_ids.json"):
        s = Scheduler()
    return s


async def run_accept(scheduler, result_seq, tmp_path, event_id=EVENT, dry_run=False):
    """Run _accept_later with a mocked Spond client returning result_seq in order."""
    mock_client = AsyncMock()
    mock_client.change_response = AsyncMock(side_effect=result_seq)

    with patch("webui.app.ACCEPTED_PATH", tmp_path / "accepted_ids.json"), \
         patch("webui.app.FAILED_PATH", tmp_path / "failed_ids.json"), \
         patch("webui.app.HISTORY_PATH", tmp_path / "history.jsonl"), \
         patch.object(scheduler, "get_client", return_value=mock_client):
        await scheduler._accept_later(
            "user@example.com", "pass", event_id, 0.0, PER,
            HEADING, dry_run, USER, None,
        )


# ---------- happy path ----------

@pytest.mark.anyio
async def test_accept_success(tmp_path):
    s = make_scheduler(tmp_path)
    await run_accept(s, [{"acceptedIds": [USER]}], tmp_path)

    assert EVENT in s._accepted
    assert EVENT not in s._permanently_failed
    assert any(e["ok"] and e["event_id"] == EVENT for e in _read_history(tmp_path))


@pytest.mark.anyio
async def test_accept_waitlisted(tmp_path):
    s = make_scheduler(tmp_path)
    await run_accept(s, [{"waitinglistIds": [USER]}], tmp_path)

    assert EVENT in s._waitlisted
    assert EVENT not in s._accepted
    entries = _read_history(tmp_path)
    assert any(e["ok"] and e["waitlisted"] and e["event_id"] == EVENT for e in entries)


# ---------- 404 fast-fail ----------

@pytest.mark.anyio
async def test_404_marks_permanently_failed(tmp_path):
    s = make_scheduler(tmp_path)
    await run_accept(s, [{"errorCode": 404}], tmp_path)

    assert EVENT in s._permanently_failed
    assert EVENT not in s._accepted
    entries = _read_history(tmp_path)
    assert any("not invited" in (e.get("error") or "") for e in entries)


@pytest.mark.anyio
async def test_404_does_not_retry(tmp_path):
    """change_response should only be called once on 404."""
    s = make_scheduler(tmp_path)
    mock_client = AsyncMock()
    mock_client.change_response = AsyncMock(return_value={"errorCode": 404})

    with patch("webui.app.ACCEPTED_PATH", tmp_path / "accepted_ids.json"), \
         patch("webui.app.FAILED_PATH", tmp_path / "failed_ids.json"), \
         patch("webui.app.HISTORY_PATH", tmp_path / "history.jsonl"), \
         patch.object(s, "get_client", return_value=mock_client):
        await s._accept_later("u", "p", EVENT, 0.0, PER, HEADING, False, USER, None)

    assert mock_client.change_response.call_count == 1


# ---------- retry then succeed ----------

@pytest.mark.anyio
async def test_retry_then_succeed(tmp_path):
    """Bot retries on error response and eventually succeeds."""
    s = make_scheduler(tmp_path)
    # First two attempts return a non-success errorCode, third succeeds.
    results = [
        {"errorCode": 500},
        {"errorCode": 500},
        {"acceptedIds": [USER]},
    ]
    await run_accept(s, results, tmp_path)

    assert EVENT in s._accepted
    entries = _read_history(tmp_path)
    ok = [e for e in entries if e["ok"]]
    assert len(ok) == 1
    assert ok[0]["attempt"] == 3


# ---------- give up after all retries ----------

@pytest.mark.anyio
async def test_give_up_after_retries(tmp_path):
    s = make_scheduler(tmp_path)
    # Always returns a generic error — exhausts retry_count=2 → 3 total attempts
    results = [{"errorCode": 500}] * 10
    await run_accept(s, results, tmp_path)

    assert EVENT in s._permanently_failed
    entries = _read_history(tmp_path)
    gave_up = [e for e in entries if "gave up" in (e.get("error") or "")]
    assert len(gave_up) == 1


# ---------- dry run ----------

@pytest.mark.anyio
async def test_dry_run_no_api_call(tmp_path):
    s = make_scheduler(tmp_path)
    mock_client = AsyncMock()
    mock_client.change_response = AsyncMock()

    with patch("webui.app.ACCEPTED_PATH", tmp_path / "accepted_ids.json"), \
         patch("webui.app.FAILED_PATH", tmp_path / "failed_ids.json"), \
         patch("webui.app.HISTORY_PATH", tmp_path / "history.jsonl"), \
         patch.object(s, "get_client", return_value=mock_client):
        await s._accept_later("u", "p", EVENT, 0.0, PER, HEADING, True, USER, None)

    mock_client.change_response.assert_not_called()
    assert EVENT in s._accepted
    entries = _read_history(tmp_path)
    assert any(e.get("dry_run") for e in entries)


# ---------- persistence across restarts ----------

@pytest.mark.anyio
async def test_accepted_persisted_across_restart(tmp_path):
    s = make_scheduler(tmp_path)
    with patch("webui.app.ACCEPTED_PATH", tmp_path / "accepted_ids.json"), \
         patch("webui.app.FAILED_PATH", tmp_path / "failed_ids.json"), \
         patch("webui.app.HISTORY_PATH", tmp_path / "history.jsonl"), \
         patch.object(s, "get_client", return_value=_mock_client_ok(USER)):
        await s._accept_later("u", "p", EVENT, 0.0, PER, HEADING, False, USER, None)

    assert (tmp_path / "accepted_ids.json").exists()

    # Simulate restart: new Scheduler loads from disk.
    with patch("webui.app.ACCEPTED_PATH", tmp_path / "accepted_ids.json"), \
         patch("webui.app.FAILED_PATH", tmp_path / "failed_ids.json"):
        s2 = Scheduler()

    assert EVENT in s2._accepted


@pytest.mark.anyio
async def test_failed_persisted_across_restart(tmp_path):
    s = make_scheduler(tmp_path)
    with patch("webui.app.ACCEPTED_PATH", tmp_path / "accepted_ids.json"), \
         patch("webui.app.FAILED_PATH", tmp_path / "failed_ids.json"), \
         patch("webui.app.HISTORY_PATH", tmp_path / "history.jsonl"), \
         patch.object(s, "get_client", return_value=_mock_client({"errorCode": 404})):
        await s._accept_later("u", "p", EVENT, 0.0, PER, HEADING, False, USER, None)

    with patch("webui.app.ACCEPTED_PATH", tmp_path / "accepted_ids.json"), \
         patch("webui.app.FAILED_PATH", tmp_path / "failed_ids.json"):
        s2 = Scheduler()

    assert EVENT in s2._permanently_failed


# ---------- helpers ----------

def _mock_client_ok(user_id):
    c = AsyncMock()
    c.change_response = AsyncMock(return_value={"acceptedIds": [user_id]})
    return c


def _mock_client(result):
    c = AsyncMock()
    c.change_response = AsyncMock(return_value=result)
    return c


def _read_history(tmp_path: Path) -> list:
    p = tmp_path / "history.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
