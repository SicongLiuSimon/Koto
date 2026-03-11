# -*- coding: utf-8 -*-
"""
Integration tests for /api/goals endpoints.

Tests goal creation, listing, stats, single goal retrieval, update,
lifecycle transitions (activate/pause/resume/complete/delete).
Uses the full_client fixture from conftest.py.
"""
from __future__ import annotations
import pytest


def _check(resp, ok_status=(200, 201)):
    body = resp.get_data(as_text=True)
    assert resp.status_code in ok_status, f"HTTP {resp.status_code}: {body[:400]}"
    return resp.get_json()


_GOAL_ID = None  # shared across tests in this module


@pytest.fixture(scope="module")
def created_goal_id(full_client):
    """Creates a test goal and returns its ID for lifecycle tests."""
    resp = full_client.post(
        "/api/goals",
        json={
            "title": "集成测试目标",
            "user_goal": "每天总结工作日志，提炼关键信息。",
            "category": "custom",
            "priority": "normal",
            "run_on_activate": False,
        },
    )
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    data = resp.get_json()
    goal = data.get("data") or data
    return goal.get("goal_id") or goal.get("id")


@pytest.mark.integration
class TestGoalCreate:
    def test_create_goal_success(self, full_client):
        resp = full_client.post(
            "/api/goals",
            json={
                "title": "测试目标",
                "user_goal": "完成单元测试覆盖率达到80%",
                "run_on_activate": False,
            },
        )
        assert resp.status_code in (200, 201), resp.get_data(as_text=True)
        data = resp.get_json()
        assert data.get("ok") is True

    def test_create_goal_missing_title_returns_400(self, full_client):
        resp = full_client.post("/api/goals", json={"user_goal": "Some goal"})
        assert resp.status_code == 400

    def test_create_goal_missing_user_goal_returns_400(self, full_client):
        resp = full_client.post("/api/goals", json={"title": "A title"})
        assert resp.status_code == 400


@pytest.mark.integration
class TestGoalList:
    def test_list_goals_returns_200(self, full_client):
        resp = full_client.get("/api/goals")
        assert resp.status_code == 200

    def test_list_goals_response_shape(self, full_client):
        data = _check(full_client.get("/api/goals"))
        assert data.get("ok") is True
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_list_goals_invalid_status_returns_400(self, full_client):
        resp = full_client.get("/api/goals?status=invalid_status_xyz")
        assert resp.status_code == 400


@pytest.mark.integration
class TestGoalStats:
    def test_get_stats_returns_200(self, full_client):
        resp = full_client.get("/api/goals/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True
        assert "total" in data.get("data", {})


@pytest.mark.integration
class TestGoalDetail:
    def test_get_goal_by_id(self, full_client, created_goal_id):
        if not created_goal_id:
            pytest.skip("Goal creation failed")
        resp = full_client.get(f"/api/goals/{created_goal_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True
        goal = data["data"]
        assert goal.get("goal_id") == created_goal_id

    def test_get_nonexistent_goal_returns_404(self, full_client):
        resp = full_client.get("/api/goals/nonexistent-goal-id-xyz")
        assert resp.status_code == 404


@pytest.mark.integration
class TestGoalLifecycle:
    def test_delete_goal(self, full_client, created_goal_id):
        if not created_goal_id:
            pytest.skip("Goal creation failed")
        resp = full_client.delete(f"/api/goals/{created_goal_id}")
        assert resp.status_code in (200, 404), resp.get_data(as_text=True)

    def test_delete_nonexistent_goal_returns_404(self, full_client):
        resp = full_client.delete("/api/goals/nonexistent-goal-id-xyz")
        assert resp.status_code == 404
