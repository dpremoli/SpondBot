"""Tests for Scheduler._accept_later event-response logic."""
import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_tmp = tempfile.mkdtemp()
os.environ.setdefault("SPONDBOT_DATA", _tmp)

from webui.app import (  # noqa: E402
    DEFAULT_SETTINGS,
    Scheduler,
    _accepted_path,
    _failed_path,
    _history_path,
)

PER = {**DEFAULT_SETTINGS, "retry_count": 2, "retry_interval": 0.0}
SPOND_USER = "uid-abc"
EVENT = "event-001"
HEADING = "Sunday Social"
TEST_UID = "test-user-scheduler"


def make_scheduler(tmp_path: Path) -> Scheduler:
    # Point DATA_DIR at tmp_path so per-user dirs land there
    with patch("webui.app.DATA_DIR", tmp_path):
        s = Scheduler(TEST_UID)
    return s


async def run_accept(scheduler, result_seq, tmp_path, event_id=EVENT, dry_run=False):
    mock_client = AsyncMock()
    mock_client.change_response = AsyncMock(side_effect=result_seq)

    with patch("webui.app.DATA_DIR", tmp_path), \
         patch.object(scheduler, "get_client", return_value=mock_client):
        await scheduler._accept_later(
            "user@example.com", "pass", event_id, 0.0, PER,
            HEADING, dry_run, SPOND_USER, None,
        )


# ---------- happy path ----------

@pytest.mark.anyio
async def test_accept_success(tmp_path):
    s = make_scheduler(tmp_path)
    await run_accept(s, [{"acceptedIds": [SPOND_USER]}], tmp_path)

    assert EVENT in s._accepted
    assert EVENT not in s._permanently_failed
    assert any(e["ok"] and e["event_id"] == EVENT for e in _read_history(tmp_path))


@pytest.mark.anyio
async def test_accept_waitlisted(tmp_path):
    s = make_scheduler(tmp_path)
    await run_accept(s, [{"waitinglistIds": [SPOND_USER]}], tmp_path)

    assert EVENT in s._waitlisted
    assert EVENT not in s._accepted
    entries = _read_history(tmp_path)
    assert any(e["ok"] and e["waitlisted"] and e["event_id"] == EVENT for e in entries)


# ---------- 404 retried (race condition fix) ----------

@pytest.mark.anyio
async def test_404_retried_and_eventually_fails(tmp_path):
    """Persistent 404 across all attempts → permanently failed with 'not invited' message."""
    s = make_scheduler(tmp_path)
    await run_accept(s, [{"errorCode": 404}] * 10, tmp_path)

    assert EVENT in s._permanently_failed
    assert EVENT not in s._accepted
    entries = _read_history(tmp_path)
    assert any("not invited" in (e.get("error") or "") for e in entries)


@pytest.mark.anyio
async def test_404_retries_all_attempts(tmp_path):
    """Bot retries on 404; change_response called retry_count+1 times."""
    s = make_scheduler(tmp_path)
    mock_client = AsyncMock()
    mock_client.change_response = AsyncMock(return_value={"errorCode": 404})

    with patch("webui.app.DATA_DIR", tmp_path), \
         patch.object(s, "get_client", return_value=mock_client):
        await s._accept_later("u", "p", EVENT, 0.0, PER, HEADING, False, SPOND_USER, None)

    assert mock_client.change_response.call_count == 3  # retry_count=2 → 3 total


@pytest.mark.anyio
async def test_404_then_success(tmp_path):
    """404 on first attempt (invite race) then succeeds on retry."""
    s = make_scheduler(tmp_path)
    results = [{"errorCode": 404}, {"acceptedIds": [SPOND_USER]}]
    await run_accept(s, results, tmp_path)

    assert EVENT in s._accepted
    assert EVENT not in s._permanently_failed
    entries = _read_history(tmp_path)
    assert any(e["ok"] and e["event_id"] == EVENT for e in entries)


# ---------- retry then succeed ----------

@pytest.mark.anyio
async def test_retry_then_succeed(tmp_path):
    """Bot retries on error response and eventually succeeds."""
    s = make_scheduler(tmp_path)
    results = [{"errorCode": 500}, {"errorCode": 500}, {"acceptedIds": [SPOND_USER]}]
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
    await run_accept(s, [{"errorCode": 500}] * 10, tmp_path)

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

    with patch("webui.app.DATA_DIR", tmp_path), \
         patch.object(s, "get_client", return_value=mock_client):
        await s._accept_later("u", "p", EVENT, 0.0, PER, HEADING, True, SPOND_USER, None)

    mock_client.change_response.assert_not_called()
    assert EVENT in s._accepted
    entries = _read_history(tmp_path)
    assert any(e.get("dry_run") for e in entries)


# ---------- persistence across restarts ----------

@pytest.mark.anyio
async def test_accepted_persisted_across_restart(tmp_path):
    s = make_scheduler(tmp_path)
    with patch("webui.app.DATA_DIR", tmp_path), \
         patch.object(s, "get_client", return_value=_mock_client_ok(SPOND_USER)):
        await s._accept_later("u", "p", EVENT, 0.0, PER, HEADING, False, SPOND_USER, None)

    accepted_file = tmp_path / "users" / TEST_UID / "accepted_ids.json"
    assert accepted_file.exists()

    with patch("webui.app.DATA_DIR", tmp_path):
        s2 = Scheduler(TEST_UID)
    assert EVENT in s2._accepted


@pytest.mark.anyio
async def test_failed_persisted_across_restart(tmp_path):
    s = make_scheduler(tmp_path)
    with patch("webui.app.DATA_DIR", tmp_path), \
         patch.object(s, "get_client", return_value=_mock_client({"errorCode": 404})):
        await s._accept_later("u", "p", EVENT, 0.0, PER, HEADING, False, SPOND_USER, None)

    with patch("webui.app.DATA_DIR", tmp_path):
        s2 = Scheduler(TEST_UID)
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
    p = tmp_path / "users" / TEST_UID / "history.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
