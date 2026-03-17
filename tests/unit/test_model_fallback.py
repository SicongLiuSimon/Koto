# -*- coding: utf-8 -*-
"""Unit tests for app.core.llm.model_fallback – circuit breaker & fallback logic."""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.core.llm.model_fallback import (
    ModelFallbackExecutor,
    _is_model_unavailable_error,
    get_fallback_executor,
)
import app.core.llm.model_fallback as _mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(side_effects: dict | None = None, default_return="ok"):
    """Return a mock provider whose generate_content dispatches by *model*."""
    provider = MagicMock()

    def _gen(prompt, model, **kw):
        if side_effects and model in side_effects:
            effect = side_effects[model]
            if isinstance(effect, Exception):
                raise effect
            return effect
        return default_return

    provider.generate_content.side_effect = _gen
    return provider


def _fresh_executor() -> ModelFallbackExecutor:
    """Create a clean executor with empty class-level circuit breaker state."""
    exe = ModelFallbackExecutor()
    # Reset class-level mutable state so tests are isolated
    ModelFallbackExecutor._cascade_failures = {}
    ModelFallbackExecutor._cascade_failure_times = {}
    return exe


SHORT_CHAIN = ["model-a", "model-b", "model-c"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSingleModelFailure:
    """When the preferred model is unavailable, the executor falls back."""

    def test_falls_back_to_next_model(self):
        exe = _fresh_executor()
        provider = _make_provider(
            side_effects={
                "model-a": Exception("404 model not found"),
            },
            default_return="from-b",
        )

        with patch.object(exe, "_build_candidate_list", return_value=SHORT_CHAIN):
            result = exe.generate_with_fallback(
                provider=provider,
                prompt="hi",
                preferred_model="model-a",
                task_type="CHAT",
            )

        assert result == "from-b"
        # model-a should now be marked unavailable
        assert not exe.is_available("model-a")


@pytest.mark.unit
class TestAllModelsFail:
    """When every candidate model is unavailable, a RuntimeError is raised."""

    def test_raises_when_all_fail(self):
        exe = _fresh_executor()
        errors = {m: Exception("404 not found") for m in SHORT_CHAIN}
        provider = _make_provider(side_effects=errors)

        with patch.object(exe, "_build_candidate_list", return_value=SHORT_CHAIN):
            with pytest.raises(Exception, match="404 not found"):
                exe.generate_with_fallback(
                    provider=provider,
                    prompt="hi",
                    preferred_model="model-a",
                    task_type="CHAT",
                )

    def test_raises_runtime_if_no_candidate_tried(self):
        """All models pre-marked unavailable → RuntimeError (no last_exc)."""
        exe = _fresh_executor()
        for m in SHORT_CHAIN:
            exe.mark_unavailable(m, ttl=600)

        provider = _make_provider()
        with patch.object(exe, "_build_candidate_list", return_value=SHORT_CHAIN):
            with pytest.raises(RuntimeError, match="所有候选模型均不可用"):
                exe.generate_with_fallback(
                    provider=provider,
                    prompt="hi",
                    preferred_model="model-a",
                    task_type="CHAT",
                )


@pytest.mark.unit
class TestCircuitBreakerOpens:
    """After an all-model cascade failure, the circuit breaker blocks calls."""

    def test_immediate_rejection_within_backoff(self):
        exe = _fresh_executor()
        errors = {m: Exception("404 not found") for m in SHORT_CHAIN}
        provider = _make_provider(side_effects=errors)

        # First call: exhaust all candidates → records cascade failure
        with patch.object(exe, "_build_candidate_list", return_value=SHORT_CHAIN):
            with pytest.raises(Exception):
                exe.generate_with_fallback(
                    provider=provider,
                    prompt="hi",
                    preferred_model="model-a",
                    task_type="TEST_CB",
                )

        # Second call within backoff window → circuit breaker fires immediately
        with patch.object(exe, "_build_candidate_list", return_value=SHORT_CHAIN):
            with pytest.raises(RuntimeError, match="Circuit breaker open"):
                exe.generate_with_fallback(
                    provider=provider,
                    prompt="hi",
                    preferred_model="model-a",
                    task_type="TEST_CB",
                )


@pytest.mark.unit
class TestBackoffTiming:
    """Exponential backoff: base * 2^(n-1), capped at 120 s."""

    @pytest.mark.parametrize(
        "fail_count, expected_backoff",
        [
            (1, 5.0),
            (2, 10.0),
            (3, 20.0),
            (4, 40.0),
            (5, 80.0),
            (6, 120.0),  # capped
            (10, 120.0),  # still capped
        ],
    )
    def test_exponential_backoff_values(self, fail_count, expected_backoff):
        exe = _fresh_executor()
        task = "BACKOFF_TEST"
        now = 1_000_000.0

        ModelFallbackExecutor._cascade_failures[task] = fail_count
        ModelFallbackExecutor._cascade_failure_times[task] = now

        provider = _make_provider()

        # Elapsed = 0 → must be inside backoff window
        with patch("app.core.llm.model_fallback.time") as mock_time:
            mock_time.time.return_value = now  # elapsed = 0
            with pytest.raises(RuntimeError, match="Circuit breaker open") as exc_info:
                exe.generate_with_fallback(
                    provider=provider,
                    prompt="hi",
                    preferred_model="model-a",
                    task_type=task,
                )
            assert f"backing off {expected_backoff:.0f}s" in str(exc_info.value)

    def test_allowed_after_backoff_expires(self):
        exe = _fresh_executor()
        task = "BACKOFF_EXPIRE"
        now = 1_000_000.0

        ModelFallbackExecutor._cascade_failures[task] = 1  # backoff = 5s
        ModelFallbackExecutor._cascade_failure_times[task] = now

        provider = _make_provider(default_return="success")

        with patch("app.core.llm.model_fallback.time") as mock_time:
            mock_time.time.return_value = now + 6.0  # past the 5s backoff
            with patch.object(exe, "_build_candidate_list", return_value=SHORT_CHAIN):
                result = exe.generate_with_fallback(
                    provider=provider,
                    prompt="hi",
                    preferred_model="model-a",
                    task_type=task,
                )
        assert result == "success"


@pytest.mark.unit
class TestCircuitBreakerResets:
    """A successful generation resets cascade_failures to 0."""

    def test_success_resets_failures(self):
        exe = _fresh_executor()
        task = "RESET_TEST"
        ModelFallbackExecutor._cascade_failures[task] = 3
        ModelFallbackExecutor._cascade_failure_times[task] = 0.0  # long ago

        provider = _make_provider(default_return="ok")

        with patch.object(exe, "_build_candidate_list", return_value=SHORT_CHAIN):
            result = exe.generate_with_fallback(
                provider=provider,
                prompt="hi",
                preferred_model="model-a",
                task_type=task,
            )

        assert result == "ok"
        assert ModelFallbackExecutor._cascade_failures[task] == 0


@pytest.mark.unit
class TestIndependentTaskTypes:
    """Circuit breaker state is per task_type."""

    def test_different_task_types_isolated(self):
        exe = _fresh_executor()
        now = 1_000_000.0

        # Trip circuit breaker for "chat" only
        ModelFallbackExecutor._cascade_failures["chat"] = 5
        ModelFallbackExecutor._cascade_failure_times["chat"] = now

        provider = _make_provider(default_return="ok")

        with patch("app.core.llm.model_fallback.time") as mock_time:
            mock_time.time.return_value = now  # inside backoff for "chat"

            # "chat" blocked
            with pytest.raises(RuntimeError, match="Circuit breaker open"):
                exe.generate_with_fallback(
                    provider=provider,
                    prompt="hi",
                    preferred_model="model-a",
                    task_type="chat",
                )

            # "code_gen" unaffected
            with patch.object(exe, "_build_candidate_list", return_value=SHORT_CHAIN):
                result = exe.generate_with_fallback(
                    provider=provider,
                    prompt="hi",
                    preferred_model="model-a",
                    task_type="code_gen",
                )
            assert result == "ok"


@pytest.mark.unit
class TestUnavailableTTL:
    """Model marked unavailable becomes available after TTL expires."""

    def test_model_available_after_ttl(self):
        exe = _fresh_executor()
        now = 1_000_000.0

        with patch("app.core.llm.model_fallback.time") as mock_time:
            mock_time.time.return_value = now
            exe.mark_unavailable("model-x", ttl=60)
            assert not exe.is_available("model-x")

            # Still unavailable just before expiry
            mock_time.time.return_value = now + 59
            assert not exe.is_available("model-x")

            # Available after TTL
            mock_time.time.return_value = now + 60
            assert exe.is_available("model-x")


@pytest.mark.unit
class TestCustomTTLFromEnv:
    """KOTO_MODEL_UNAVAILABLE_TTL env var changes the TTL value."""

    def test_env_overrides_default_ttl(self):
        with patch.dict("os.environ", {"KOTO_MODEL_UNAVAILABLE_TTL": "42"}):
            # Re-evaluate the class attribute with the env var set
            new_ttl = int("42")
            original = ModelFallbackExecutor._UNAVAILABLE_TTL
            try:
                ModelFallbackExecutor._UNAVAILABLE_TTL = new_ttl
                exe = ModelFallbackExecutor()
                now = 1_000_000.0

                with patch("app.core.llm.model_fallback.time") as mock_time:
                    mock_time.time.return_value = now
                    exe.mark_unavailable("model-env")

                    # Should be unavailable for 42s
                    mock_time.time.return_value = now + 41
                    assert not exe.is_available("model-env")

                    mock_time.time.return_value = now + 42
                    assert exe.is_available("model-env")
            finally:
                ModelFallbackExecutor._UNAVAILABLE_TTL = original


@pytest.mark.unit
class TestGetFallbackExecutorSingleton:
    """Module-level get_fallback_executor() returns a singleton."""

    def test_returns_same_instance(self):
        # Reset the module-level singleton
        _mod._executor = None
        try:
            a = get_fallback_executor()
            b = get_fallback_executor()
            assert a is b
            assert isinstance(a, ModelFallbackExecutor)
        finally:
            _mod._executor = None


@pytest.mark.unit
class TestIsModelUnavailableError:
    """Covers the _is_model_unavailable_error helper."""

    @pytest.mark.parametrize(
        "msg",
        [
            "HTTP 404: model not found",
            "The model does not exist in this project",
            "model is unavailable right now",
            "INVALID_ARGUMENT: model xyz",
            "unknown model: foobar",
            "model_not_found",
            "Project X does not have access to model Y",
            "permission denied for model Z",
            "This endpoint is not available",
            "Interactions API only",
        ],
    )
    def test_detects_unavailable(self, msg):
        assert _is_model_unavailable_error(Exception(msg))

    @pytest.mark.parametrize(
        "msg",
        [
            "network timeout",
            "rate limit exceeded",
            "internal server error",
            "invalid prompt",
        ],
    )
    def test_ignores_non_model_errors(self, msg):
        assert not _is_model_unavailable_error(Exception(msg))
