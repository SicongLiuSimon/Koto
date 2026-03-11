# -*- coding: utf-8 -*-
"""
Integration tests for /api/tasks endpoints.

Tests task listing, stats, individual task retrieval, cancel, and delete.
Uses the full_client fixture from conftest.py (all blueprints registered).
"""
from __future__ import annotations
import pytest


def _check(resp, ok_status=(200, 201)):
    body = resp.get_data(as_text=True)
    assert resp.status_code in ok_status, f"HTTP {resp.status_code}: {body[:400]}"
    return resp.get_json()


@pytest.mark.integration
class TestTaskList:
    def test_list_tasks_returns_200(self, full_client):
        resp = full_client.get("/api/tasks")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_list_tasks_response_shape(self, full_client):
        data = _check(full_client.get("/api/tasks"))
        assert data.get("ok") is True
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_list_tasks_pagination_params(self, full_client):
        resp = full_client.get("/api/tasks?limit=5&offset=0")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "limit" in data
        assert data["limit"] == 5

    def test_list_tasks_invalid_status_returns_400(self, full_client):
        resp = full_client.get("/api/tasks?status=not_a_real_status")
        assert resp.status_code == 400


@pytest.mark.integration
class TestTaskStats:
    def test_get_stats_returns_200(self, full_client):
        resp = full_client.get("/api/tasks/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True

    def test_get_stats_has_data(self, full_client):
        data = _check(full_client.get("/api/tasks/stats"))
        assert "data" in data


@pytest.mark.integration
class TestTaskNotFound:
    def test_get_nonexistent_task_returns_404(self, full_client):
        resp = full_client.get("/api/tasks/nonexistent-task-id-xyz-123")
        assert resp.status_code == 404

    def test_cancel_nonexistent_task_returns_404(self, full_client):
        resp = full_client.post("/api/tasks/nonexistent-task-id-xyz/cancel")
        assert resp.status_code == 404

    def test_delete_nonexistent_task_returns_404(self, full_client):
        resp = full_client.delete("/api/tasks/nonexistent-task-id-xyz")
        assert resp.status_code == 404


@pytest.mark.integration
class TestTaskPurge:
    def test_purge_tasks_returns_200(self, full_client):
        resp = full_client.post("/api/tasks/purge", json={"keep_days": 30})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True
        assert "data" in data
        assert "deleted" in data["data"]
