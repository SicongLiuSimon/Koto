"""Unit tests for the /api/health and /api/ping endpoints."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is importable
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from flask import Flask

from web.routes.health import health_bp


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(health_bp)
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# /api/ping
# ---------------------------------------------------------------------------


class TestPing:
    def test_ping_returns_200(self, client):
        resp = client.get("/api/ping")
        assert resp.status_code == 200

    def test_ping_body(self, client):
        data = client.get("/api/ping").get_json()
        assert data == {"status": "ok"}


# ---------------------------------------------------------------------------
# /api/health — happy path
# ---------------------------------------------------------------------------


class TestHealthHappyPath:
    @patch("web.routes.health._check_ollama", return_value={"status": "ok"})
    @patch(
        "web.routes.health._check_disk",
        return_value={"status": "ok", "free_mb": 5000.0},
    )
    def test_returns_200(self, _disk, _ollama, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200

    @patch("web.routes.health._check_ollama", return_value={"status": "ok"})
    @patch(
        "web.routes.health._check_disk",
        return_value={"status": "ok", "free_mb": 5000.0},
    )
    def test_has_expected_fields(self, _disk, _ollama, client):
        data = client.get("/api/health").get_json()
        assert data["status"] == "healthy"
        assert "uptime_seconds" in data
        assert "version" in data
        assert "checks" in data
        assert "timestamp" in data
        assert "ollama" in data["checks"]
        assert "disk" in data["checks"]

    @patch("web.routes.health._check_ollama", return_value={"status": "ok"})
    @patch(
        "web.routes.health._check_disk",
        return_value={"status": "ok", "free_mb": 5000.0},
    )
    def test_version_read(self, _disk, _ollama, client):
        data = client.get("/api/health").get_json()
        # VERSION file exists in the repo; should not be "unknown"
        assert data["version"] != ""
        assert isinstance(data["version"], str)


# ---------------------------------------------------------------------------
# Degraded — ollama unreachable
# ---------------------------------------------------------------------------


class TestHealthDegraded:
    @patch(
        "web.routes.health._check_ollama",
        return_value={"status": "error", "detail": "connection refused"},
    )
    @patch(
        "web.routes.health._check_disk",
        return_value={"status": "ok", "free_mb": 5000.0},
    )
    def test_degraded_when_ollama_down(self, _disk, _ollama, client):
        data = client.get("/api/health").get_json()
        assert data["status"] == "degraded"
        assert data["checks"]["ollama"]["status"] == "error"

    @patch(
        "web.routes.health._check_ollama",
        return_value={"status": "error", "detail": "connection refused"},
    )
    @patch(
        "web.routes.health._check_disk",
        return_value={"status": "ok", "free_mb": 5000.0},
    )
    def test_degraded_still_returns_200(self, _disk, _ollama, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Unhealthy — disk check fails
# ---------------------------------------------------------------------------


class TestHealthUnhealthy:
    @patch("web.routes.health._check_ollama", return_value={"status": "ok"})
    @patch(
        "web.routes.health._check_disk",
        return_value={"status": "error", "detail": "low disk"},
    )
    def test_unhealthy_when_disk_fails(self, _disk, _ollama, client):
        resp = client.get("/api/health")
        data = resp.get_json()
        assert data["status"] == "unhealthy"
        assert resp.status_code == 503
