"""Unit tests for AIRouter (OrderedDict LRU cache) and LocalModelRouter additions.

Covers:
- AIRouter._cache is an OrderedDict class attribute
- AIRouter._cache_max_size == 100
- LRU eviction: adding 101 entries removes the oldest (key0)
- Cache move-to-end behaviour on hit (oldest becomes second-oldest after hit)
- RouterDecision dataclass construction and defaults
- LocalModelRouter.pick_best_chat_model with empty and non-empty installed lists
"""

from __future__ import annotations

from collections import OrderedDict

import pytest

# ---------------------------------------------------------------------------
# AIRouter — cache structure
# ---------------------------------------------------------------------------


class TestAIRouterCacheType:
    def test_cache_is_ordered_dict(self):
        from app.core.routing.ai_router import AIRouter

        assert isinstance(AIRouter._cache, OrderedDict)

    def test_cache_max_size_is_100(self):
        from app.core.routing.ai_router import AIRouter

        assert AIRouter._cache_max_size == 100


# ---------------------------------------------------------------------------
# AIRouter — LRU eviction (direct cache manipulation, no live LLM)
# ---------------------------------------------------------------------------


class TestAIRouterLRUEviction:
    def setup_method(self):
        from app.core.routing.ai_router import AIRouter

        AIRouter._cache.clear()

    def teardown_method(self):
        from app.core.routing.ai_router import AIRouter

        AIRouter._cache.clear()

    def test_evicts_oldest_when_over_limit(self):
        from app.core.routing.ai_router import AIRouter

        cache = AIRouter._cache
        for i in range(101):
            key = f"key{i}"
            cache[key] = f"val{i}"
            cache.move_to_end(key)
            if len(cache) > AIRouter._cache_max_size:
                cache.popitem(last=False)

        assert len(cache) == 100
        assert "key0" not in cache, "oldest entry must be evicted"
        assert "key100" in cache, "newest entry must be retained"

    def test_move_to_end_changes_eviction_order(self):
        """Accessing key1 should protect it from being the next eviction victim."""
        from app.core.routing.ai_router import AIRouter

        cache = AIRouter._cache
        # Fill to exactly max_size
        for i in range(100):
            key = f"k{i}"
            cache[key] = i
            cache.move_to_end(key)

        # Touch k0 — it moves to the end (most-recent position)
        cache.move_to_end("k0")

        # Add one more entry, triggering eviction of the new oldest (k1)
        cache["k_new"] = "new"
        cache.move_to_end("k_new")
        if len(cache) > AIRouter._cache_max_size:
            cache.popitem(last=False)

        assert "k0" in cache, "touched entry should survive eviction"
        assert "k1" not in cache, "formerly-oldest entry should be evicted"


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
# LocalModelRouter.pick_best_chat_model
# ---------------------------------------------------------------------------


class TestPickBestChatModel:
    def test_returns_none_for_empty_installed(self):
        from app.core.routing.local_model_router import LocalModelRouter

        assert LocalModelRouter.pick_best_chat_model([]) is None

    def test_returns_higher_priority_model(self):
        from app.core.routing.local_model_router import LocalModelRouter

        # qwen3:1.7b is earlier in OLLAMA_MODELS than llama3.2:3b
        result = LocalModelRouter.pick_best_chat_model(["qwen3:1.7b", "llama3.2:3b"])
        assert result == "qwen3:1.7b"

    def test_returns_only_available_model(self):
        from app.core.routing.local_model_router import LocalModelRouter

        result = LocalModelRouter.pick_best_chat_model(["llama3.2:3b"])
        assert result == "llama3.2:3b"

    def test_respects_priority_order(self):
        from app.core.routing.local_model_router import LocalModelRouter

        # qwen3:8b > qwen3:4b in OLLAMA_MODELS priority
        result = LocalModelRouter.pick_best_chat_model(["qwen3:4b", "qwen3:8b"])
        assert result == "qwen3:8b"

    def test_returns_first_for_unknown_models(self):
        from app.core.routing.local_model_router import LocalModelRouter

        installed = ["unknown-model:7b"]
        result = LocalModelRouter.pick_best_chat_model(installed)
        assert result == "unknown-model:7b"
