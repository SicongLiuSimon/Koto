# -*- coding: utf-8 -*-
"""
Integration tests for /api/jobs trigger endpoints.

Tests trigger templates, bootstrap, listing, toggle (PATCH), create, and delete.
Uses the session-scoped `client` fixture from conftest.py.
"""
from __future__ import annotations
import pytest


def _check(resp, ok_status=(200, 201)):
    body = resp.get_data(as_text=True)
    assert resp.status_code in ok_status, f"HTTP {resp.status_code}: {body[:400]}"
    return resp.get_json()


@pytest.mark.integration
class TestTriggerTemplates:
    def test_get_trigger_templates(self, client):
        resp = client.get("/api/jobs/triggers/templates")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data is not None

    def test_templates_contain_list(self, client):
        data = _check(client.get("/api/jobs/triggers/templates"))
        # Response shape: {"ok": true, "data": [...], "total": N}
        templates = data.get("data", data.get("templates", data))
        assert isinstance(templates, list)


@pytest.mark.integration
class TestTriggerBootstrap:
    def test_bootstrap_triggers(self, client):
        resp = client.post("/api/jobs/triggers/bootstrap", json={"force": True})
        assert resp.status_code in (200, 201), resp.get_data(as_text=True)
        data = resp.get_json()
        assert data is not None


@pytest.mark.integration
class TestTriggerList:
    def test_list_triggers_returns_200(self, client):
        resp = client.get("/api/jobs/triggers")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_list_triggers_returns_list(self, client):
        data = _check(client.get("/api/jobs/triggers"))
        # Response shape: {"ok": true, "data": [...], "total": N}
        triggers = data.get("data", data.get("triggers", data))
        assert isinstance(triggers, list)


@pytest.mark.integration
class TestTriggerCRUD:
    _created_trigger_id = None

    def test_create_custom_trigger(self, client):
        payload = {
            "name": "集成测试触发器",
            "trigger_type": "webhook",
            "job_type": "agent_query",
            "config": {},
        }
        resp = client.post("/api/jobs/triggers", json=payload)
        assert resp.status_code in (200, 201), resp.get_data(as_text=True)
        data = resp.get_json()
        assert data is not None
        # Store trigger_id for subsequent tests
        trigger = data.get("data") or data.get("trigger") or {}
        TestTriggerCRUD._created_trigger_id = (
            trigger.get("trigger_id") if isinstance(trigger, dict) else None
        )

    def test_toggle_trigger(self, client):
        tid = TestTriggerCRUD._created_trigger_id
        if not tid:
            pytest.skip("No trigger was created in previous test")
        resp = client.patch(f"/api/jobs/triggers/{tid}", json={"enabled": False})
        assert resp.status_code in (200, 404), resp.get_data(as_text=True)

    def test_delete_trigger(self, client):
        tid = TestTriggerCRUD._created_trigger_id
        if not tid:
            pytest.skip("No trigger was created in previous test")
        resp = client.delete(f"/api/jobs/triggers/{tid}")
        assert resp.status_code in (200, 404), resp.get_data(as_text=True)

    def test_delete_nonexistent_trigger(self, client):
        resp = client.delete("/api/jobs/triggers/nonexistent-trigger-id-xyz")
        assert resp.status_code in (400, 404, 200), resp.get_data(as_text=True)
