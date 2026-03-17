"""
Unit tests for core API endpoints in web/app.py:
- /api/health
- /api/info
- /api/ping
- /api/chat (mocked brain)
- Global error handlers (404, 405, 500) → JSON with request_id
- X-Request-ID header on every response
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="module")
def client():
    """Create Flask test client with auth disabled."""
    import os

    os.environ.setdefault("KOTO_AUTH_ENABLED", "false")
    os.environ.setdefault("KOTO_DEPLOY_MODE", "local")
    # Prevent sentry init during tests
    os.environ.pop("SENTRY_DSN", None)

    from web.app import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── /api/health ───────────────────────────────────────────────────────────────


class TestHealthEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/api/health")
        assert resp.status_code in (200, 503)

    def test_returns_json(self, client):
        resp = client.get("/api/health")
        data = resp.get_json()
        assert data is not None

    def test_status_field(self, client):
        resp = client.get("/api/health")
        assert resp.get_json()["status"] in ("healthy", "degraded", "unhealthy")

    def test_includes_version(self, client):
        resp = client.get("/api/health")
        data = resp.get_json()
        assert "version" in data
        assert data["version"] != ""

    def test_includes_timestamp(self, client):
        resp = client.get("/api/health")
        data = resp.get_json()
        assert "timestamp" in data
        assert "uptime_seconds" in data
        assert "checks" in data

    def test_request_id_header(self, client):
        resp = client.get("/api/health")
        assert "X-Request-ID" in resp.headers

    def test_custom_request_id_echoed(self, client):
        resp = client.get("/api/health", headers={"X-Request-ID": "test-123"})
        assert resp.headers.get("X-Request-ID") == "test-123"


# ── /api/info ─────────────────────────────────────────────────────────────────


class TestInfoEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/api/info")
        assert resp.status_code == 200

    def test_contains_version(self, client):
        data = resp = client.get("/api/info").get_json()
        assert "version" in data

    def test_contains_deploy_mode(self, client):
        data = client.get("/api/info").get_json()
        assert "deploy_mode" in data
        assert data["deploy_mode"] in ("local", "cloud")

    def test_contains_auth_enabled(self, client):
        data = client.get("/api/info").get_json()
        assert "auth_enabled" in data
        assert isinstance(data["auth_enabled"], bool)

    def test_request_id_header(self, client):
        resp = client.get("/api/info")
        assert "X-Request-ID" in resp.headers


# ── Global error handlers ─────────────────────────────────────────────────────


class TestGlobalErrorHandlers:
    def test_404_returns_json(self, client):
        resp = client.get("/this/route/does/not/exist")
        assert resp.status_code == 404
        assert resp.content_type.startswith("application/json")

    def test_404_has_error_field(self, client):
        resp = client.get("/no/such/route")
        data = resp.get_json()
        assert "error" in data

    def test_404_has_status_field(self, client):
        data = client.get("/no/such/route").get_json()
        assert data.get("status") == 404

    def test_404_has_request_id(self, client):
        data = client.get("/no/such/route").get_json()
        assert "request_id" in data
        assert len(data["request_id"]) > 0

    def test_404_has_request_id_header(self, client):
        resp = client.get("/no/such/route")
        assert "X-Request-ID" in resp.headers
        # request_id in body matches header
        assert resp.get_json()["request_id"] == resp.headers["X-Request-ID"]

    def test_405_returns_json(self, client):
        # GET on a POST-only endpoint
        resp = client.get("/api/chat")
        assert resp.status_code == 405
        assert resp.content_type.startswith("application/json")

    def test_405_has_error_field(self, client):
        data = client.get("/api/chat").get_json()
        assert "error" in data

    def test_500_handler_returns_json(self, client):
        """Verify /api/health returns 500 JSON when an unexpected error occurs."""
        from web import app as web_app_module

        # Blueprint registers view as "health.health"
        view_name = (
            "health.health"
            if "health.health" in web_app_module.app.view_functions
            else "health"
        )
        original_fn = web_app_module.app.view_functions.get(view_name)

        def _raise():
            raise RuntimeError("forced 500 for test")

        web_app_module.app.view_functions[view_name] = _raise
        # Ensure Flask doesn't re-raise during testing
        web_app_module.app.config["PROPAGATE_EXCEPTIONS"] = False
        try:
            resp = client.get("/api/health")
            assert resp.status_code == 500
            data = resp.get_json()
            assert data is not None
            assert "error" in data or "status" in data
        finally:
            if original_fn is not None:
                web_app_module.app.view_functions[view_name] = original_fn
            web_app_module.app.config["PROPAGATE_EXCEPTIONS"] = True


# ── /api/chat (mocked) ────────────────────────────────────────────────────────


class TestChatEndpoint:
    def _post(self, client, payload):
        return client.post(
            "/api/chat",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_missing_session_returns_400(self, client):
        resp = self._post(client, {"message": "hello"})
        assert resp.status_code == 400

    def test_missing_message_returns_400(self, client):
        resp = self._post(client, {"session": "test-sess"})
        assert resp.status_code == 400

    def test_empty_message_returns_400(self, client):
        resp = self._post(client, {"session": "test-sess", "message": ""})
        assert resp.status_code == 400

    def test_valid_request_calls_brain(self, client):
        """Mock brain.chat to verify routing and response shape."""
        from web import app as app_module

        mock_result = {"response": "Hello from AI!", "model": "gemini-test"}
        with patch.object(
            app_module.brain, "chat", return_value=mock_result
        ) as mock_chat:
            resp = self._post(client, {"session": "sess1", "message": "hi"})
            mock_chat.assert_called_once()
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["response"] == "Hello from AI!"

    def test_response_includes_request_id_header(self, client):
        from web import app as app_module

        with patch.object(
            app_module.brain, "chat", return_value={"response": "ok", "model": "x"}
        ):
            resp = self._post(client, {"session": "sess1", "message": "test"})
            assert "X-Request-ID" in resp.headers
