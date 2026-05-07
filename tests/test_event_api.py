"""Tests for event API helpers and admin-gated endpoints."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp()
os.environ.setdefault("SPONDBOT_DATA", _tmp)
os.environ.setdefault("SPONDBOT_SECRET", "test-secret-key-for-tests-only")

from webui.app import (  # noqa: E402
    _extract_location,
    _is_payment_required,
    app,
    DEFAULT_SETTINGS,
    load_config,
    save_config,
)
from webui.auth import get_admin_user, get_current_user  # noqa: E402

TEST_UID = "test-uid-event-api"
ADMIN_USER = {"id": TEST_UID, "username": "admin", "is_admin": True}
PLAIN_USER = {"id": "plain-uid", "username": "bob", "is_admin": False}


# ============================================================
# _extract_location
# ============================================================

def test_extract_location_feature_properties():
    e = {"location": {"feature": {"properties": {"name": "Sports Hall"}}}}
    assert _extract_location(e) == "Sports Hall"


def test_extract_location_address_fallback():
    e = {"location": {"address": "123 Main St"}}
    assert _extract_location(e) == "123 Main St"


def test_extract_location_name_fallback():
    e = {"location": {"name": "Field 3"}}
    assert _extract_location(e) == "Field 3"


def test_extract_location_string_loc_is_safe():
    """Spond sometimes returns location as a plain string — must not crash."""
    e = {"location": "some string"}
    assert _extract_location(e) is None


def test_extract_location_string_feature_is_safe():
    e = {"location": {"feature": "not-a-dict"}}
    assert _extract_location(e) is None


def test_extract_location_missing():
    assert _extract_location({}) is None
    assert _extract_location({"location": None}) is None


def test_extract_location_empty_dict():
    assert _extract_location({"location": {}}) is None


# ============================================================
# _is_payment_required
# ============================================================

def test_payment_required_top_level_flag():
    assert _is_payment_required({"requiresPayment": True}) is True


def test_payment_required_registration_info():
    assert _is_payment_required({"registrationInfo": {"requiresPayment": True}}) is True
    assert _is_payment_required({"registrationInfo": {"price": 5.0}}) is True
    assert _is_payment_required({"registrationInfo": {"paymentInfo": {"amount": 10}}}) is True


def test_payment_not_required():
    assert _is_payment_required({}) is False
    assert _is_payment_required({"requiresPayment": False}) is False
    assert _is_payment_required({"registrationInfo": {}}) is False


# ============================================================
# Admin-only endpoint enforcement
# ============================================================

def _client_as(user: dict, tmp_path: Path) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: user
    if user["is_admin"]:
        app.dependency_overrides[get_admin_user] = lambda: user
    else:
        from fastapi import HTTPException
        app.dependency_overrides[get_admin_user] = _raises_403
    return TestClient(app, raise_server_exceptions=False)


def _raises_403():
    from fastapi import HTTPException
    raise HTTPException(403, "Admin only")


@pytest.fixture(autouse=True)
def _clean_overrides():
    yield
    app.dependency_overrides.clear()


def test_post_settings_admin_allowed(tmp_path):
    app.dependency_overrides[get_current_user] = lambda: ADMIN_USER
    app.dependency_overrides[get_admin_user] = lambda: ADMIN_USER
    with patch("webui.app.DATA_DIR", tmp_path), \
         patch.dict("webui.app._config_cache", {}, clear=True):
        save_config({"username": "u", "password": "", "group_ids": [],
                     "selected_event_ids": [], "defaults": dict(DEFAULT_SETTINGS),
                     "event_settings": {}, "dry_run": False, "group_by": "heading"}, TEST_UID)
        client = TestClient(app, raise_server_exceptions=False)
        payload = {**DEFAULT_SETTINGS, "dry_run": False, "group_by": "heading"}
        r = client.post("/api/settings", json=payload)
    assert r.status_code == 200


def test_post_settings_non_admin_rejected():
    app.dependency_overrides[get_current_user] = lambda: PLAIN_USER
    app.dependency_overrides[get_admin_user] = _raises_403
    client = TestClient(app, raise_server_exceptions=False)
    payload = {**DEFAULT_SETTINGS, "dry_run": False, "group_by": "heading"}
    r = client.post("/api/settings", json=payload)
    assert r.status_code == 403


def test_post_event_settings_non_admin_rejected():
    app.dependency_overrides[get_current_user] = lambda: PLAIN_USER
    app.dependency_overrides[get_admin_user] = _raises_403
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/event-settings/EVID123", json={"initial_delay": 1.0})
    assert r.status_code == 403


def test_delete_event_settings_non_admin_rejected():
    app.dependency_overrides[get_current_user] = lambda: PLAIN_USER
    app.dependency_overrides[get_admin_user] = _raises_403
    client = TestClient(app, raise_server_exceptions=False)
    r = client.delete("/api/event-settings/EVID123")
    assert r.status_code == 403


def test_get_event_settings_non_admin_allowed(tmp_path):
    """Reading per-event settings is still allowed for non-admins."""
    app.dependency_overrides[get_current_user] = lambda: PLAIN_USER
    app.dependency_overrides[get_admin_user] = _raises_403
    with patch("webui.app.DATA_DIR", tmp_path), \
         patch.dict("webui.app._config_cache", {}, clear=True):
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/event-settings/EVID123")
    assert r.status_code == 200


# ============================================================
# Decline endpoint wiring
# ============================================================

def _make_mock_scheduler(event_id="EV1"):
    sch = MagicMock()
    sch.events = [{"id": event_id, "heading": "Test Event",
                   "startTimestamp": "2099-01-01T10:00:00Z",
                   "recipients": {"group": {"id": "GRP1"}}}]
    sch._permanently_failed = set()
    sch._cached_member_ids = {"GRP1": "MEM1"}
    sch._cached_user_id = "MEM1"
    sch._scheduled = {}
    return sch


def test_decline_endpoint_fires_task(tmp_path):
    app.dependency_overrides[get_current_user] = lambda: ADMIN_USER
    app.dependency_overrides[get_admin_user] = lambda: ADMIN_USER

    mock_sch = _make_mock_scheduler("EV-DECLINE")
    mock_manager = AsyncMock()
    mock_manager.get = AsyncMock(return_value=mock_sch)

    import asyncio

    fired = []

    async def fake_accept_later(username, password, event_id, delay, per, heading, dry_run, member_id, start_ts):
        fired.append({"event_id": event_id, "response": per.get("response")})

    mock_sch._accept_later = fake_accept_later

    with patch("webui.app.DATA_DIR", tmp_path), \
         patch.dict("webui.app._config_cache", {}, clear=True), \
         patch("webui.app.manager", mock_manager):
        save_config({"username": "u@e.com", "password": "enc:dGVzdA==",
                     "group_ids": [], "selected_event_ids": [],
                     "defaults": dict(DEFAULT_SETTINGS), "event_settings": {},
                     "dry_run": False, "group_by": "heading"}, TEST_UID)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.post("/api/events/EV-DECLINE/decline")

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "fired"
    assert data["event_id"] == "EV-DECLINE"


def test_decline_endpoint_404_on_unknown_event(tmp_path):
    app.dependency_overrides[get_current_user] = lambda: ADMIN_USER

    mock_sch = _make_mock_scheduler("OTHER-EV")
    mock_manager = AsyncMock()
    mock_manager.get = AsyncMock(return_value=mock_sch)

    with patch("webui.app.DATA_DIR", tmp_path), \
         patch.dict("webui.app._config_cache", {}, clear=True), \
         patch("webui.app.manager", mock_manager):
        save_config({"username": "u@e.com", "password": "enc:dGVzdA==",
                     "group_ids": [], "selected_event_ids": [],
                     "defaults": dict(DEFAULT_SETTINGS), "event_settings": {},
                     "dry_run": False, "group_by": "heading"}, TEST_UID)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.post("/api/events/DOES-NOT-EXIST/decline")

    assert r.status_code == 404


def test_decline_uses_declined_response_regardless_of_settings(tmp_path):
    """Even if event-settings override response to 'accepted', decline must send 'declined'."""
    app.dependency_overrides[get_current_user] = lambda: ADMIN_USER
    app.dependency_overrides[get_admin_user] = lambda: ADMIN_USER

    mock_sch = _make_mock_scheduler("EV-DEC2")
    mock_manager = AsyncMock()
    mock_manager.get = AsyncMock(return_value=mock_sch)

    captured_per = {}

    async def fake_accept_later(username, password, event_id, delay, per, heading, dry_run, member_id, start_ts):
        captured_per.update(per)

    mock_sch._accept_later = fake_accept_later

    with patch("webui.app.DATA_DIR", tmp_path), \
         patch.dict("webui.app._config_cache", {}, clear=True), \
         patch("webui.app.manager", mock_manager):
        cfg = {"username": "u@e.com", "password": "enc:dGVzdA==",
               "group_ids": [], "selected_event_ids": [],
               "defaults": dict(DEFAULT_SETTINGS),
               "event_settings": {"EV-DEC2": {"response": "accepted"}},
               "dry_run": False, "group_by": "heading"}
        save_config(cfg, TEST_UID)
        client = TestClient(app, raise_server_exceptions=False)
        client.post("/api/events/EV-DEC2/decline")

    assert captured_per.get("response") == "declined"
