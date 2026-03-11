# -*- coding: utf-8 -*-
"""
Integration tests for /api/ops endpoints.

Tests health snapshot, readiness probe, metrics, incidents listing,
triggers status, and GC endpoint.
Uses the full_client fixture from conftest.py.
"""
from __future__ import annotations
import pytest


def _check(resp, ok_status=(200, 201)):
    body = resp.get_data(as_text=True)
    assert resp.status_code in ok_status, f"HTTP {resp.status_code}: {body[:400]}"
    return resp.get_json()


@pytest.mark.integration
class TestOpsHealth:
    def test_health_endpoint_returns_200_or_503(self, full_client):
        resp = full_client.get("/api/ops/health")
        # Healthy → 200; unhealthy → 503 — both valid responses (not 500)
        assert resp.status_code in (200, 503), resp.get_data(as_text=True)

    def test_health_response_has_status_field(self, full_client):
        resp = full_client.get("/api/ops/health")
        data = resp.get_json()
        assert data is not None
        assert "status" in data
        assert data["status"] in ("healthy", "degraded", "unhealthy")


@pytest.mark.integration
class TestOpsReadiness:
    def test_readiness_probe_returns_200_or_503(self, full_client):
        resp = full_client.get("/api/ops/readiness")
        assert resp.status_code in (200, 503), resp.get_data(as_text=True)

    def test_readiness_response_has_ready_field(self, full_client):
        resp = full_client.get("/api/ops/readiness")
        data = resp.get_json()
        assert "ready" in data
        assert isinstance(data["ready"], bool)
        assert "timestamp" in data


@pytest.mark.integration
class TestOpsMetrics:
    def test_metrics_returns_200(self, full_client):
        resp = full_client.get("/api/ops/metrics")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_metrics_contains_expected_keys(self, full_client):
        data = _check(full_client.get("/api/ops/metrics"))
        assert "timestamp" in data
        # jobs and/or triggers stats may be present
        assert "jobs" in data or "triggers" in data or "ops_events" in data


@pytest.mark.integration
class TestOpsIncidents:
    def test_incidents_returns_200(self, full_client):
        resp = full_client.get("/api/ops/incidents")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_incidents_response_shape(self, full_client):
        data = _check(full_client.get("/api/ops/incidents"))
        assert "events" in data
        assert isinstance(data["events"], list)
        assert "count" in data

    def test_incidents_with_filter_params(self, full_client):
        resp = full_client.get("/api/ops/incidents?n=5&severity=info")
        assert resp.status_code == 200


@pytest.mark.integration
class TestOpsTriggersStatus:
    def test_triggers_status_returns_200(self, full_client):
        resp = full_client.get("/api/ops/triggers/status")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_triggers_status_response_shape(self, full_client):
        data = _check(full_client.get("/api/ops/triggers/status"))
        assert "triggers" in data
        assert isinstance(data["triggers"], list)
        assert "trigger_count" in data


@pytest.mark.integration
class TestOpsGC:
    def test_manual_gc_returns_200(self, full_client):
        resp = full_client.post("/api/ops/gc")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_manual_gc_response_has_collected_objects(self, full_client):
        data = _check(full_client.post("/api/ops/gc"))
        assert "collected_objects" in data
        assert isinstance(data["collected_objects"], int)
        assert "timestamp" in data
