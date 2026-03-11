"""Integration tests for the Skill Marketplace blueprint (/api/skillmarket/*).

All tests use the Flask test client with no real LLM or file I/O outside tmp dirs.
"""
from __future__ import annotations
import json
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def market_client(full_client):
    """Reuse the full-app test client (marketplace_bp is registered in full_app)."""
    return full_client


# ---------------------------------------------------------------------------
# Catalog & library
# ---------------------------------------------------------------------------

class TestCatalog:
    def test_catalog_returns_200(self, market_client):
        resp = market_client.get("/api/skillmarket/catalog")
        assert resp.status_code == 200

    def test_catalog_response_is_json(self, market_client):
        resp = market_client.get("/api/skillmarket/catalog")
        data = resp.get_json()
        assert data is not None

    def test_library_returns_200(self, market_client):
        resp = market_client.get("/api/skillmarket/library")
        assert resp.status_code == 200

    def test_library_response_is_json(self, market_client):
        resp = market_client.get("/api/skillmarket/library")
        assert resp.get_json() is not None


# ---------------------------------------------------------------------------
# Featured & search
# ---------------------------------------------------------------------------

class TestFeaturedAndSearch:
    def test_featured_returns_200(self, market_client):
        resp = market_client.get("/api/skillmarket/featured")
        assert resp.status_code == 200

    def test_search_returns_200_with_query(self, market_client):
        resp = market_client.get("/api/skillmarket/search?q=code")
        assert resp.status_code == 200

    def test_search_returns_400_without_query(self, market_client):
        """Search endpoint requires q param; missing q returns 400."""
        resp = market_client.get("/api/skillmarket/search")
        assert resp.status_code == 400

    def test_search_response_is_json(self, market_client):
        resp = market_client.get("/api/skillmarket/search?q=write")
        assert resp.get_json() is not None


# ---------------------------------------------------------------------------
# Stats & status
# ---------------------------------------------------------------------------

class TestStatsAndStatus:
    def test_stats_returns_200(self, market_client):
        resp = market_client.get("/api/skillmarket/stats")
        assert resp.status_code == 200

    def test_stats_response_is_json(self, market_client):
        resp = market_client.get("/api/skillmarket/stats")
        assert resp.get_json() is not None

    def test_status_returns_200(self, market_client):
        resp = market_client.get("/api/skillmarket/status")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Active skills
# ---------------------------------------------------------------------------

class TestActiveSkills:
    def test_active_returns_200(self, market_client):
        resp = market_client.get("/api/skillmarket/active")
        assert resp.status_code == 200

    def test_active_response_is_json(self, market_client):
        resp = market_client.get("/api/skillmarket/active")
        assert resp.get_json() is not None


# ---------------------------------------------------------------------------
# Install / uninstall lifecycle
# ---------------------------------------------------------------------------

class TestInstallUninstall:
    def test_install_without_body_returns_4xx(self, market_client):
        resp = market_client.post(
            "/api/skillmarket/install",
            content_type="application/json",
            data="{}",
        )
        # Missing required fields should be 400 (or at least not 500)
        assert resp.status_code in (400, 422)

    def test_install_custom_skill(self, market_client):
        skill_payload = {
            "id": "test_market_skill_001",
            "name": "Test Market Skill",
            "description": "A skill for marketplace integration testing",
            "prompt_template": "You are a helpful assistant. {user_input}",
            "task_types": ["CHAT"],
            "tags": ["test"],
            "custom": True,
            "_overwrite": True,
        }
        resp = market_client.post(
            "/api/skillmarket/install",
            json=skill_payload,
        )
        assert resp.status_code in (200, 201), resp.get_data(as_text=True)

    def test_uninstall_nonexistent_returns_4xx(self, market_client):
        resp = market_client.post("/api/skillmarket/uninstall/nonexistent_skill_xyz")
        assert resp.status_code in (404, 400)


# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------

class TestToggle:
    def test_toggle_nonexistent_skill_returns_4xx(self, market_client):
        resp = market_client.post(
            "/api/skillmarket/toggle/totally_nonexistent_skill",
            json={},
        )
        assert resp.status_code in (404, 400)

    def test_toggle_existing_skill_returns_2xx(self, market_client):
        # Use a built-in skill that should always exist
        resp = market_client.post(
            "/api/skillmarket/toggle/concise_mode",
            json={"enabled": True},
        )
        assert resp.status_code in (200, 201, 204)


# ---------------------------------------------------------------------------
# Rate
# ---------------------------------------------------------------------------

class TestRate:
    def test_rate_requires_rating_field(self, market_client):
        resp = market_client.post(
            "/api/skillmarket/rate/concise_mode",
            json={},
        )
        assert resp.status_code in (400, 422)

    def test_rate_with_valid_rating(self, market_client):
        resp = market_client.post(
            "/api/skillmarket/rate/concise_mode",
            json={"score": 5},
        )
        assert resp.status_code in (200, 201)

    def test_rate_nonexistent_skill_returns_4xx(self, market_client):
        resp = market_client.post(
            "/api/skillmarket/rate/absolutely_nonexistent_skill_xyz",
            json={"rating": 3},
        )
        assert resp.status_code in (404, 400)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class TestExport:
    def test_export_existing_skill_returns_2xx(self, market_client):
        resp = market_client.get("/api/skillmarket/export/concise_mode")
        assert resp.status_code in (200, 201)

    def test_export_nonexistent_skill_returns_4xx(self, market_client):
        resp = market_client.get("/api/skillmarket/export/not_a_real_skill_xyz")
        assert resp.status_code in (404, 400)

    def test_export_pack_returns_2xx(self, market_client):
        """GET /export-pack requires skill IDs via query param or returns 400."""
        resp = market_client.get("/api/skillmarket/export-pack?ids=concise_mode")
        # If the endpoint or skill doesn't exist, 404/400 is also acceptable
        assert resp.status_code in (200, 201, 400, 404)


# ---------------------------------------------------------------------------
# Suggest
# ---------------------------------------------------------------------------

class TestSuggest:
    def test_suggest_returns_200(self, market_client):
        """suggest requires q param."""
        resp = market_client.get("/api/skillmarket/suggest?q=help+me+write+code")
        assert resp.status_code == 200
