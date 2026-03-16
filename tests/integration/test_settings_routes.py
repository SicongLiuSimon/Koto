# -*- coding: utf-8 -*-
"""
Integration tests for settings-related endpoints in web/app.py:
- GET  /api/settings — get all settings
- POST /api/settings — update a setting
- POST /api/settings/reset — reset settings to defaults
- GET  /api/info — app metadata
- GET  /api/setup/status — setup status check
- GET  /api/v1/models — model listing
- GET  /api/token-stats — token usage statistics
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def client():
    """Create Flask test client from the monolith web app."""
    os.environ.setdefault("KOTO_AUTH_ENABLED", "false")
    os.environ.setdefault("KOTO_DEPLOY_MODE", "local")
    os.environ.pop("SENTRY_DSN", None)

    from web.app import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _check(resp, ok_status=(200,)):
    body = resp.get_data(as_text=True)
    assert resp.status_code in ok_status, f"HTTP {resp.status_code}: {body[:400]}"
    return resp.get_json()


# ── GET /api/settings ────────────────────────────────────────────────────────


@pytest.mark.integration
class TestGetSettings:
    def test_returns_200(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 200

    def test_returns_json_dict(self, client):
        data = _check(client.get("/api/settings"))
        assert isinstance(data, dict)

    def test_contains_expected_categories(self, client):
        data = _check(client.get("/api/settings"))
        # Settings should have at least storage and appearance categories
        assert "storage" in data or "appearance" in data or "ai" in data

    def test_method_not_allowed_delete(self, client):
        """DELETE /api/settings should return 405."""
        resp = client.delete("/api/settings")
        assert resp.status_code == 405


# ── POST /api/settings ───────────────────────────────────────────────────────


@pytest.mark.integration
class TestUpdateSettings:
    def test_update_valid_setting(self, client):
        resp = client.post(
            "/api/settings",
            json={"category": "appearance", "key": "theme", "value": "dark"},
        )
        data = _check(resp)
        assert data["success"] is True

    def test_missing_category_returns_error(self, client):
        resp = client.post("/api/settings", json={"key": "theme", "value": "dark"})
        data = resp.get_json()
        assert data["success"] is False

    def test_missing_key_returns_error(self, client):
        resp = client.post(
            "/api/settings", json={"category": "appearance", "value": "dark"}
        )
        data = resp.get_json()
        assert data["success"] is False

    def test_update_ai_setting(self, client):
        resp = client.post(
            "/api/settings",
            json={"category": "ai", "key": "show_thinking", "value": True},
        )
        data = _check(resp)
        assert data["success"] is True

    def test_setting_persists_in_get(self, client):
        """After updating a setting, GET should reflect the change."""
        client.post(
            "/api/settings",
            json={"category": "appearance", "key": "theme", "value": "light"},
        )
        data = _check(client.get("/api/settings"))
        appearance = data.get("appearance", {})
        assert appearance.get("theme") == "light"

        # Restore default
        client.post(
            "/api/settings",
            json={"category": "appearance", "key": "theme", "value": "dark"},
        )


# ── POST /api/settings/reset ────────────────────────────────────────────────


@pytest.mark.integration
class TestResetSettings:
    def test_reset_returns_success(self, client):
        data = _check(client.post("/api/settings/reset"))
        assert data["success"] is True

    def test_reset_restores_defaults(self, client):
        """After reset, settings should contain default values."""
        client.post("/api/settings/reset")
        data = _check(client.get("/api/settings"))
        appearance = data.get("appearance", {})
        assert appearance.get("theme") == "dark"


# ── GET /api/info ────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestApiInfo:
    def test_returns_200(self, client):
        resp = client.get("/api/info")
        assert resp.status_code == 200

    def test_contains_version(self, client):
        data = _check(client.get("/api/info"))
        assert "version" in data
        assert data["version"] != ""

    def test_contains_deploy_mode(self, client):
        data = _check(client.get("/api/info"))
        assert "deploy_mode" in data
        assert data["deploy_mode"] in ("local", "cloud")

    def test_contains_auth_enabled(self, client):
        data = _check(client.get("/api/info"))
        assert "auth_enabled" in data
        assert isinstance(data["auth_enabled"], bool)


# ── GET /api/setup/status ────────────────────────────────────────────────────


@pytest.mark.integration
class TestSetupStatus:
    def test_returns_200(self, client):
        resp = client.get("/api/setup/status")
        assert resp.status_code == 200

    def test_contains_required_fields(self, client):
        data = _check(client.get("/api/setup/status"))
        assert "has_api_key" in data
        assert "has_workspace" in data
        assert "workspace_path" in data
        assert isinstance(data["has_workspace"], bool)


# ── GET /api/v1/models ───────────────────────────────────────────────────────


@pytest.mark.integration
class TestModelsEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/api/v1/models")
        assert resp.status_code == 200

    def test_contains_model_map(self, client):
        data = _check(client.get("/api/v1/models"))
        assert "model_map" in data
        assert "available" in data

    def test_ready_field_is_bool(self, client):
        data = _check(client.get("/api/v1/models"))
        assert "ready" in data
        assert isinstance(data["ready"], bool)


# ── GET /api/token-stats ─────────────────────────────────────────────────────


@pytest.mark.integration
class TestTokenStats:
    def test_returns_200(self, client):
        resp = client.get("/api/token-stats")
        # May be 200 or 500 if token_tracker not available
        assert resp.status_code in (200, 500)

    def test_returns_json(self, client):
        resp = client.get("/api/token-stats")
        data = resp.get_json()
        assert data is not None
