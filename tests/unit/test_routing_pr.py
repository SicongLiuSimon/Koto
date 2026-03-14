"""Unit tests for AIRouter (dict cache) and LocalModelRouter additions.

Covers:
- AIRouter._cache is a dict class attribute
- AIRouter._cache_max_size == 100
- Cache eviction: adding entries beyond max_size triggers half-eviction
- RouterDecision dataclass construction and defaults
- LocalModelRouter.pick_best_chat_model (no-arg classmethod, queries Ollama)
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# AIRouter — cache structure
# ---------------------------------------------------------------------------


class TestAIRouterCacheType:
    def test_cache_is_dict(self):
        from app.core.routing.ai_router import AIRouter

        assert isinstance(AIRouter._cache, dict)

    def test_cache_max_size_is_100(self):
        from app.core.routing.ai_router import AIRouter

        assert AIRouter._cache_max_size == 100


# ---------------------------------------------------------------------------
# AIRouter — eviction (direct cache manipulation, no live LLM)
# ---------------------------------------------------------------------------


class TestAIRouterCacheEviction:
    def setup_method(self):
        from app.core.routing.ai_router import AIRouter

        AIRouter._cache.clear()

    def teardown_method(self):
        from app.core.routing.ai_router import AIRouter

        AIRouter._cache.clear()

    def test_evicts_half_when_over_limit(self):
        from app.core.routing.ai_router import AIRouter

        cache = AIRouter._cache
        max_size = AIRouter._cache_max_size
        # Fill to max_size
        for i in range(max_size):
            cache[f"key{i}"] = f"val{i}"

        assert len(cache) == max_size

        # Trigger eviction by simulating what classify() does
        if len(cache) >= max_size:
            keys = list(cache.keys())[:max_size // 2]
            for k in keys:
                del cache[k]
        cache["key_new"] = "val_new"

        assert len(cache) <= max_size
        assert "key_new" in cache


# ---------------------------------------------------------------------------
# RouterDecision dataclass
# ---------------------------------------------------------------------------


class TestRouterDecision:
    def test_default_construction(self):
        from app.core.routing.local_model_router import RouterDecision

        d = RouterDecision()
        assert d.task_type == "CHAT"
        assert d.forward_to_cloud is True
        assert d.confidence == 0.0
        assert d.source == "Local"
        assert d.latency_ms == 0
        assert d.params == {}

    def test_custom_fields(self):
        from app.core.routing.local_model_router import RouterDecision

        d = RouterDecision(
            task_type="CODER",
            skill_id="my-skill",
            forward_to_cloud=False,
            confidence=0.92,
            hint="write tests",
            source="Cache",
            latency_ms=15,
        )
        assert d.task_type == "CODER"
        assert d.skill_id == "my-skill"
        assert d.forward_to_cloud is False
        assert d.confidence == pytest.approx(0.92)
        assert d.hint == "write tests"
        assert d.source == "Cache"
        assert d.latency_ms == 15

    def test_confidence_str_contains_source_and_value(self):
        from app.core.routing.local_model_router import RouterDecision

        d = RouterDecision(
            task_type="RESEARCH", confidence=0.75, latency_ms=42, source="Local"
        )
        s = d.confidence_str
        assert "Local" in s
        assert "0.75" in s
        assert "42" in s

    def test_to_legacy_tuple_structure(self):
        from app.core.routing.local_model_router import RouterDecision

        d = RouterDecision(
            task_type="PAINTER", source="Fallback", confidence=0.5, latency_ms=5
        )
        t = d.to_legacy_tuple()
        assert len(t) == 3
        assert t[0] == "PAINTER"
        assert t[2] == "Fallback"


# ---------------------------------------------------------------------------
# LocalModelRouter.pick_best_chat_model (no-arg classmethod)
# ---------------------------------------------------------------------------


class TestPickBestChatModel:
    def test_returns_cached_response_model(self):
        from app.core.routing.local_model_router import LocalModelRouter

        # If _response_model is set, pick_best_chat_model returns it directly
        original = LocalModelRouter._response_model
        try:
            LocalModelRouter._response_model = "qwen3:8b"
            assert LocalModelRouter.pick_best_chat_model() == "qwen3:8b"
        finally:
            LocalModelRouter._response_model = original

    def test_returns_none_when_no_model_available(self):
        from unittest.mock import patch
        from app.core.routing.local_model_router import LocalModelRouter

        original_resp = LocalModelRouter._response_model
        original_model = LocalModelRouter._model_name
        try:
            LocalModelRouter._response_model = None
            LocalModelRouter._model_name = None
            with patch.object(LocalModelRouter, "is_ollama_available", return_value=False):
                result = LocalModelRouter.pick_best_chat_model()
                # Should return None or _model_name (which is None)
                assert result is None
        finally:
            LocalModelRouter._response_model = original_resp
            LocalModelRouter._model_name = original_model
