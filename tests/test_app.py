"""Tests for SpondBot utility functions in webui/app.py."""
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Point data dir at a temp directory before importing app so it doesn't
# create ./data in the repo root during tests.
_tmp = tempfile.mkdtemp()
os.environ.setdefault("SPONDBOT_DATA", _tmp)

from webui.app import (  # noqa: E402
    DEFAULT_SETTINGS,
    VERSION,
    _available_epoch,
    append_history,
    clear_history,
    decrypt_password,
    encrypt_password,
    load_config,
    read_history,
    save_config,
    settings_for,
)


# ---------- version ----------

def test_version_is_semver():
    parts = VERSION.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


# ---------- password encryption ----------

def test_encrypt_decrypt_roundtrip():
    plain = "my-secret-password"
    assert decrypt_password(encrypt_password(plain)) == plain


def test_encrypt_empty():
    assert encrypt_password("") == ""
    assert decrypt_password("") == ""


def test_decrypt_legacy_plaintext():
    # Passwords stored without the "enc:" prefix are returned as-is.
    assert decrypt_password("plaintext") == "plaintext"


def test_decrypt_bad_token_returns_empty(capsys):
    assert decrypt_password("enc:notvalidbase64==") == ""


# ---------- config persistence ----------

def test_save_and_load_config(tmp_path):
    with patch("webui.app.CONFIG_PATH", tmp_path / "config.json"), \
         patch("webui.app._config_cache", None):
        cfg = {
            "username": "user@example.com",
            "password": "secret",
            "group_ids": ["ABC123"],
            "selected_event_ids": ["ev1"],
            "defaults": dict(DEFAULT_SETTINGS),
            "event_settings": {},
            "dry_run": False,
            "group_by": "heading",
        }
        save_config(cfg)
        loaded = load_config()
        assert loaded["username"] == "user@example.com"
        assert loaded["password"] == "secret"  # decrypted
        assert loaded["group_ids"] == ["ABC123"]


def test_load_config_defaults_on_empty(tmp_path):
    with patch("webui.app.CONFIG_PATH", tmp_path / "missing.json"), \
         patch("webui.app._config_cache", None):
        cfg = load_config()
        assert cfg["username"] == ""
        assert cfg["group_ids"] == []
        assert cfg["dry_run"] is False
        assert cfg["group_by"] == "heading"


# ---------- settings_for ----------

def test_settings_for_uses_defaults():
    cfg = {"defaults": {"initial_delay": 1.0, "retry_count": 5,
                        "retry_interval": 0.5, "response": "accepted"},
           "event_settings": {}}
    s = settings_for(cfg, "event-xyz")
    assert s["initial_delay"] == 1.0
    assert s["retry_count"] == 5


def test_settings_for_applies_override():
    cfg = {"defaults": dict(DEFAULT_SETTINGS),
           "event_settings": {"ev1": {"initial_delay": 9.9, "response": "declined"}}}
    s = settings_for(cfg, "ev1")
    assert s["initial_delay"] == 9.9
    assert s["response"] == "declined"
    # Un-overridden keys inherit from defaults
    assert s["retry_count"] == DEFAULT_SETTINGS["retry_count"]


def test_settings_for_no_override_uses_global_defaults():
    cfg = {"defaults": None, "event_settings": None}
    s = settings_for(cfg, "ev-unknown")
    assert s == DEFAULT_SETTINGS


# ---------- _available_epoch ----------

def test_available_epoch_invite_time():
    import datetime as dt
    iso = "2026-05-01T18:00:00Z"
    expected = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    assert _available_epoch({"inviteTime": iso}) == pytest.approx(expected, abs=1)


def test_available_epoch_falls_back_to_start():
    event = {"startTimestamp": "2026-06-01T10:00:00Z"}
    ts = _available_epoch(event)
    assert ts > 0


def test_available_epoch_no_timestamps_returns_now():
    before = time.time()
    ts = _available_epoch({})
    assert ts >= before


# ---------- history ----------

def test_append_and_read_history(tmp_path):
    with patch("webui.app.HISTORY_PATH", tmp_path / "history.jsonl"):
        append_history({"event_id": "ev1", "heading": "Test", "ok": True})
        append_history({"event_id": "ev2", "heading": "Test2", "ok": False, "error": "oops"})
        entries = read_history(limit=10)
        assert len(entries) == 2
        # read_history returns newest first
        assert entries[0]["event_id"] == "ev2"
        assert entries[1]["event_id"] == "ev1"


def test_read_history_limit(tmp_path):
    with patch("webui.app.HISTORY_PATH", tmp_path / "history.jsonl"):
        for i in range(10):
            append_history({"event_id": f"ev{i}", "ok": True})
        entries = read_history(limit=3)
        assert len(entries) == 3


def test_read_history_event_id_filter(tmp_path):
    with patch("webui.app.HISTORY_PATH", tmp_path / "history.jsonl"):
        append_history({"event_id": "target", "ok": True})
        append_history({"event_id": "other", "ok": True})
        append_history({"event_id": "target", "ok": False, "error": "retry"})
        entries = read_history(event_id="target")
        assert len(entries) == 2
        assert all(e["event_id"] == "target" for e in entries)


def test_read_history_missing_file(tmp_path):
    with patch("webui.app.HISTORY_PATH", tmp_path / "nonexistent.jsonl"):
        assert read_history() == []


# ---------- clear_history ----------

def test_clear_history_all(tmp_path):
    with patch("webui.app.HISTORY_PATH", tmp_path / "history.jsonl"):
        for i in range(5):
            append_history({"event_id": f"ev{i}", "ok": True})
        removed = clear_history()
        assert removed == 5
        assert read_history() == []


def test_clear_history_by_event_id(tmp_path):
    with patch("webui.app.HISTORY_PATH", tmp_path / "history.jsonl"):
        append_history({"event_id": "target", "ok": True})
        append_history({"event_id": "other", "ok": True})
        append_history({"event_id": "target", "ok": False, "error": "retry"})
        removed = clear_history(event_id="target")
        assert removed == 2
        remaining = read_history(limit=10)
        assert len(remaining) == 1
        assert remaining[0]["event_id"] == "other"


def test_clear_history_missing_file(tmp_path):
    with patch("webui.app.HISTORY_PATH", tmp_path / "nonexistent.jsonl"):
        assert clear_history() == 0


def test_clear_history_nonexistent_event_id(tmp_path):
    with patch("webui.app.HISTORY_PATH", tmp_path / "history.jsonl"):
        append_history({"event_id": "ev1", "ok": True})
        removed = clear_history(event_id="no-such-event")
        assert removed == 0
        assert len(read_history()) == 1
