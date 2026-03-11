# -*- coding: utf-8 -*-
"""Unit tests for app.core.llm.base.LLMProvider abstract interface."""
import logging
import pytest
from app.core.llm.base import LLMProvider

_LOGGER = "app.core.llm.base"


def _make_provider(**overrides):
    """Factory: returns a minimal concrete LLMProvider subclass."""
    class _Provider(LLMProvider):
        def generate_content(self, prompt, model, **kwargs):
            return {"content": str(prompt)}
        def get_token_count(self, prompt, model):
            return 1
    return _Provider()


@pytest.mark.unit
class TestLLMProviderAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            LLMProvider()  # type: ignore[abstract]

    def test_subclass_without_generate_content_raises(self):
        class IncompleteProvider(LLMProvider):
            def get_token_count(self, prompt, model):
                return 0

        with pytest.raises(TypeError):
            IncompleteProvider()

    def test_subclass_without_get_token_count_raises(self):
        class IncompleteProvider(LLMProvider):
            def generate_content(self, prompt, model, **kwargs):
                return {"content": "hello"}

        with pytest.raises(TypeError):
            IncompleteProvider()

    def test_concrete_subclass_can_be_instantiated(self):
        class MockProvider(LLMProvider):
            def generate_content(self, prompt, model, system_instruction=None,
                                  tools=None, stream=False, **kwargs):
                return {"content": f"Response to: {prompt}", "model": model}

            def get_token_count(self, prompt, model):
                if isinstance(prompt, str):
                    return len(prompt.split())
                return sum(len(str(m)) for m in prompt)

        provider = MockProvider()
        assert isinstance(provider, LLMProvider)

    def test_concrete_subclass_generate_content(self):
        class EchoProvider(LLMProvider):
            def generate_content(self, prompt, model, **kwargs):
                return {"content": str(prompt), "model": model}

            def get_token_count(self, prompt, model):
                return 1

        provider = EchoProvider()
        result = provider.generate_content("Hello", model="test-model")
        assert result["content"] == "Hello"
        assert result["model"] == "test-model"

    def test_concrete_subclass_get_token_count(self):
        class CountingProvider(LLMProvider):
            def generate_content(self, prompt, model, **kwargs):
                return {"content": ""}

            def get_token_count(self, prompt, model):
                return len(prompt) if isinstance(prompt, str) else len(prompt)

        provider = CountingProvider()
        assert provider.get_token_count("hello world", "model") == 11
        assert provider.get_token_count(["msg1", "msg2"], "model") == 2

    def test_subclass_is_instance_of_llm_provider(self):
        class ConcreteProvider(LLMProvider):
            def generate_content(self, prompt, model, **kwargs):
                return {"content": "ok"}

            def get_token_count(self, prompt, model):
                return 0

        p = ConcreteProvider()
        assert isinstance(p, LLMProvider)


@pytest.mark.unit
class TestLLMProviderLogging:
    """Verify that _log_request / _log_response emit the correct log levels."""

    def test_log_request_emits_debug(self, caplog):
        """_log_request() must emit a DEBUG log with model and prompt_len."""
        provider = _make_provider()
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            provider._log_request("hello world", model="test-model")
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("test-model" in m and "LLMProvider" in m for m in debug_msgs), debug_msgs

    def test_log_response_ok_emits_debug(self, caplog):
        """_log_response() on success must emit a DEBUG log."""
        provider = _make_provider()
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            provider._log_response("test-model", response_len=42)
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("test-model" in m and "OK" in m for m in debug_msgs), debug_msgs

    def test_log_response_error_emits_warning(self, caplog):
        """_log_response(error=True) must emit a WARNING, not a DEBUG (negative: DEBUG absent)."""
        provider = _make_provider()
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            provider._log_response("test-model", error=True, error_msg="timeout")
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("test-model" in m and "ERROR" in m for m in warnings), warnings
        # Must NOT emit a DEBUG "OK" record for the same call
        ok_debug = [r.message for r in caplog.records
                    if r.levelno == logging.DEBUG and "OK" in r.message]
        assert ok_debug == [], f"Should not emit DEBUG OK on error: {ok_debug}"

    def test_log_request_no_warning(self, caplog):
        """_log_request() must never emit a WARNING (negative test)."""
        provider = _make_provider()
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            provider._log_request(["msg1", "msg2"], model="gemini-pro")
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == [], f"Unexpected warnings in _log_request: {[r.message for r in warnings]}"

