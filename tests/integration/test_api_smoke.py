# -*- coding: utf-8 -*-
"""
API smoke tests — verify every /api/* endpoint responds without 500 errors.

Uses the full_client fixture (all blueprints registered) from conftest.py.
Endpoints that are not registered in the test app will return 404, which is
acceptable for smoke testing purposes.
"""

from __future__ import annotations

import pytest

# Valid non-error status codes for smoke tests.
# 200 = OK, 201 = Created, 204 = No Content,
# 401/403 = auth required (expected in test env),
# 404 = endpoint not registered in test app or resource missing,
# 405 = method not allowed (route exists but wrong HTTP verb),
# 503 = service unavailable (health probes when deps missing).
_OK = (200, 201, 204, 301, 302, 400, 401, 403, 404, 405, 422, 503)


def _smoke(resp):
    """Assert the response is NOT a 500 Internal Server Error."""
    body = resp.get_data(as_text=True)
    assert resp.status_code != 500, f"Got 500 on {resp.request.path}: {body[:500]}"
    assert (
        resp.status_code in _OK
    ), f"Unexpected status {resp.status_code} on {resp.request.path}: {body[:300]}"
    return resp


# ── Memory endpoints ─────────────────────────────────────────────────────────


@pytest.mark.integration
class TestMemorySmoke:
    """Smoke tests for /api/memories and /api/memory/* endpoints."""

    def test_memory_list(self, full_client):
        _smoke(full_client.get("/api/memories"))

    def test_memory_create(self, full_client):
        _smoke(full_client.post("/api/memories", json={"content": "test memory"}))

    def test_memory_create_with_tags(self, full_client):
        _smoke(
            full_client.post(
                "/api/memories",
                json={"content": "tagged memory", "tags": ["test"]},
            )
        )

    def test_memory_profile(self, full_client):
        _smoke(full_client.get("/api/memory/profile"))

    def test_memory_stats(self, full_client):
        _smoke(full_client.get("/api/memory/stats"))

    def test_memory_personality(self, full_client):
        _smoke(full_client.get("/api/memory/personality"))

    def test_memory_auto_learn(self, full_client):
        _smoke(full_client.post("/api/memory/auto-learn", json={}))

    def test_memory_profile_update(self, full_client):
        _smoke(full_client.post("/api/memory/profile", json={"name": "test"}))


# ── Macro endpoints ──────────────────────────────────────────────────────────


@pytest.mark.integration
class TestMacroSmoke:
    """Smoke tests for /api/macro/* endpoints."""

    def test_macro_pending(self, full_client):
        _smoke(full_client.get("/api/macro/pending"))

    def test_macro_history(self, full_client):
        _smoke(full_client.get("/api/macro/history"))

    def test_macro_dismiss_nonexistent(self, full_client):
        _smoke(full_client.post("/api/macro/dismiss/nonexistent-id"))

    def test_macro_confirm_nonexistent(self, full_client):
        _smoke(full_client.post("/api/macro/confirm/nonexistent-id"))


# ── Setup / Config endpoints ─────────────────────────────────────────────────


@pytest.mark.integration
class TestSetupSmoke:
    """Smoke tests for /api/setup/* and /api/diagnose endpoints."""

    def test_setup_test(self, full_client):
        _smoke(full_client.get("/api/setup/test"))

    def test_setup_status(self, full_client):
        _smoke(full_client.get("/api/setup/status"))

    def test_diagnose(self, full_client):
        _smoke(full_client.get("/api/diagnose"))


# ── Utility endpoints ────────────────────────────────────────────────────────


@pytest.mark.integration
class TestUtilitySmoke:
    """Smoke tests for /api/browse, /api/ping, /api/info."""

    def test_browse(self, full_client):
        _smoke(full_client.get("/api/browse"))

    def test_ping(self, full_client):
        _smoke(full_client.get("/api/ping"))

    def test_info(self, full_client):
        _smoke(full_client.get("/api/info"))


# ── Mini chat ────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestMiniChatSmoke:
    """Smoke tests for /api/mini/chat endpoint."""

    def test_mini_chat_basic(self, full_client):
        _smoke(full_client.post("/api/mini/chat", json={"message": "hello"}))

    def test_mini_chat_empty(self, full_client):
        _smoke(full_client.post("/api/mini/chat", json={}))


# ── Notebook endpoints ───────────────────────────────────────────────────────


@pytest.mark.integration
class TestNotebookSmoke:
    """Smoke tests for /api/notebook/* endpoints."""

    def test_notebook_overview(self, full_client):
        _smoke(full_client.post("/api/notebook/overview", json={}))

    def test_notebook_qa(self, full_client):
        _smoke(full_client.post("/api/notebook/qa", json={"question": "test"}))

    def test_notebook_study_guide(self, full_client):
        _smoke(full_client.post("/api/notebook/study_guide", json={}))


# ── Voice endpoints ──────────────────────────────────────────────────────────


@pytest.mark.integration
class TestVoiceSmoke:
    """Smoke tests for /api/voice/* endpoints."""

    def test_voice_engines(self, full_client):
        _smoke(full_client.get("/api/voice/engines"))

    def test_voice_stt_status(self, full_client):
        _smoke(full_client.get("/api/voice/stt_status"))

    def test_voice_commands(self, full_client):
        _smoke(full_client.get("/api/voice/commands"))


# ── Document endpoints ───────────────────────────────────────────────────────


@pytest.mark.integration
class TestDocumentSmoke:
    """Smoke tests for /api/document/* endpoints."""

    def test_document_analyze(self, full_client):
        _smoke(full_client.post("/api/document/analyze", json={"text": "test"}))

    def test_document_smart_process(self, full_client):
        _smoke(
            full_client.post(
                "/api/document/smart-process",
                json={"text": "test"},
            )
        )


# ── Operations endpoints ─────────────────────────────────────────────────────


@pytest.mark.integration
class TestOpsSmoke:
    """Smoke tests for /api/ops/* endpoints (supplement to test_ops_routes.py)."""

    def test_ops_health(self, full_client):
        resp = full_client.get("/api/ops/health")
        assert resp.status_code in (200, 503)

    def test_ops_readiness(self, full_client):
        resp = full_client.get("/api/ops/readiness")
        assert resp.status_code in (200, 503)

    def test_ops_metrics(self, full_client):
        _smoke(full_client.get("/api/ops/metrics"))

    def test_ops_incidents(self, full_client):
        _smoke(full_client.get("/api/ops/incidents"))


# ── Shadow watcher endpoints ─────────────────────────────────────────────────


@pytest.mark.integration
class TestShadowSmoke:
    """Smoke tests for /api/shadow/* endpoints."""

    def test_shadow_status(self, full_client):
        _smoke(full_client.get("/api/shadow/status"))

    def test_shadow_patterns(self, full_client):
        _smoke(full_client.get("/api/shadow/patterns"))

    def test_shadow_insights(self, full_client):
        _smoke(full_client.get("/api/shadow/insights"))
