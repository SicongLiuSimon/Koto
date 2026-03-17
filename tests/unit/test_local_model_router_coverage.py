"""Extended coverage tests for LocalModelRouter.

All network calls are mocked so tests run offline.
This file supplements the existing test_local_model_router.py with
additional edge-case and branch-coverage tests.
"""

from __future__ import annotations

import json
import socket
import time

import pytest
import requests

from unittest.mock import patch, MagicMock, Mock, PropertyMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_router():
    """Reset all class-level cached state on LocalModelRouter."""
    from app.core.routing.local_model_router import LocalModelRouter

    LocalModelRouter._available = None
    LocalModelRouter._check_time = 0
    LocalModelRouter._initialized = False
    LocalModelRouter._model_name = None
    LocalModelRouter._response_model = None
    LocalModelRouter._response_model_inited = False


def _mock_post_response(content, status_code=200):
    """Build a MagicMock that behaves like requests.Response for a POST."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = content if isinstance(content, str) else json.dumps(content)
    resp.json.return_value = (
        {"message": {"content": content}} if isinstance(content, str) else content
    )
    return resp


def _mock_tags_response(model_names: list[str], status_code=200):
    """Build a MagicMock for GET /api/tags."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"models": [{"name": n} for n in model_names]}
    return resp


# ═══════════════════════════════════════════════════════════════════
# RouterDecision dataclass
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestRouterDecisionCoverage:
    def test_defaults(self):
        from app.core.routing.local_model_router import RouterDecision

        d = RouterDecision()
        assert d.task_type == "CHAT"
        assert d.skill_id is None
        assert d.forward_to_cloud is True
        assert d.confidence == 0.0
        assert d.hint is None
        assert d.source == "Local"
        assert d.latency_ms == 0
        assert d.params == {}

    def test_custom_values(self):
        from app.core.routing.local_model_router import RouterDecision

        d = RouterDecision(
            task_type="CODER",
            skill_id="my-skill",
            forward_to_cloud=False,
            confidence=0.95,
            hint="write clean code",
            source="Cache",
            latency_ms=150,
            params={"key": "value"},
        )
        assert d.task_type == "CODER"
        assert d.skill_id == "my-skill"
        assert d.forward_to_cloud is False
        assert d.confidence == 0.95
        assert d.hint == "write clean code"

    def test_confidence_str_format(self):
        from app.core.routing.local_model_router import RouterDecision

        d = RouterDecision(source="Local", confidence=0.85, latency_ms=42)
        s = d.confidence_str
        assert "Local" in s
        assert "0.85" in s
        assert "42ms" in s

    def test_confidence_str_zero(self):
        from app.core.routing.local_model_router import RouterDecision

        d = RouterDecision(source="Fallback", confidence=0.0, latency_ms=0)
        s = d.confidence_str
        assert "Fallback" in s
        assert "0.00" in s

    def test_to_legacy_tuple_structure(self):
        from app.core.routing.local_model_router import RouterDecision

        d = RouterDecision(
            task_type="RESEARCH", source="Local", confidence=0.75, latency_ms=100
        )
        t = d.to_legacy_tuple()
        assert isinstance(t, tuple)
        assert len(t) == 3
        assert t[0] == "RESEARCH"
        assert t[2] == "Local"
        assert "0.75" in t[1]

    def test_to_legacy_tuple_preserves_source(self):
        from app.core.routing.local_model_router import RouterDecision

        d = RouterDecision(
            task_type="CHAT", source="Cache", confidence=0.5, latency_ms=10
        )
        assert d.to_legacy_tuple()[2] == "Cache"


# ═══════════════════════════════════════════════════════════════════
# LocalModelRouter methods
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestLocalModelRouterCoverage:
    def setup_method(self):
        _reset_router()

    def teardown_method(self):
        _reset_router()

    # ── is_ollama_available ────────────────────────────────────────

    @patch("app.core.routing.local_model_router.socket.socket")
    def test_is_ollama_available_success(self, mock_socket_cls):
        from app.core.routing.local_model_router import LocalModelRouter

        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value = mock_sock

        assert LocalModelRouter.is_ollama_available() is True
        mock_sock.close.assert_called_once()

    @patch("app.core.routing.local_model_router.socket.socket")
    def test_is_ollama_available_refused(self, mock_socket_cls):
        from app.core.routing.local_model_router import LocalModelRouter

        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 111  # ECONNREFUSED
        mock_socket_cls.return_value = mock_sock

        assert LocalModelRouter.is_ollama_available() is False

    @patch("app.core.routing.local_model_router.socket.socket")
    def test_is_ollama_available_caching_within_30s(self, mock_socket_cls):
        from app.core.routing.local_model_router import LocalModelRouter

        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value = mock_sock

        # First call — socket is created
        assert LocalModelRouter.is_ollama_available() is True
        # Second call — should use cache
        assert LocalModelRouter.is_ollama_available() is True
        assert mock_socket_cls.call_count == 1

    @patch("app.core.routing.local_model_router.time.time")
    @patch("app.core.routing.local_model_router.socket.socket")
    def test_is_ollama_available_cache_expired(self, mock_socket_cls, mock_time):
        from app.core.routing.local_model_router import LocalModelRouter

        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value = mock_sock

        # First call at t=1000
        mock_time.return_value = 1000.0
        assert LocalModelRouter.is_ollama_available() is True

        # Second call at t=1031 (>30s later) — cache expired
        mock_time.return_value = 1031.0
        assert LocalModelRouter.is_ollama_available() is True
        assert mock_socket_cls.call_count == 2

    @patch(
        "app.core.routing.local_model_router.socket.socket", side_effect=OSError("fail")
    )
    def test_is_ollama_available_exception(self, _mock):
        from app.core.routing.local_model_router import LocalModelRouter

        assert LocalModelRouter.is_ollama_available() is False

    @patch.dict("os.environ", {"KOTO_DEPLOY_MODE": "cloud"})
    def test_is_ollama_available_cloud_mode(self):
        from app.core.routing.local_model_router import LocalModelRouter

        assert LocalModelRouter.is_ollama_available() is False

    # ── init_model ─────────────────────────────────────────────────

    @patch("app.core.routing.local_model_router.requests.get")
    @patch.object(
        __import__(
            "app.core.routing.local_model_router", fromlist=["LocalModelRouter"]
        ).LocalModelRouter,
        "is_ollama_available",
        return_value=True,
    )
    def test_init_model_success(self, _mock_avail, mock_get):
        from app.core.routing.local_model_router import LocalModelRouter

        mock_get.return_value = _mock_tags_response(["qwen3:4b", "llama3.2:3b"])
        result = LocalModelRouter.init_model()

        assert result is True
        assert LocalModelRouter._initialized is True
        assert LocalModelRouter._model_name is not None

    @patch.object(
        __import__(
            "app.core.routing.local_model_router", fromlist=["LocalModelRouter"]
        ).LocalModelRouter,
        "is_ollama_available",
        return_value=False,
    )
    def test_init_model_ollama_unavailable(self, _mock_avail):
        from app.core.routing.local_model_router import LocalModelRouter

        result = LocalModelRouter.init_model()
        assert result is False
        assert LocalModelRouter._initialized is False

    @patch("app.core.routing.local_model_router.requests.get")
    @patch.object(
        __import__(
            "app.core.routing.local_model_router", fromlist=["LocalModelRouter"]
        ).LocalModelRouter,
        "is_ollama_available",
        return_value=True,
    )
    def test_init_model_api_failure(self, _mock_avail, mock_get):
        from app.core.routing.local_model_router import LocalModelRouter

        mock_get.return_value = _mock_tags_response([], status_code=500)
        result = LocalModelRouter.init_model()
        assert result is False

    @patch("app.core.routing.local_model_router.requests.get")
    @patch.object(
        __import__(
            "app.core.routing.local_model_router", fromlist=["LocalModelRouter"]
        ).LocalModelRouter,
        "is_ollama_available",
        return_value=True,
    )
    def test_init_model_no_models(self, _mock_avail, mock_get):
        from app.core.routing.local_model_router import LocalModelRouter

        mock_get.return_value = _mock_tags_response([])
        result = LocalModelRouter.init_model()
        assert result is False

    @patch(
        "app.core.routing.local_model_router.requests.get",
        side_effect=Exception("network error"),
    )
    @patch.object(
        __import__(
            "app.core.routing.local_model_router", fromlist=["LocalModelRouter"]
        ).LocalModelRouter,
        "is_ollama_available",
        return_value=True,
    )
    def test_init_model_request_exception(self, _mock_avail, mock_get):
        from app.core.routing.local_model_router import LocalModelRouter

        result = LocalModelRouter.init_model()
        assert result is False

    def test_init_model_already_initialized(self):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"
        result = LocalModelRouter.init_model()
        assert result is True

    @patch("app.core.routing.local_model_router.requests.get")
    @patch.object(
        __import__(
            "app.core.routing.local_model_router", fromlist=["LocalModelRouter"]
        ).LocalModelRouter,
        "is_ollama_available",
        return_value=True,
    )
    def test_init_model_with_explicit_name(self, _mock_avail, mock_get):
        from app.core.routing.local_model_router import LocalModelRouter

        mock_get.return_value = _mock_tags_response(["custom-model:latest"])
        # Model name not in priority list but explicitly given
        result = LocalModelRouter.init_model("custom-model:latest")
        # The method checks installed list, custom-model won't match OLLAMA_MODELS
        # but model_name was given explicitly — depends on implementation
        # It will look for it in installed list; since "custom-model" isn't in
        # OLLAMA_MODELS, it won't find a target and returns False unless
        # model_name param is used directly. Let's check behavior.
        # Looking at source: target_model = model_name, if model_name given
        # it's used directly.
        # Wait — re-reading init_model: target_model = model_name if given
        # Actually not — `target_model = model_name` then `if not target_model`
        # tries to find one. If model_name is passed, target_model is set.
        # But it still needs installed list. Let me re-read...
        # The installed list is fetched first. If the fetch works, then
        # `target_model = model_name` (the explicit param). Since it's not None,
        # it skips the search. Then checks `if not target_model:` → False.
        # So it should succeed.
        assert result is True
        assert LocalModelRouter._model_name == "custom-model:latest"

    @patch("app.core.routing.local_model_router.requests.get")
    @patch.object(
        __import__(
            "app.core.routing.local_model_router", fromlist=["LocalModelRouter"]
        ).LocalModelRouter,
        "is_ollama_available",
        return_value=True,
    )
    def test_init_model_no_matching_priority(self, _mock_avail, mock_get):
        from app.core.routing.local_model_router import LocalModelRouter

        # Installed models don't match any OLLAMA_MODELS priority list
        mock_get.return_value = _mock_tags_response(["unknown-model:latest"])
        result = LocalModelRouter.init_model()
        assert result is False

    # ── call_ollama_chat ───────────────────────────────────────────

    @patch("app.core.routing.local_model_router.requests.post")
    def test_call_ollama_chat_success(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._model_name = "qwen3:4b"
        mock_post.return_value = _mock_post_response("Hello world!")

        content, err = LocalModelRouter.call_ollama_chat(
            messages=[{"role": "user", "content": "hi"}],
            model_name="qwen3:4b",
        )
        assert err is None
        assert content == "Hello world!"

    @patch("app.core.routing.local_model_router.requests.post")
    def test_call_ollama_chat_http_error(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._model_name = "qwen3:4b"
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "Internal Server Error"
        mock_post.return_value = resp

        content, err = LocalModelRouter.call_ollama_chat(
            messages=[{"role": "user", "content": "test"}],
            model_name="qwen3:4b",
        )
        assert content == ""
        assert "500" in err

    @patch("app.core.routing.local_model_router.requests.post")
    def test_call_ollama_chat_timeout(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._model_name = "qwen3:4b"
        mock_post.side_effect = requests.exceptions.Timeout("timed out")

        content, err = LocalModelRouter.call_ollama_chat(
            messages=[{"role": "user", "content": "test"}],
            model_name="qwen3:4b",
            timeout=2.0,
        )
        assert content == ""
        assert "超时" in err or "2" in err

    @patch("app.core.routing.local_model_router.requests.post")
    def test_call_ollama_chat_connection_error(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._model_name = "qwen3:4b"
        mock_post.side_effect = requests.ConnectionError("refused")

        content, err = LocalModelRouter.call_ollama_chat(
            messages=[{"role": "user", "content": "test"}],
            model_name="qwen3:4b",
        )
        assert content == ""
        assert err is not None

    @patch("app.core.routing.local_model_router.requests.post")
    def test_call_ollama_chat_strip_think(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._model_name = "qwen3:4b"
        raw = "<think>internal reasoning</think>The actual answer"
        mock_post.return_value = _mock_post_response(raw)

        content, err = LocalModelRouter.call_ollama_chat(
            messages=[{"role": "user", "content": "question"}],
            model_name="qwen3:4b",
            strip_think=True,
        )
        assert err is None
        assert "<think>" not in content
        assert "The actual answer" in content

    @patch("app.core.routing.local_model_router.requests.post")
    def test_call_ollama_chat_no_strip_think(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._model_name = "qwen3:4b"
        raw = "<think>reasoning</think>Answer"
        mock_post.return_value = _mock_post_response(raw)

        content, err = LocalModelRouter.call_ollama_chat(
            messages=[{"role": "user", "content": "q"}],
            model_name="qwen3:4b",
            strip_think=False,
        )
        assert err is None
        assert "<think>" in content

    def test_call_ollama_chat_no_model(self):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._model_name = None
        content, err = LocalModelRouter.call_ollama_chat(
            messages=[{"role": "user", "content": "test"}],
        )
        assert content == ""
        assert err is not None

    @patch("app.core.routing.local_model_router.requests.post")
    def test_call_ollama_chat_with_format_and_options(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._model_name = "qwen3:4b"
        mock_post.return_value = _mock_post_response('{"result": "ok"}')

        content, err = LocalModelRouter.call_ollama_chat(
            messages=[{"role": "user", "content": "json test"}],
            model_name="qwen3:4b",
            fmt="json",
            options={"temperature": 0.0},
        )
        assert err is None
        # Verify payload included format and options
        call_kwargs = mock_post.call_args
        payload = (
            call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs[0][0]
        )
        assert payload.get("format") == "json"
        assert payload.get("options") == {"temperature": 0.0}

    @patch("app.core.routing.local_model_router.requests.post")
    def test_call_ollama_chat_empty_response_fallback(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._model_name = "qwen3:4b"
        # message.content is empty, but "response" field has data
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "message": {"content": ""},
            "response": "fallback text",
        }
        mock_post.return_value = resp

        content, err = LocalModelRouter.call_ollama_chat(
            messages=[{"role": "user", "content": "test"}],
            model_name="qwen3:4b",
        )
        assert err is None
        assert content == "fallback text"

    # ── classify ───────────────────────────────────────────────────

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_with_valid_response(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "message": {"content": json.dumps({"task": "CODER", "confidence": 0.9})}
        }
        mock_post.return_value = resp

        task, conf_str, source = LocalModelRouter.classify("write a sort function")
        assert task == "CODER"
        assert "Local" in conf_str or source == "Local"

    def test_classify_ollama_unavailable(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=False):
            task, reason, source = LocalModelRouter.classify("hello")
        assert task is None
        assert "ModelNotReady" in reason
        assert source == "Local"

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_api_http_error(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        resp = MagicMock()
        resp.status_code = 503
        mock_post.return_value = resp

        task, reason, source = LocalModelRouter.classify("test")
        assert task is None
        assert "503" in reason

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_timeout(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"
        mock_post.side_effect = requests.exceptions.Timeout("timed out")

        task, reason, source = LocalModelRouter.classify("test", timeout=1.0)
        assert task is None
        assert "Timeout" in reason

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_generic_exception(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"
        mock_post.side_effect = RuntimeError("unexpected")

        task, reason, source = LocalModelRouter.classify("test")
        assert task is None
        assert "Error" in reason

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_text_fallback_parsing(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        # Return non-JSON text containing a valid task type
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "message": {
                "content": "I think this is a PAINTER task with high confidence"
            }
        }
        mock_post.return_value = resp

        task, conf_str, source = LocalModelRouter.classify("draw a cat")
        assert task == "PAINTER"

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_alias_mapping(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        # Return JSON with alias task type "CODE" → should map to "CODER"
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "message": {"content": json.dumps({"task": "CODE", "confidence": 0.85})}
        }
        mock_post.return_value = resp

        task, conf_str, source = LocalModelRouter.classify("write code")
        assert task == "CODER"

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_low_confidence_returns_none(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        # Confidence below threshold (0.45)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "message": {"content": json.dumps({"task": "CHAT", "confidence": 0.2})}
        }
        mock_post.return_value = resp

        task, reason, source = LocalModelRouter.classify("hmm")
        assert task is None
        assert "ParseError" in reason

    # ── classify_with_hint ─────────────────────────────────────────

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_with_hint_valid_json(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        payload = json.dumps(
            {
                "task": "WEB_SEARCH",
                "confidence": 0.92,
                "hint": "show weather forecast table",
                "complexity": "normal",
            }
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"message": {"content": payload}}
        mock_post.return_value = resp

        result = LocalModelRouter.classify_with_hint("what's the weather today")
        assert len(result) == 5
        task, conf_str, source, hint, complexity = result
        assert task == "WEB_SEARCH"
        assert hint == "show weather forecast table"
        assert complexity == "normal"

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_with_hint_complex_task(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        payload = json.dumps(
            {
                "task": "RESEARCH",
                "confidence": 0.88,
                "hint": "deep analysis required",
                "complexity": "complex",
            }
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"message": {"content": payload}}
        mock_post.return_value = resp

        task, _, _, hint, complexity = LocalModelRouter.classify_with_hint(
            "deep research on AI"
        )
        assert task == "RESEARCH"
        assert complexity == "complex"

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_with_hint_text_fallback(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        # First call for classify_with_hint returns non-parseable text containing a task
        # Second call will be the fallback classify() call
        resp_hint = MagicMock()
        resp_hint.status_code = 200
        resp_hint.json.return_value = {
            "message": {"content": "This looks like a CHAT task to me"}
        }
        # classify_with_hint falls through to text parsing if JSON fails
        # If confidence < 0.45 it falls back to classify()
        resp_classify = MagicMock()
        resp_classify.status_code = 200
        resp_classify.json.return_value = {
            "message": {"content": json.dumps({"task": "CHAT", "confidence": 0.8})}
        }
        mock_post.side_effect = [resp_hint, resp_classify]

        result = LocalModelRouter.classify_with_hint("hello there")
        assert len(result) == 5
        task = result[0]
        assert task == "CHAT"

    def test_classify_with_hint_model_not_ready(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=False):
            result = LocalModelRouter.classify_with_hint("test")
        assert len(result) == 5
        assert result[0] is None
        assert "ModelNotReady" in result[1]

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_with_hint_http_error_fallback(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        # First call (classify_with_hint) returns 500
        resp_fail = MagicMock()
        resp_fail.status_code = 500

        # Fallback classify() call
        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {
            "message": {"content": json.dumps({"task": "CHAT", "confidence": 0.8})}
        }
        mock_post.side_effect = [resp_fail, resp_ok]

        result = LocalModelRouter.classify_with_hint("test")
        assert len(result) == 5
        # Falls back to classify
        assert result[0] == "CHAT"
        assert result[3] is None  # hint is None on fallback

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_with_hint_timeout_fallback(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        # First call times out, second (classify fallback) succeeds
        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {
            "message": {"content": json.dumps({"task": "CHAT", "confidence": 0.7})}
        }
        mock_post.side_effect = [requests.exceptions.Timeout("timeout"), resp_ok]

        result = LocalModelRouter.classify_with_hint("test")
        assert len(result) == 5
        assert result[4] == "normal"  # complexity defaults to normal on fallback

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_with_hint_alias_mapping(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        payload = json.dumps(
            {
                "task": "DRAW",  # alias for PAINTER
                "confidence": 0.90,
                "hint": None,
                "complexity": "normal",
            }
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"message": {"content": payload}}
        mock_post.return_value = resp

        task, _, _, _, _ = LocalModelRouter.classify_with_hint("draw a cat")
        assert task == "PAINTER"

    # ── _init_response_model ──────────────────────────────────────

    def test_init_response_model_returns_cached(self):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._response_model = "qwen3:8b"
        LocalModelRouter._response_model_inited = True
        result = LocalModelRouter._init_response_model()
        assert result is True

    @patch("app.core.routing.local_model_router.requests.get")
    def test_init_response_model_from_ollama(self, mock_get):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._response_model = None
        LocalModelRouter._response_model_inited = False

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=True):
            mock_get.return_value = _mock_tags_response(["qwen3:8b"])
            result = LocalModelRouter._init_response_model()
        assert result is True
        assert LocalModelRouter._response_model is not None

    def test_init_response_model_fallback_to_classifier(self):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._response_model = None
        LocalModelRouter._response_model_inited = False
        LocalModelRouter._model_name = "qwen3:4b"

        with patch.object(
            LocalModelRouter, "is_ollama_available", return_value=True
        ), patch("app.core.routing.local_model_router.requests.get") as mock_get:
            mock_get.return_value = _mock_tags_response(["unknown-model:latest"])
            result = LocalModelRouter._init_response_model()
        # Falls back to _model_name when no preferred model matches
        assert result is True
        assert LocalModelRouter._response_model == "qwen3:4b"

    def test_init_response_model_false_when_unavailable(self):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._response_model = None
        LocalModelRouter._response_model_inited = False
        LocalModelRouter._model_name = None

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=False):
            result = LocalModelRouter._init_response_model()
        assert result is False

    # ── is_simple_query ────────────────────────────────────────────

    def test_is_simple_query_simple_greeting(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=True):
            assert LocalModelRouter.is_simple_query("你好", "CHAT") is True

    def test_is_simple_query_non_chat_task(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=True):
            assert LocalModelRouter.is_simple_query("draw a cat", "PAINTER") is False

    def test_is_simple_query_ollama_unavailable(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=False):
            assert LocalModelRouter.is_simple_query("hi", "CHAT") is False

    def test_is_simple_query_long_input(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=True):
            long_text = "a" * 81
            assert LocalModelRouter.is_simple_query(long_text, "CHAT") is False

    def test_is_simple_query_with_history_too_long(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=True):
            history = [{"role": "user", "parts": ["hi"]}] * 10
            assert (
                LocalModelRouter.is_simple_query("hi", "CHAT", history=history) is False
            )

    def test_is_simple_query_with_short_history(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=True):
            history = [{"role": "user", "parts": ["hi"]}] * 4
            assert (
                LocalModelRouter.is_simple_query("你好", "CHAT", history=history)
                is True
            )

    def test_is_simple_query_realtime_keyword(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=True):
            assert LocalModelRouter.is_simple_query("今天天气如何", "CHAT") is False

    def test_is_simple_query_technical_signal(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=True):
            assert LocalModelRouter.is_simple_query("什么是lambda", "CHAT") is False

    def test_is_simple_query_deep_keyword(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=True):
            assert LocalModelRouter.is_simple_query("深入分析", "CHAT") is False

    def test_is_simple_query_code_gen_context(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=True):
            assert LocalModelRouter.is_simple_query("帮我写python代码", "CHAT") is False

    # ── generate_plan ──────────────────────────────────────────────

    @patch("app.core.routing.local_model_router.requests.post")
    def test_generate_plan_success(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        payload = json.dumps(
            {"steps": ["Analyze requirements", "Design solution", "Implement code"]}
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"message": {"content": payload}}
        mock_post.return_value = resp

        steps = LocalModelRouter.generate_plan("write a sort", "CODER")
        assert isinstance(steps, list)
        assert len(steps) == 3
        assert "Analyze requirements" in steps

    @patch("app.core.routing.local_model_router.requests.post")
    def test_generate_plan_http_error(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        resp = MagicMock()
        resp.status_code = 500
        mock_post.return_value = resp

        steps = LocalModelRouter.generate_plan("test", "CHAT")
        assert steps == []

    @patch("app.core.routing.local_model_router.requests.post")
    def test_generate_plan_exception_fallback(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"
        mock_post.side_effect = Exception("network error")

        steps = LocalModelRouter.generate_plan("test", "CHAT")
        assert steps == []

    def test_generate_plan_model_not_ready(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=False):
            steps = LocalModelRouter.generate_plan("test", "CHAT")
        assert steps == []

    @patch("app.core.routing.local_model_router.requests.post")
    def test_generate_plan_empty_response(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"message": {"content": ""}}
        mock_post.return_value = resp

        steps = LocalModelRouter.generate_plan("test", "CHAT")
        assert steps == []

    @patch("app.core.routing.local_model_router.requests.post")
    def test_generate_plan_truncates_to_five(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        many_steps = ["step1", "step2", "step3", "step4", "step5", "step6", "step7"]
        payload = json.dumps({"steps": many_steps})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"message": {"content": payload}}
        mock_post.return_value = resp

        steps = LocalModelRouter.generate_plan("big task", "CODER")
        assert len(steps) <= 5

    # ── generate_stream ────────────────────────────────────────────

    @patch("app.core.routing.local_model_router.requests.post")
    @patch("app.core.routing.local_model_router.requests.get")
    def test_generate_stream_yields_chunks(self, mock_get, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        # Set up response model
        with patch.object(LocalModelRouter, "_init_response_model", return_value=True):
            LocalModelRouter._response_model = "qwen3:8b"

            # Simulate streaming response
            lines = [
                json.dumps({"message": {"content": "Hello "}, "done": False}).encode(),
                json.dumps({"message": {"content": "world!"}, "done": True}).encode(),
            ]
            resp = MagicMock()
            resp.status_code = 200
            resp.iter_lines.return_value = iter(lines)
            mock_post.return_value = resp

            gen = LocalModelRouter.generate_stream("hi")
            assert gen is not None
            chunks = list(gen)
            combined = "".join(chunks)
            assert "Hello" in combined or "world" in combined

    def test_generate_stream_returns_none_when_unavailable(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "_init_response_model", return_value=False):
            result = LocalModelRouter.generate_stream("test")
        assert result is None

    @patch("app.core.routing.local_model_router.requests.post")
    def test_generate_stream_with_history(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "_init_response_model", return_value=True):
            LocalModelRouter._response_model = "qwen3:8b"

            lines = [
                json.dumps({"message": {"content": "response"}, "done": True}).encode(),
            ]
            resp = MagicMock()
            resp.status_code = 200
            resp.iter_lines.return_value = iter(lines)
            mock_post.return_value = resp

            history = [
                {"role": "user", "parts": ["previous question"]},
                {"role": "model", "parts": ["previous answer"]},
            ]
            gen = LocalModelRouter.generate_stream("follow up", history=history)
            assert gen is not None
            chunks = list(gen)
            # Verify messages include history
            call_kwargs = mock_post.call_args[1] if mock_post.call_args[1] else {}
            payload = call_kwargs.get("json", {})
            messages = payload.get("messages", [])
            # system + 2 history + 1 user = 4 messages
            assert len(messages) >= 3

    @patch("app.core.routing.local_model_router.requests.post")
    def test_generate_stream_with_system_instruction(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "_init_response_model", return_value=True):
            LocalModelRouter._response_model = "qwen3:8b"

            lines = [
                json.dumps({"message": {"content": "ok"}, "done": True}).encode(),
            ]
            resp = MagicMock()
            resp.status_code = 200
            resp.iter_lines.return_value = iter(lines)
            mock_post.return_value = resp

            gen = LocalModelRouter.generate_stream(
                "hello",
                system_instruction="You are a helpful bot.",
            )
            assert gen is not None
            list(gen)
            payload = mock_post.call_args[1]["json"]
            assert payload["messages"][0]["content"] == "You are a helpful bot."

    @patch("app.core.routing.local_model_router.requests.post")
    def test_generate_stream_http_error_yields_nothing(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "_init_response_model", return_value=True):
            LocalModelRouter._response_model = "qwen3:8b"

            resp = MagicMock()
            resp.status_code = 500
            mock_post.return_value = resp

            gen = LocalModelRouter.generate_stream("test")
            assert gen is not None
            chunks = list(gen)
            assert chunks == []

    # ── classify_v2 ────────────────────────────────────────────────

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_v2_returns_router_decision(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter, RouterDecision

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        payload = json.dumps(
            {
                "task": "CODER",
                "confidence": 0.9,
                "hint": "write clean code",
                "complexity": "normal",
            }
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"message": {"content": payload}}
        mock_post.return_value = resp

        with patch.object(LocalModelRouter, "is_simple_query", return_value=False):
            decision = LocalModelRouter.classify_v2("write a sort function")
        assert isinstance(decision, RouterDecision)
        assert decision.task_type == "CODER"
        assert decision.confidence > 0
        assert decision.forward_to_cloud is True
        assert decision.hint == "write clean code"

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_v2_with_skill_routing(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter, RouterDecision

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        payload = json.dumps(
            {
                "task": "PAINTER",
                "confidence": 0.95,
                "hint": None,
                "complexity": "normal",
            }
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"message": {"content": payload}}
        mock_post.return_value = resp

        with patch.object(LocalModelRouter, "is_simple_query", return_value=False):
            decision = LocalModelRouter.classify_v2(
                "draw a cat",
                include_skill_routing=True,
            )
        assert isinstance(decision, RouterDecision)
        assert decision.task_type == "PAINTER"
        assert decision.params.get("task_type") == "PAINTER"

    def test_classify_v2_without_ollama(self):
        from app.core.routing.local_model_router import LocalModelRouter, RouterDecision

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=False):
            decision = LocalModelRouter.classify_v2("hello")
        assert isinstance(decision, RouterDecision)
        assert decision.task_type == "CHAT"
        assert decision.forward_to_cloud is True
        assert decision.confidence == 0.0

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_v2_simple_query_local(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter, RouterDecision

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        payload = json.dumps(
            {
                "task": "CHAT",
                "confidence": 0.85,
                "hint": None,
                "complexity": "normal",
            }
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"message": {"content": payload}}
        mock_post.return_value = resp

        with patch.object(LocalModelRouter, "is_simple_query", return_value=True):
            decision = LocalModelRouter.classify_v2("hi", include_skill_routing=False)
        assert isinstance(decision, RouterDecision)
        assert decision.forward_to_cloud is False

    @patch("app.core.routing.local_model_router.requests.post")
    def test_classify_v2_legacy_tuple_roundtrip(self, mock_post):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._initialized = True
        LocalModelRouter._model_name = "qwen3:4b"

        payload = json.dumps(
            {
                "task": "RESEARCH",
                "confidence": 0.88,
                "hint": None,
                "complexity": "normal",
            }
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"message": {"content": payload}}
        mock_post.return_value = resp

        with patch.object(LocalModelRouter, "is_simple_query", return_value=False):
            decision = LocalModelRouter.classify_v2("analyze trends")
        t = decision.to_legacy_tuple()
        assert t[0] == "RESEARCH"
        assert isinstance(t[1], str)
        assert isinstance(t[2], str)

    # ── _init_response_model ───────────────────────────────────────

    @patch("app.core.routing.local_model_router.requests.get")
    def test_init_response_model_success(self, mock_get):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=True):
            mock_get.return_value = _mock_tags_response(["qwen3:8b"])
            result = LocalModelRouter._init_response_model()
        assert result is True
        assert LocalModelRouter._response_model is not None

    @patch("app.core.routing.local_model_router.requests.get")
    def test_init_response_model_fallback_to_classifier(self, mock_get):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._model_name = "qwen3:4b"
        with patch.object(LocalModelRouter, "is_ollama_available", return_value=True):
            # No response models match installed list
            mock_get.return_value = _mock_tags_response(["unknown-model:latest"])
            result = LocalModelRouter._init_response_model()
        assert result is True
        assert LocalModelRouter._response_model == "qwen3:4b"

    def test_init_response_model_already_initialized(self):
        from app.core.routing.local_model_router import LocalModelRouter

        LocalModelRouter._response_model = "qwen3:8b"
        LocalModelRouter._response_model_inited = True
        result = LocalModelRouter._init_response_model()
        assert result is True

    def test_init_response_model_ollama_unavailable(self):
        from app.core.routing.local_model_router import LocalModelRouter

        with patch.object(LocalModelRouter, "is_ollama_available", return_value=False):
            result = LocalModelRouter._init_response_model()
        assert result is False
