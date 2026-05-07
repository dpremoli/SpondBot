"""Tests for SpondBot utility functions in webui/app.py."""
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

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

TEST_UID = "test-user-app"


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
    assert decrypt_password("plaintext") == "plaintext"


def test_decrypt_bad_token_returns_empty():
    assert decrypt_password("enc:notvalidbase64==") == ""


# ---------- config persistence ----------

def test_save_and_load_config(tmp_path):
    with patch("webui.app.DATA_DIR", tmp_path), \
         patch.dict("webui.app._config_cache", {}, clear=True):
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
        save_config(cfg, TEST_UID)
        del_cache(tmp_path)
        loaded = load_config(TEST_UID)
        assert loaded["username"] == "user@example.com"
        assert loaded["password"] == "secret"
        assert loaded["group_ids"] == ["ABC123"]


def test_load_config_defaults_on_empty(tmp_path):
    with patch("webui.app.DATA_DIR", tmp_path), \
         patch.dict("webui.app._config_cache", {}, clear=True):
        cfg = load_config(TEST_UID)
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
    ts = _available_epoch({"startTimestamp": "2026-06-01T10:00:00Z"})
    assert ts > 0


def test_available_epoch_no_timestamps_returns_now():
    before = time.time()
    ts = _available_epoch({})
    assert ts >= before


# ---------- history ----------

def test_append_and_read_history(tmp_path):
    with patch("webui.app.DATA_DIR", tmp_path):
        append_history({"event_id": "ev1", "heading": "Test", "ok": True}, TEST_UID)
        append_history({"event_id": "ev2", "heading": "Test2", "ok": False, "error": "oops"}, TEST_UID)
        entries = read_history(TEST_UID, limit=10)
        assert len(entries) == 2
        assert entries[0]["event_id"] == "ev2"  # newest first
        assert entries[1]["event_id"] == "ev1"


def test_read_history_limit(tmp_path):
    with patch("webui.app.DATA_DIR", tmp_path):
        for i in range(10):
            append_history({"event_id": f"ev{i}", "ok": True}, TEST_UID)
        entries = read_history(TEST_UID, limit=3)
        assert len(entries) == 3


def test_read_history_event_id_filter(tmp_path):
    with patch("webui.app.DATA_DIR", tmp_path):
        append_history({"event_id": "target", "ok": True}, TEST_UID)
        append_history({"event_id": "other", "ok": True}, TEST_UID)
        append_history({"event_id": "target", "ok": False, "error": "retry"}, TEST_UID)
        entries = read_history(TEST_UID, event_id="target")
        assert len(entries) == 2
        assert all(e["event_id"] == "target" for e in entries)


def test_read_history_missing_file(tmp_path):
    with patch("webui.app.DATA_DIR", tmp_path):
        uid = "no-history-user"
        assert read_history(uid) == []


# ---------- clear_history ----------

def test_clear_history_all(tmp_path):
    with patch("webui.app.DATA_DIR", tmp_path):
        for i in range(5):
            append_history({"event_id": f"ev{i}", "ok": True}, TEST_UID)
        removed = clear_history(TEST_UID)
        assert removed == 5
        assert read_history(TEST_UID) == []


def test_clear_history_by_event_id(tmp_path):
    with patch("webui.app.DATA_DIR", tmp_path):
        append_history({"event_id": "target", "ok": True}, TEST_UID)
        append_history({"event_id": "other", "ok": True}, TEST_UID)
        append_history({"event_id": "target", "ok": False, "error": "retry"}, TEST_UID)
        removed = clear_history(TEST_UID, event_id="target")
        assert removed == 2
        remaining = read_history(TEST_UID, limit=10)
        assert len(remaining) == 1
        assert remaining[0]["event_id"] == "other"


def test_clear_history_missing_file(tmp_path):
    with patch("webui.app.DATA_DIR", tmp_path):
        assert clear_history("ghost-user") == 0


def test_clear_history_nonexistent_event_id(tmp_path):
    with patch("webui.app.DATA_DIR", tmp_path):
        append_history({"event_id": "ev1", "ok": True}, TEST_UID)
        removed = clear_history(TEST_UID, event_id="no-such-event")
        assert removed == 0
        assert len(read_history(TEST_UID)) == 1


# ---------- per-user data isolation ----------

def test_history_is_isolated_per_user(tmp_path):
    """Two users' histories must not overlap."""
    with patch("webui.app.DATA_DIR", tmp_path):
        append_history({"event_id": "ev-alice", "ok": True}, "alice")
        append_history({"event_id": "ev-bob", "ok": True}, "bob")
        alice_entries = read_history("alice", limit=100)
        bob_entries = read_history("bob", limit=100)
        assert all(e["event_id"] == "ev-alice" for e in alice_entries)
        assert all(e["event_id"] == "ev-bob" for e in bob_entries)


def test_config_is_isolated_per_user(tmp_path):
    """Two users' configs must not cross-contaminate."""
    with patch("webui.app.DATA_DIR", tmp_path), \
         patch.dict("webui.app._config_cache", {}, clear=True):
        cfg_alice = {"username": "alice@example.com", "password": "pw-a",
                     "group_ids": ["G-ALICE"], "selected_event_ids": [],
                     "defaults": dict(DEFAULT_SETTINGS), "event_settings": {},
                     "dry_run": False, "group_by": "heading"}
        cfg_bob = {"username": "bob@example.com", "password": "pw-b",
                   "group_ids": ["G-BOB"], "selected_event_ids": [],
                   "defaults": dict(DEFAULT_SETTINGS), "event_settings": {},
                   "dry_run": False, "group_by": "day"}
        save_config(cfg_alice, "alice")
        save_config(cfg_bob, "bob")
        loaded_alice = load_config("alice")
        loaded_bob = load_config("bob")
        assert loaded_alice["username"] == "alice@example.com"
        assert loaded_bob["username"] == "bob@example.com"
        assert loaded_alice["group_by"] == "heading"
        assert loaded_bob["group_by"] == "day"


# ---------- helpers ----------

def del_cache(tmp_path):
    """Clear in-memory config cache between tests."""
    import webui.app as m
    m._config_cache.clear()
