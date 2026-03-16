"""
Comprehensive unit tests for web/auth.py — covering JWT validation, password
hashing, user persistence, token lifecycle, rate limiting, and Flask
decorator middleware.
"""

import hashlib
import json
import os
import tempfile
import time

import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_auth_module():
    """Import (or re-import) the auth module."""
    import web.auth as auth_mod
    return auth_mod


def _make_flask_app(auth_enabled: bool = True):
    """Create a minimal Flask app with auth routes registered."""
    from flask import Flask
    auth_mod = _get_auth_module()

    app = Flask(__name__)
    app.config["TESTING"] = True

    # Decorate a couple of dummy endpoints for decorator tests
    @app.route("/protected")
    @auth_mod.require_auth
    def protected():
        from flask import g, jsonify
        return jsonify({"user": g.user_id})

    @app.route("/optional")
    @auth_mod.optional_auth
    def optional():
        from flask import g, jsonify
        return jsonify({"user": g.user_id})

    return app


# ═══════════════════════════════════════════════════════════════════════════
# 1. _validate_jwt_secret
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestValidateJwtSecret:

    def test_local_mode_returns_ephemeral_secret(self, monkeypatch):
        """Local mode with no KOTO_JWT_SECRET returns a non-empty ephemeral secret."""
        monkeypatch.setenv("KOTO_DEPLOY_MODE", "local")
        monkeypatch.delenv("KOTO_JWT_SECRET", raising=False)
        auth_mod = _get_auth_module()
        result = auth_mod._validate_jwt_secret()
        assert isinstance(result, str) and len(result) > 0

    def test_cloud_mode_no_secret_raises(self, monkeypatch):
        """Cloud mode with no KOTO_JWT_SECRET must raise RuntimeError."""
        monkeypatch.setenv("KOTO_DEPLOY_MODE", "cloud")
        monkeypatch.delenv("KOTO_JWT_SECRET", raising=False)
        auth_mod = _get_auth_module()
        with pytest.raises(RuntimeError, match="KOTO_JWT_SECRET"):
            auth_mod._validate_jwt_secret()

    def test_cloud_mode_with_secret_returns_it(self, monkeypatch):
        """Cloud mode with KOTO_JWT_SECRET set returns that value."""
        monkeypatch.setenv("KOTO_DEPLOY_MODE", "cloud")
        monkeypatch.setenv("KOTO_JWT_SECRET", "my-cloud-secret-abc123")
        auth_mod = _get_auth_module()
        assert auth_mod._validate_jwt_secret() == "my-cloud-secret-abc123"


# ═══════════════════════════════════════════════════════════════════════════
# 2. _hash_password
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestHashPassword:

    def test_returns_consistent_hash_with_same_salt(self):
        auth_mod = _get_auth_module()
        h1, s1 = auth_mod._hash_password("password123", salt="fixedsalt")
        h2, s2 = auth_mod._hash_password("password123", salt="fixedsalt")
        assert h1 == h2
        assert s1 == s2 == "fixedsalt"

    def test_generates_different_salt_each_call(self):
        auth_mod = _get_auth_module()
        _, s1 = auth_mod._hash_password("password123")
        _, s2 = auth_mod._hash_password("password123")
        assert s1 != s2

    def test_explicit_salt_is_deterministic(self):
        """PBKDF2-HMAC-SHA256 with the same inputs always yields the same hash."""
        auth_mod = _get_auth_module()
        expected = hashlib.pbkdf2_hmac(
            "sha256", b"hello", b"salt42", 100000
        ).hex()
        h, _ = auth_mod._hash_password("hello", salt="salt42")
        assert h == expected


# ═══════════════════════════════════════════════════════════════════════════
# 3. _load_users / _save_users
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestUserPersistence:

    def test_load_users_with_valid_file(self, tmp_path, monkeypatch):
        users_file = tmp_path / "users.json"
        sample = {"alice@test.com": {"user_id": "u1", "name": "Alice"}}
        users_file.write_text(json.dumps(sample), encoding="utf-8")

        auth_mod = _get_auth_module()
        monkeypatch.setattr(auth_mod, "USERS_FILE", str(users_file))
        loaded = auth_mod._load_users()
        assert loaded == sample

    def test_load_users_missing_file_returns_empty(self, tmp_path, monkeypatch):
        auth_mod = _get_auth_module()
        monkeypatch.setattr(auth_mod, "USERS_FILE", str(tmp_path / "nonexistent.json"))
        assert auth_mod._load_users() == {}

    def test_save_users_writes_json(self, tmp_path, monkeypatch):
        users_file = tmp_path / "out" / "users.json"
        auth_mod = _get_auth_module()
        monkeypatch.setattr(auth_mod, "USERS_FILE", str(users_file))

        data = {"bob@test.com": {"user_id": "u2", "name": "Bob"}}
        auth_mod._save_users(data)

        assert users_file.exists()
        saved = json.loads(users_file.read_text(encoding="utf-8"))
        assert saved == data


# ═══════════════════════════════════════════════════════════════════════════
# 4. _generate_token / _verify_token
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestTokenLifecycle:

    def test_generate_token_returns_string(self):
        auth_mod = _get_auth_module()
        token = auth_mod._generate_token("uid1", "user@test.com")
        assert isinstance(token, str) and len(token) > 0

    def test_verify_valid_token_returns_payload(self):
        auth_mod = _get_auth_module()
        token = auth_mod._generate_token("uid1", "user@test.com")
        payload = auth_mod._verify_token(token)
        assert payload is not None
        assert payload["user_id"] == "uid1"
        assert payload["email"] == "user@test.com"

    def test_verify_invalid_token_returns_none(self):
        auth_mod = _get_auth_module()
        assert auth_mod._verify_token("garbage.token.value") is None

    def test_verify_expired_token_returns_none(self):
        auth_mod = _get_auth_module()
        # Generate a token that is already expired by patching time.time
        original_time = time.time
        with patch("time.time", return_value=original_time() - 400_000):
            token = auth_mod._generate_token("uid1", "user@test.com")
        # Verify at real (current) time — token should be expired
        assert auth_mod._verify_token(token) is None


# ═══════════════════════════════════════════════════════════════════════════
# 5. Rate limiting
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestRateLimiting:

    @pytest.fixture(autouse=True)
    def _clear_rate_limits(self):
        auth_mod = _get_auth_module()
        auth_mod._rate_limits.clear()
        yield
        auth_mod._rate_limits.clear()

    def test_under_limit_returns_true(self):
        auth_mod = _get_auth_module()
        assert auth_mod._check_rate_limit("testuser") is True

    def test_over_limit_returns_false(self):
        auth_mod = _get_auth_module()
        # Fill up to the limit
        for _ in range(auth_mod.MAX_DAILY_REQUESTS):
            auth_mod._increment_request("testuser")
        assert auth_mod._check_rate_limit("testuser") is False

    def test_increment_request_increments_counter(self):
        auth_mod = _get_auth_module()
        auth_mod._increment_request("u1")
        auth_mod._increment_request("u1")
        assert auth_mod._rate_limits["u1"]["count"] == 2

    def test_rate_limit_resets_on_new_day(self):
        auth_mod = _get_auth_module()
        # Simulate yesterday's data
        auth_mod._rate_limits["u1"] = {"date": "1999-01-01", "count": 999}
        # Checking today should reset and return True
        assert auth_mod._check_rate_limit("u1") is True
        assert auth_mod._rate_limits["u1"]["count"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 6. Flask decorator middleware
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestFlaskDecorators:

    @pytest.fixture(autouse=True)
    def _clear_rate_limits(self):
        auth_mod = _get_auth_module()
        auth_mod._rate_limits.clear()
        yield
        auth_mod._rate_limits.clear()

    def test_require_auth_blocks_unauthenticated(self, monkeypatch):
        """With AUTH_ENABLED=True and no token, require_auth returns 401."""
        auth_mod = _get_auth_module()
        monkeypatch.setattr(auth_mod, "AUTH_ENABLED", True)
        app = _make_flask_app(auth_enabled=True)
        with app.test_client() as c:
            resp = c.get("/protected")
            assert resp.status_code == 401

    def test_require_auth_allows_valid_token(self, monkeypatch):
        """With AUTH_ENABLED=True and a valid Bearer token, require_auth passes."""
        auth_mod = _get_auth_module()
        monkeypatch.setattr(auth_mod, "AUTH_ENABLED", True)
        token = auth_mod._generate_token("uid-ok", "ok@test.com")
        app = _make_flask_app(auth_enabled=True)
        with app.test_client() as c:
            resp = c.get("/protected", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            assert resp.get_json()["user"] == "uid-ok"

    def test_optional_auth_allows_unauthenticated_when_disabled(self, monkeypatch):
        """With AUTH_ENABLED=False, optional_auth sets user to 'local'."""
        auth_mod = _get_auth_module()
        monkeypatch.setattr(auth_mod, "AUTH_ENABLED", False)
        app = _make_flask_app(auth_enabled=False)
        with app.test_client() as c:
            resp = c.get("/optional")
            assert resp.status_code == 200
            assert resp.get_json()["user"] == "local"

    def test_optional_auth_requires_token_when_enabled(self, monkeypatch):
        """With AUTH_ENABLED=True and no token, optional_auth returns 401."""
        auth_mod = _get_auth_module()
        monkeypatch.setattr(auth_mod, "AUTH_ENABLED", True)
        app = _make_flask_app(auth_enabled=True)
        with app.test_client() as c:
            resp = c.get("/optional")
            assert resp.status_code == 401
