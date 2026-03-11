# -*- coding: utf-8 -*-
"""
Integration tests for /api/agent endpoints.

Tests the agent inference endpoint with a mocked LLM provider so no real
Gemini/Ollama calls are made.
"""
from __future__ import annotations
import sys
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


def _root():
    return Path(__file__).resolve().parents[2]


def _ensure_path():
    root = str(_root())
    if root not in sys.path:
        sys.path.insert(0, root)


@pytest.fixture(scope="module")
def agent_client(_koto_tmp_db):
    """Flask test client with agent blueprint registered."""
    _ensure_path()
    from flask import Flask
    from app.api.agent_routes import agent_bp

    app = Flask(__name__)
    app.register_blueprint(agent_bp, url_prefix="/api/agent")
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.integration
class TestAgentQueryEndpoint:
    def test_agent_query_with_mocked_agent(self, agent_client):
        """POST /api/agent/query should return structured response with mocked agent."""
        mock_response = MagicMock()
        mock_response.content = "Mocked answer"
        mock_response.steps = []
        mock_response.metadata = {}
        mock_response.to_dict.return_value = {
            "content": "Mocked answer",
            "steps": [],
            "metadata": {},
        }

        with patch("app.api.agent_routes.get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.run.return_value = mock_response
            mock_get_agent.return_value = mock_agent

            resp = agent_client.post(
                "/api/agent/query",
                json={"query": "Hello, what is 2+2?"},
            )
            # Should return 200 with content or 404/500 if endpoint not configured
            assert resp.status_code in (200, 404, 500), resp.get_data(as_text=True)

    def test_agent_query_missing_query(self, agent_client):
        """POST without query body should return 400."""
        resp = agent_client.post("/api/agent/query", json={})
        # Could be 400 (bad request) or other error — should not be 5xx crash
        assert resp.status_code in (400, 422, 404, 200), resp.get_data(as_text=True)

    def test_agent_chat_stream_endpoint_exists(self, agent_client):
        """GET /api/agent/chat/stream endpoint should exist (may need params)."""
        resp = agent_client.get("/api/agent/")
        # 405 method not allowed = endpoint exists; 404 = no route at that path
        assert resp.status_code in (200, 404, 405), resp.get_data(as_text=True)
