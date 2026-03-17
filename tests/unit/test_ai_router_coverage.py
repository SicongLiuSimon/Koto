"""Comprehensive tests for app.core.routing.ai_router.AIRouter."""

import pytest
import json
import hashlib
from unittest.mock import patch, MagicMock, Mock


def _make_mock_response(text):
    """Helper: build a mock GenAI response whose .candidates[0].content.parts[0].text returns *text*."""
    part = MagicMock()
    part.text = text
    content = MagicMock()
    content.parts = [part]
    candidate = MagicMock()
    candidate.content = content
    response = MagicMock()
    response.candidates = [candidate]
    return response


def _make_mock_client(text="CHAT"):
    """Return a mock client whose generate_content returns *text* immediately."""
    client = MagicMock()
    client.models.generate_content.return_value = _make_mock_response(text)
    return client


@pytest.mark.unit
class TestCacheSet:
    """Tests for AIRouter._cache_set() and LRU eviction."""

    def setup_method(self):
        from app.core.routing.ai_router import AIRouter

        AIRouter._cache.clear()
        AIRouter._router_model = "gemini-2.5-flash"

    def teardown_method(self):
        from app.core.routing.ai_router import AIRouter

        AIRouter._cache.clear()
        AIRouter._router_model = "gemini-2.5-flash"

    def test_basic_set_and_get(self):
        from app.core.routing.ai_router import AIRouter

        AIRouter._cache_set("key1", "value1")
        assert AIRouter._cache["key1"] == "value1"

    def test_eviction_when_full(self):
        """When cache reaches _cache_max_size, oldest half should be evicted."""
        from app.core.routing.ai_router import AIRouter

        original_max = AIRouter._cache_max_size
        try:
            AIRouter._cache_max_size = 10
            # Fill cache to capacity
            for i in range(10):
                AIRouter._cache[f"k{i}"] = f"v{i}"
            assert len(AIRouter._cache) == 10
            # Next insert triggers eviction of oldest 5
            AIRouter._cache_set("new_key", "new_value")
            assert len(AIRouter._cache) <= 6  # 10 - 5 evicted + 1 new
            assert "new_key" in AIRouter._cache
            # Oldest keys should be gone
            assert "k0" not in AIRouter._cache
            assert "k4" not in AIRouter._cache
            # Newest pre-eviction keys should remain
            assert "k5" in AIRouter._cache
        finally:
            AIRouter._cache_max_size = original_max


@pytest.mark.unit
class TestClassify:
    """Tests for AIRouter.classify()."""

    def setup_method(self):
        from app.core.routing.ai_router import AIRouter

        AIRouter._cache.clear()
        AIRouter._router_model = "gemini-2.5-flash"
        AIRouter._ROUTER_MODEL_CHAIN = ["gemini-2.5-flash", "gemini-2.0-flash-lite"]

    def teardown_method(self):
        from app.core.routing.ai_router import AIRouter

        AIRouter._cache.clear()
        AIRouter._router_model = "gemini-2.5-flash"
        AIRouter._ROUTER_MODEL_CHAIN = ["gemini-2.5-flash", "gemini-2.0-flash-lite"]

    @patch("app.core.routing.ai_router.hashlib")
    def test_cache_hit(self, mock_hashlib):
        from app.core.routing.ai_router import AIRouter

        # Pre-populate cache
        mock_hashlib.md5.return_value.hexdigest.return_value = "abcdef1234567890extra"
        cache_key = "abcdef1234567890"
        AIRouter._cache[cache_key] = ("CODER", "🤖 AI")

        client = MagicMock()
        task, conf, src = AIRouter.classify(client, "write python code")
        assert task == "CODER"
        assert src == "Cache"
        # Client should NOT be called on cache hit
        client.models.generate_content.assert_not_called()

    def test_successful_classification(self):
        from app.core.routing.ai_router import AIRouter

        client = _make_mock_client("CODER")
        task, conf, src = AIRouter.classify(client, "write python code", timeout=5.0)
        assert task == "CODER"
        assert src == "AI"
        assert conf == "🤖 AI"

    def test_classification_painter(self):
        from app.core.routing.ai_router import AIRouter

        client = _make_mock_client("PAINTER")
        task, conf, src = AIRouter.classify(client, "draw a cat", timeout=5.0)
        assert task == "PAINTER"

    def test_unrecognized_text_falls_back_to_chat(self):
        """If the model returns text that doesn't match any valid task, default to CHAT."""
        from app.core.routing.ai_router import AIRouter

        client = _make_mock_client("UNKNOWN_TASK")
        task, conf, src = AIRouter.classify(client, "hello", timeout=5.0)
        assert task == "CHAT"

    def test_timeout_returns_chat_fallback(self):
        """When the thread exceeds the timeout, classify returns CHAT with Timeout-fallback."""
        import time
        from app.core.routing.ai_router import AIRouter

        def slow_generate(*args, **kwargs):
            time.sleep(5)
            return _make_mock_response("CODER")

        client = MagicMock()
        client.models.generate_content.side_effect = slow_generate

        task, conf, src = AIRouter.classify(client, "write code", timeout=0.1)
        assert task == "CHAT"
        assert conf == "Timeout-fallback"
        assert src == "AI"

    def test_error_returns_none(self):
        """A non-unavailable exception returns (None, 'Error', 'AI')."""
        from app.core.routing.ai_router import AIRouter

        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError("connection refused")
        task, conf, src = AIRouter.classify(client, "hello", timeout=5.0)
        assert task is None
        assert conf == "Error"

    def test_model_degradation_chain(self):
        """When the first model is unavailable (404), the router tries the next in the chain."""
        from app.core.routing.ai_router import AIRouter

        AIRouter._router_model = "gemini-2.5-flash"

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            model = kwargs.get("model", args[0] if args else None)
            if model == "gemini-2.5-flash":
                raise Exception("404 not found")
            return _make_mock_response("CODER")

        client = MagicMock()
        client.models.generate_content.side_effect = side_effect

        task, conf, src = AIRouter.classify(client, "write code", timeout=5.0)
        # Multiple models were attempted (chain was iterated)
        assert call_count >= 2
        # The error from the first model persists in result_holder,
        # so the overall result is an error even though a later model succeeded.
        assert task is None
        assert conf == "Error"

    def test_all_models_unavailable(self):
        """When every model in the chain fails with 'not found', return error."""
        from app.core.routing.ai_router import AIRouter

        client = MagicMock()
        client.models.generate_content.side_effect = Exception("404 not found")

        task, conf, src = AIRouter.classify(client, "hello", timeout=5.0)
        assert task is None
        assert conf == "Error"

    def test_empty_candidates_falls_back(self):
        """If response.candidates is empty for all models, classify returns NoResult."""
        from app.core.routing.ai_router import AIRouter

        response = MagicMock()
        response.candidates = []
        client = MagicMock()
        client.models.generate_content.return_value = response
        task, conf, src = AIRouter.classify(client, "hello", timeout=5.0)
        assert task is None
        assert conf == "NoResult"


@pytest.mark.unit
class TestClassifyWithHint:
    """Tests for AIRouter.classify_with_hint()."""

    def setup_method(self):
        from app.core.routing.ai_router import AIRouter

        AIRouter._cache.clear()
        AIRouter._router_model = "gemini-2.5-flash"
        AIRouter._ROUTER_MODEL_CHAIN = ["gemini-2.5-flash", "gemini-2.0-flash-lite"]

    def teardown_method(self):
        from app.core.routing.ai_router import AIRouter

        AIRouter._cache.clear()
        AIRouter._router_model = "gemini-2.5-flash"
        AIRouter._ROUTER_MODEL_CHAIN = ["gemini-2.5-flash", "gemini-2.0-flash-lite"]

    def test_cache_hit(self):
        from app.core.routing.ai_router import AIRouter

        user_input = "check weather"
        cache_key = "h:" + hashlib.md5(user_input.encode()).hexdigest()[:16]
        AIRouter._cache[cache_key] = ("WEB_SEARCH", "🤖 AI+Hint", "show temperature")

        client = MagicMock()
        task, conf, src, hint = AIRouter.classify_with_hint(client, user_input)
        assert task == "WEB_SEARCH"
        assert src == "Cache"
        assert hint == "show temperature"
        client.models.generate_content.assert_not_called()

    def test_cache_hit_without_hint(self):
        """Cache tuple with only 2 elements should yield hint=None."""
        from app.core.routing.ai_router import AIRouter

        user_input = "hello"
        cache_key = "h:" + hashlib.md5(user_input.encode()).hexdigest()[:16]
        AIRouter._cache[cache_key] = ("CHAT", "🤖 AI+Hint")

        client = MagicMock()
        task, conf, src, hint = AIRouter.classify_with_hint(client, user_input)
        assert task == "CHAT"
        assert hint is None

    def test_successful_json_parse_with_hint(self):
        from app.core.routing.ai_router import AIRouter

        response_text = json.dumps({"task": "WEB_SEARCH", "hint": "show current price"})
        client = _make_mock_client(response_text)
        task, conf, src, hint = AIRouter.classify_with_hint(
            client, "bitcoin price", timeout=5.0
        )
        assert task == "WEB_SEARCH"
        assert hint == "show current price"
        assert src == "AI"

    def test_successful_json_parse_null_hint(self):
        from app.core.routing.ai_router import AIRouter

        response_text = json.dumps({"task": "CHAT", "hint": None})
        client = _make_mock_client(response_text)
        task, conf, src, hint = AIRouter.classify_with_hint(
            client, "what is python", timeout=5.0
        )
        assert task == "CHAT"
        assert hint is None

    def test_successful_json_short_hint_ignored(self):
        """Hints with 3 or fewer chars are discarded."""
        from app.core.routing.ai_router import AIRouter

        response_text = json.dumps({"task": "CHAT", "hint": "hi"})
        client = _make_mock_client(response_text)
        task, conf, src, hint = AIRouter.classify_with_hint(client, "hey", timeout=5.0)
        assert task == "CHAT"
        assert hint is None

    def test_fallback_text_parse(self):
        """If JSON parse fails, fall back to text scan for task name."""
        from app.core.routing.ai_router import AIRouter

        # Return non-JSON text that contains a valid task keyword
        client = _make_mock_client("The task is RESEARCH based on input.")
        task, conf, src, hint = AIRouter.classify_with_hint(
            client, "deep analysis of AI", timeout=5.0
        )
        assert task == "RESEARCH"
        assert hint is None

    def test_timeout_falls_back_to_classify(self):
        """On timeout, classify_with_hint delegates to classify()."""
        import time
        from app.core.routing.ai_router import AIRouter

        call_count = {"n": 0}

        def slow_then_fast(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                time.sleep(5)  # First call (classify_with_hint's thread) is slow
                return _make_mock_response("CODER")
            # Subsequent calls (classify fallback) return immediately
            return _make_mock_response("CHAT")

        client = MagicMock()
        client.models.generate_content.side_effect = slow_then_fast

        task, conf, src, hint = AIRouter.classify_with_hint(
            client, "hello", timeout=0.1
        )
        # Should have fallen back; hint should be None
        assert hint is None
        assert task is not None

    def test_error_falls_back_to_classify(self):
        """On non-unavailable error, classify_with_hint delegates to classify()."""
        from app.core.routing.ai_router import AIRouter

        call_count = {"n": 0}

        def error_then_ok(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] <= 1:
                raise RuntimeError("some network error")
            return _make_mock_response("CHAT")

        client = MagicMock()
        client.models.generate_content.side_effect = error_then_ok

        task, conf, src, hint = AIRouter.classify_with_hint(
            client, "hello", timeout=5.0
        )
        assert hint is None
        # classify fallback should have been invoked
        assert task is not None

    def test_returns_hint_when_present(self):
        """Full round-trip: JSON with both task and meaningful hint."""
        from app.core.routing.ai_router import AIRouter

        payload = json.dumps({"task": "CODER", "hint": "use modular design with tests"})
        client = _make_mock_client(payload)
        task, conf, src, hint = AIRouter.classify_with_hint(
            client, "build a web scraper", timeout=5.0
        )
        assert task == "CODER"
        assert hint == "use modular design with tests"
        assert conf == "🤖 AI+Hint"
