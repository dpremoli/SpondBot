"""Tests for multi-user authentication and security (users.py, auth.py)."""
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from jose import jwt

_tmp = tempfile.mkdtemp()
os.environ.setdefault("SPONDBOT_DATA", _tmp)
os.environ.setdefault("SPONDBOT_SECRET", "test-secret-key-for-tests-only")

from webui.users import (  # noqa: E402
    create_user,
    delete_user,
    get_user_by_id,
    get_user_by_username,
    hash_password,
    verify_password,
)
from webui.auth import (  # noqa: E402
    ALGORITHM,
    create_access_token,
    decode_token,
)
from fastapi import HTTPException


# ============================================================
# Password hashing
# ============================================================

def test_hash_password_is_bcrypt():
    h = hash_password("mysecret")
    assert h.startswith("$2b$") or h.startswith("$2a$")


def test_verify_password_correct():
    h = hash_password("correct")
    assert verify_password("correct", h) is True


def test_verify_password_wrong():
    h = hash_password("correct")
    assert verify_password("wrong", h) is False


def test_verify_password_none_hash_returns_false():
    """Must return False (and not raise) for non-existent user — timing-safe."""
    assert verify_password("anything", None) is False


def test_verify_password_none_still_runs_bcrypt(monkeypatch):
    """Even with None hash, bcrypt must still run (timing safety)."""
    import bcrypt as _bcrypt
    calls = []
    real_checkpw = _bcrypt.checkpw
    def spy_checkpw(pw, h):
        calls.append(pw)
        return real_checkpw(pw, h)
    monkeypatch.setattr("webui.users.bcrypt.checkpw", spy_checkpw)
    verify_password("dummy-input", None)
    assert len(calls) == 1  # always calls bcrypt even for unknown user


# ============================================================
# JWT tokens
# ============================================================

def test_create_and_decode_token():
    token = create_access_token("uid-1", "alice", is_admin=False)
    payload = decode_token(token)
    assert payload["sub"] == "uid-1"
    assert payload["username"] == "alice"
    assert payload["is_admin"] is False


def test_create_admin_token():
    token = create_access_token("uid-2", "admin", is_admin=True)
    payload = decode_token(token)
    assert payload["is_admin"] is True


def test_decode_invalid_token_raises_401():
    with pytest.raises(HTTPException) as exc:
        decode_token("not.a.valid.token")
    assert exc.value.status_code == 401


def test_decode_expired_token_raises_401():
    from webui.auth import _SECRET_KEY, ALGORITHM
    expired = jwt.encode(
        {"sub": "uid-x", "username": "x", "is_admin": False,
         "exp": datetime.now(timezone.utc) - timedelta(seconds=10)},
        _SECRET_KEY, algorithm=ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        decode_token(expired)
    assert exc.value.status_code == 401


def test_decode_token_missing_sub_raises_401():
    from webui.auth import _SECRET_KEY, ALGORITHM
    bad = jwt.encode(
        {"username": "alice", "is_admin": False,
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        _SECRET_KEY, algorithm=ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        decode_token(bad)
    assert exc.value.status_code == 401


# ============================================================
# User CRUD
# ============================================================

def test_create_user_returns_public_fields(tmp_path):
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        u = create_user("alice", "password123")
        assert "hashed_password" not in u
        assert u["username"] == "alice"
        assert u["is_admin"] is False
        assert "id" in u


def test_create_user_stores_bcrypt_hash(tmp_path):
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        create_user("bob", "mypassword")
        from webui.users import _load_raw
        raw = _load_raw()
        user = next(u for u in raw if u["username"] == "bob")
        assert user["hashed_password"].startswith("$2b$") or user["hashed_password"].startswith("$2a$")
        assert "mypassword" not in user["hashed_password"]


def test_create_user_duplicate_raises(tmp_path):
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        create_user("carol", "password1")
        with pytest.raises(ValueError, match="already exists"):
            create_user("carol", "password2")


def test_create_user_duplicate_case_insensitive(tmp_path):
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        create_user("Dave", "password1")
        with pytest.raises(ValueError):
            create_user("dave", "password2")


def test_get_user_by_username_case_insensitive(tmp_path):
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        create_user("Eve", "password")
        assert get_user_by_username("eve") is not None
        assert get_user_by_username("EVE") is not None


def test_get_user_by_username_missing_returns_none(tmp_path):
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        assert get_user_by_username("ghost") is None


def test_get_user_by_id(tmp_path):
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        u = create_user("frank", "password")
        found = get_user_by_id(u["id"])
        assert found is not None
        assert found["username"] == "frank"


def test_delete_user(tmp_path):
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        u = create_user("grace", "password")
        assert delete_user(u["id"]) is True
        assert get_user_by_username("grace") is None


def test_delete_nonexistent_user_returns_false(tmp_path):
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        assert delete_user("no-such-id") is False


def test_update_user_password(tmp_path):
    from webui.users import update_user
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        u = create_user("henry", "oldpassword")
        update_user(u["id"], password="newpassword")
        updated = get_user_by_id(u["id"])
        assert verify_password("newpassword", updated["hashed_password"]) is True
        assert verify_password("oldpassword", updated["hashed_password"]) is False


def test_update_nonexistent_user_raises(tmp_path):
    from webui.users import update_user
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        with pytest.raises(KeyError):
            update_user("no-such-id", is_admin=True)


# ============================================================
# Security: hashed_password never exposed
# ============================================================

def test_public_strips_hashed_password(tmp_path):
    """create_user must never return hashed_password in public dict."""
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        u = create_user("iris", "secretpassword")
        assert "hashed_password" not in u


def test_load_users_strips_hashed_password(tmp_path):
    """load_users must not expose hashed_password."""
    from webui.users import load_users
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        create_user("jake", "password")
        for u in load_users():
            assert "hashed_password" not in u


# ============================================================
# Users file atomic write
# ============================================================

def test_users_file_created_atomically(tmp_path):
    """No partial writes: file must not exist in half-written state."""
    with patch("webui.users.DATA_DIR", tmp_path), \
         patch("webui.users.USERS_PATH", tmp_path / "users.json"):
        create_user("kurt", "password")
        assert (tmp_path / "users.json").exists()
        # Temp file must be cleaned up
        assert not (tmp_path / "users.json.tmp").exists()
