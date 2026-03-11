"""Unit tests for LocalModelRouter.

All network calls to Ollama are mocked so tests run offline.
"""
from __future__ import annotations
import socket
import pytest


# ---------------------------------------------------------------------------
# Fixture: reset cached state between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_router():
    from app.core.routing.local_model_router import LocalModelRouter
    # Reset cached availability so each test starts fresh
    LocalModelRouter._available = None
    LocalModelRouter._check_time = 0
    yield
    LocalModelRouter._available = None
    LocalModelRouter._check_time = 0


# ---------------------------------------------------------------------------
# is_ollama_available()
# ---------------------------------------------------------------------------

class TestIsOllamaAvailable:
    def test_returns_false_when_socket_refused(self, mocker):
        from app.core.routing.local_model_router import LocalModelRouter
        mock_sock = mocker.MagicMock()
        mock_sock.connect_ex.return_value = 111  # ECONNREFUSED
        mocker.patch("socket.socket", return_value=mock_sock)
        assert LocalModelRouter.is_ollama_available() is False

    def test_returns_true_when_socket_connects(self, mocker):
        from app.core.routing.local_model_router import LocalModelRouter
        mock_sock = mocker.MagicMock()
        mock_sock.connect_ex.return_value = 0  # success
        mocker.patch("socket.socket", return_value=mock_sock)
        assert LocalModelRouter.is_ollama_available() is True

    def test_returns_false_on_socket_exception(self, mocker):
        from app.core.routing.local_model_router import LocalModelRouter
        mocker.patch("socket.socket", side_effect=OSError("network error"))
        assert LocalModelRouter.is_ollama_available() is False

    def test_returns_false_in_cloud_mode(self, monkeypatch):
        from app.core.routing.local_model_router import LocalModelRouter
        monkeypatch.setenv("KOTO_DEPLOY_MODE", "cloud")
        assert LocalModelRouter.is_ollama_available() is False
        monkeypatch.delenv("KOTO_DEPLOY_MODE")

    def test_caches_result_within_30_seconds(self, mocker):
        import time
        from app.core.routing.local_model_router import LocalModelRouter
        mock_sock = mocker.MagicMock()
        mock_sock.connect_ex.return_value = 0
        socket_mock = mocker.patch("socket.socket", return_value=mock_sock)
        LocalModelRouter.is_ollama_available()
        LocalModelRouter.is_ollama_available()
        # Socket should only be created once (second call uses cache)
        assert socket_mock.call_count == 1


# ---------------------------------------------------------------------------
# call_ollama_chat() – mocked HTTP
# ---------------------------------------------------------------------------

class TestCallOllamaChat:
    def _make_mock_response(self, mocker, content="hello"):
        mock_resp = mocker.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {"content": content}
        }
        return mock_resp

    def test_returns_content_on_success(self, mocker):
        from app.core.routing.local_model_router import LocalModelRouter
        LocalModelRouter._model_name = "qwen3:4b"
        mocker.patch(
            "app.core.routing.local_model_router.requests.post",
            return_value=self._make_mock_response(mocker, "CHAT"),
        )
        content, err = LocalModelRouter.call_ollama_chat(
            messages=[{"role": "user", "content": "hello"}],
            model_name="qwen3:4b",
        )
        assert err is None
        assert content == "CHAT"

    def test_returns_error_on_http_failure(self, mocker):
        from app.core.routing.local_model_router import LocalModelRouter
        LocalModelRouter._model_name = "qwen3:4b"
        mock_resp = mocker.MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mocker.patch("app.core.routing.local_model_router.requests.post", return_value=mock_resp)
        content, err = LocalModelRouter.call_ollama_chat(
            messages=[{"role": "user", "content": "test"}],
            model_name="qwen3:4b",
        )
        assert err is not None
        assert content == ""

    def test_returns_error_on_connection_error(self, mocker):
        from app.core.routing.local_model_router import LocalModelRouter
        import requests as req_lib
        LocalModelRouter._model_name = "qwen3:4b"
        mocker.patch(
            "app.core.routing.local_model_router.requests.post",
            side_effect=req_lib.ConnectionError("refused"),
        )
        content, err = LocalModelRouter.call_ollama_chat(
            messages=[{"role": "user", "content": "test"}],
            model_name="qwen3:4b",
        )
        assert err is not None
        assert content == ""

    def test_strips_think_blocks_from_qwen3(self, mocker):
        from app.core.routing.local_model_router import LocalModelRouter
        LocalModelRouter._model_name = "qwen3:4b"
        raw = "<think>reasoning here</think>CODER"
        mocker.patch(
            "app.core.routing.local_model_router.requests.post",
            return_value=self._make_mock_response(mocker, raw),
        )
        content, err = LocalModelRouter.call_ollama_chat(
            messages=[{"role": "user", "content": "write code"}],
            model_name="qwen3:4b",
            strip_think=True,
        )
        assert err is None
        assert "<think>" not in content


# ---------------------------------------------------------------------------
# route() – high-level routing
# ---------------------------------------------------------------------------

class TestRoute:
    def test_returns_tuple_of_three(self, mocker):
        from app.core.routing.local_model_router import LocalModelRouter
        mocker.patch.object(LocalModelRouter, "is_ollama_available", return_value=False)
        result = LocalModelRouter.classify("hello there")
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_fallback_returns_none_task_type_when_model_not_ready(self, mocker):
        """classify() returns (None, reason, source) when Ollama is unavailable."""
        from app.core.routing.local_model_router import LocalModelRouter
        mocker.patch.object(LocalModelRouter, "is_ollama_available", return_value=False)
        task_type, reason, source = LocalModelRouter.classify("hello")
        # Model not initialized → task_type is None, reason explains why
        assert task_type is None
        assert reason is not None
        assert source == "Local"

    def test_returns_string_tuple_on_successful_response(self, mocker):
        """When Ollama responds, classify() should return a valid task type string."""
        import json
        from app.core.routing.local_model_router import LocalModelRouter
        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"
        mock_resp = mocker.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {"content": json.dumps({"task": "CODER", "confidence": 0.9})}
        }
        mocker.patch("app.core.routing.local_model_router.requests.post", return_value=mock_resp)
        task_type, _, _ = LocalModelRouter.classify("fix this bug in my code")
        assert task_type in ("CODER", "CHAT", "AGENT", "RESEARCH", "WEB_SEARCH",
                             "PAINTER", "FILE_GEN", "DOC_ANNOTATE", "SYSTEM", "FILE_SEARCH")
        LocalModelRouter._initialized = False


# ---------------------------------------------------------------------------
# RouterDecision dataclass
# ---------------------------------------------------------------------------

class TestRouterDecision:
    def test_default_values(self):
        from app.core.routing.local_model_router import RouterDecision
        d = RouterDecision()
        assert d.task_type == "CHAT"
        assert d.forward_to_cloud is True
        assert d.confidence == 0.0

    def test_confidence_str_property(self):
        from app.core.routing.local_model_router import RouterDecision
        d = RouterDecision(task_type="CODER", confidence=0.85, latency_ms=42, source="Local")
        assert "Local" in d.confidence_str
        assert "0.85" in d.confidence_str

    def test_to_legacy_tuple(self):
        from app.core.routing.local_model_router import RouterDecision
        d = RouterDecision(task_type="RESEARCH", source="Fallback", confidence=0.5, latency_ms=10)
        t = d.to_legacy_tuple()
        assert t[0] == "RESEARCH"
        assert t[2] == "Fallback"


# ---------------------------------------------------------------------------
# Logging assertions
# ---------------------------------------------------------------------------

class TestRouterLogging:
    def test_no_error_logs_on_offline_classify(self, mocker, caplog):
        import logging
        from app.core.routing.local_model_router import LocalModelRouter
        mocker.patch.object(LocalModelRouter, "is_ollama_available", return_value=False)
        with caplog.at_level(logging.ERROR, logger="app.core.routing.local_model_router"):
            LocalModelRouter.classify("test input")
        error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_logs == []
