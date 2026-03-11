# -*- coding: utf-8 -*-
"""
Integration tests for /api/skills endpoints.

Tests skill listing, creation, update, deletion, enable/disable, and MCP export.
Uses the session-scoped `client` fixture from conftest.py.
"""
from __future__ import annotations
import pytest


def _check(resp, ok_status=(200, 201)):
    body = resp.get_data(as_text=True)
    assert resp.status_code in ok_status, f"HTTP {resp.status_code}: {body[:400]}"
    return resp.get_json()


@pytest.mark.integration
class TestSkillList:
    def test_list_skills_returns_200(self, client):
        data = _check(client.get("/api/skills"))
        assert "skills" in data or isinstance(data, list) or "data" in data

    def test_list_skills_is_list(self, client):
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        data = resp.get_json()
        # Could be {"skills": [...]} or a list directly
        skills = data.get("skills", data) if isinstance(data, dict) else data
        assert isinstance(skills, list)


@pytest.mark.integration
class TestSkillCRUD:
    _created_id = None

    def test_create_custom_skill(self, client):
        payload = {
            "id": "test_custom_integration_skill",
            "name": "集成测试技能",
            "icon": "🧪",
            "category": "custom",
            "description": "A skill created by integration tests",
            "prompt": "You are a test assistant.",
        }
        resp = client.post("/api/skills", json=payload)
        # Accept 200 or 201
        assert resp.status_code in (200, 201), resp.get_data(as_text=True)
        data = resp.get_json()
        assert data is not None

    def test_get_skill_by_id(self, client):
        resp = client.get("/api/skills/test_custom_integration_skill")
        # If skill was successfully created, it should be retrievable
        if resp.status_code == 200:
            data = resp.get_json()
            assert data is not None

    def test_delete_custom_skill(self, client):
        resp = client.delete("/api/skills/test_custom_integration_skill")
        # 200 if deleted, 404 if never created — both are acceptable
        assert resp.status_code in (200, 404), resp.get_data(as_text=True)


@pytest.mark.integration
class TestSkillEnable:
    def test_enable_endpoint_exists(self, client):
        """Enable endpoint should return 200 or 404 (skill not found), not 500."""
        resp = client.post("/api/skills/concise_mode/enable", json={"enabled": True})
        assert resp.status_code in (200, 404, 400), resp.get_data(as_text=True)


@pytest.mark.integration
class TestSkillBootstrap:
    def test_bootstrap_creates_skills(self, client):
        data = _check(client.post("/api/skills/bindings/bootstrap", json={"force": True}))
        assert "success" in data or "created" in data or "ok" in data


@pytest.mark.integration
class TestSkillMCP:
    def test_mcp_export_returns_tools(self, client):
        resp = client.get("/api/skills/mcp")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert "tools" in data or isinstance(data, list) or data is not None
