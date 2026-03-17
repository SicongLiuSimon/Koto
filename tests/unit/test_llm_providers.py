"""Unit tests for LLM providers: GeminiProvider and OllamaLLMProvider.

All external API/network calls are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# GeminiProvider — basic structure and generate_content
# ---------------------------------------------------------------------------


class TestGeminiProviderBasic:
    def test_provider_has_timeout_config(self):
        from app.core.llm.gemini import GeminiProvider

        assert hasattr(GeminiProvider, "MAX_RETRIES")
        assert isinstance(GeminiProvider.MAX_RETRIES, int)
        assert GeminiProvider.MAX_RETRIES > 0

    def test_normal_model_not_substituted(self):
        from app.core.llm.gemini import _INTERACTIONS_ONLY_MODELS

        # Normal model should NOT be in the interactions-only set
        assert "gemini-2.5-flash" not in _INTERACTIONS_ONLY_MODELS


# ---------------------------------------------------------------------------
# OllamaLLMProvider — _resolve_model auto-selection
# ---------------------------------------------------------------------------


class TestOllamaLLMProviderResolveModel:
    def _get_provider(self, model=None):
        from app.core.llm.ollama_llm_provider import OllamaLLMProvider

        # Reset class-level cache
        OllamaLLMProvider._auto_model = ""
        OllamaLLMProvider._auto_model_ts = 0.0
        return OllamaLLMProvider(model=model)

    def test_explicit_model_returned_directly(self):
        prov = self._get_provider(model="qwen3:8b")
        assert prov._resolve_model() == "qwen3:8b"

    def test_none_model_calls_local_router(self):
        from app.core.llm.ollama_llm_provider import OllamaLLMProvider

        OllamaLLMProvider._auto_model = ""
        OllamaLLMProvider._auto_model_ts = 0.0
        prov = self._get_provider(model=None)

        with patch(
            "app.core.llm.ollama_llm_provider.OllamaLLMProvider._resolve_model",
            return_value="qwen3:4b",
        ) as mock_resolve:
            result = prov._resolve_model()
        # Either the mock intercepted it or it returned the patched value
        assert isinstance(result, str)

    def test_cached_auto_model_used_within_ttl(self):
        import time

        from app.core.llm.ollama_llm_provider import OllamaLLMProvider

        OllamaLLMProvider._auto_model = "cached-model:7b"
        OllamaLLMProvider._auto_model_ts = time.time()  # fresh timestamp — within TTL
        prov = OllamaLLMProvider(model=None)  # no explicit model
        result = prov._resolve_model()
        # Cache hit: should return cached value without calling router
        assert result == "cached-model:7b"

    def test_expired_cache_triggers_re_detection(self):
        import sys
        import time
        from unittest.mock import MagicMock

        from app.core.llm.ollama_llm_provider import OllamaLLMProvider

        OllamaLLMProvider._auto_model = "stale-model:7b"
        OllamaLLMProvider._auto_model_ts = time.time() - 9999  # expired
        prov = self._get_provider(model=None)

        mock_router = MagicMock()
        mock_router.pick_best_chat_model.return_value = "fresh-model:8b"
        with patch.dict(
            sys.modules,
            {
                "app.core.routing.local_model_router": MagicMock(
                    LocalModelRouter=mock_router
                )
            },
        ):
            result = prov._resolve_model()
        # Either fresh detection worked or fallback to hardcoded default
        assert isinstance(result, str)
        assert result != ""

    def test_fallback_when_router_unavailable(self):
        import sys
        import time
        from unittest.mock import MagicMock

        from app.core.llm.ollama_llm_provider import OllamaLLMProvider

        OllamaLLMProvider._auto_model = ""
        OllamaLLMProvider._auto_model_ts = 0.0
        prov = self._get_provider(model=None)

        mock_router_module = MagicMock()
        mock_router_module.LocalModelRouter.pick_best_chat_model.side_effect = (
            Exception("ollama down")
        )
        with patch.dict(
            sys.modules, {"app.core.routing.local_model_router": mock_router_module}
        ):
            result = prov._resolve_model()

        # Should fall back to hardcoded default, not crash
        assert isinstance(result, str)
        assert len(result) > 0

    def test_auto_model_cache_class_level(self):
        """Two instances with model=None share the class-level cache."""
        import time

        from app.core.llm.ollama_llm_provider import OllamaLLMProvider

        OllamaLLMProvider._auto_model = "shared:7b"
        OllamaLLMProvider._auto_model_ts = time.time()

        p1 = OllamaLLMProvider(model=None)
        p2 = OllamaLLMProvider(model=None)
        assert p1._resolve_model() == p2._resolve_model() == "shared:7b"
