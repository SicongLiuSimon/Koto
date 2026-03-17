"""Tests for the Koto custom exception hierarchy."""

import inspect
import pytest

from app.core.errors import (
    KotoError,
    LLMProviderError,
    OllamaConnectionError,
    GeminiAPIError,
    ModelNotFoundError,
    ModelTimeoutError,
    SkillError,
    SkillNotFoundError,
    SkillExecutionError,
    SkillLoadError,
    AuthenticationError,
    TokenExpiredError,
    InsufficientPermissionsError,
    ConfigurationError,
    MissingConfigError,
    StorageError,
    PathTraversalError,
    RoutingError,
    TaskClassificationError,
)

# Collect all exception classes defined in the module
ALL_EXCEPTIONS = [
    KotoError,
    LLMProviderError,
    OllamaConnectionError,
    GeminiAPIError,
    ModelNotFoundError,
    ModelTimeoutError,
    SkillError,
    SkillNotFoundError,
    SkillExecutionError,
    SkillLoadError,
    AuthenticationError,
    TokenExpiredError,
    InsufficientPermissionsError,
    ConfigurationError,
    MissingConfigError,
    StorageError,
    PathTraversalError,
    RoutingError,
    TaskClassificationError,
]


class TestInheritance:
    """All exceptions must inherit from KotoError."""

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_inherits_from_koto_error(self, exc_cls):
        assert issubclass(exc_cls, KotoError)

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_inherits_from_exception(self, exc_cls):
        assert issubclass(exc_cls, Exception)

    def test_llm_subtree(self):
        for cls in (
            OllamaConnectionError,
            GeminiAPIError,
            ModelNotFoundError,
            ModelTimeoutError,
        ):
            assert issubclass(cls, LLMProviderError)

    def test_skill_subtree(self):
        for cls in (SkillNotFoundError, SkillExecutionError, SkillLoadError):
            assert issubclass(cls, SkillError)

    def test_auth_subtree(self):
        for cls in (TokenExpiredError, InsufficientPermissionsError):
            assert issubclass(cls, AuthenticationError)

    def test_config_subtree(self):
        assert issubclass(MissingConfigError, ConfigurationError)

    def test_storage_subtree(self):
        assert issubclass(PathTraversalError, StorageError)

    def test_routing_subtree(self):
        assert issubclass(TaskClassificationError, RoutingError)


class TestToDict:
    """to_dict() must return a well-formed dict."""

    def test_basic_to_dict(self):
        err = KotoError("something broke")
        result = err.to_dict()
        assert result == {
            "error": "KOTO_ERROR",
            "message": "something broke",
            "details": {},
        }

    def test_to_dict_with_details(self):
        details = {"model": "llama3", "status": 503}
        err = OllamaConnectionError("server down", details=details)
        result = err.to_dict()
        assert result["error"] == "OLLAMA_CONNECTION_ERROR"
        assert result["message"] == "server down"
        assert result["details"] == details

    def test_to_dict_keys(self):
        err = SkillNotFoundError("no such skill")
        assert set(err.to_dict().keys()) == {"error", "message", "details"}

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_all_exceptions_have_to_dict(self, exc_cls):
        err = exc_cls("test message")
        result = err.to_dict()
        assert isinstance(result, dict)
        assert "error" in result
        assert "message" in result
        assert "details" in result


class TestErrorCodeUniqueness:
    """Every concrete exception must have a unique error_code."""

    def test_error_codes_are_unique(self):
        codes = [cls.error_code for cls in ALL_EXCEPTIONS]
        assert len(codes) == len(set(codes)), (
            f"Duplicate error_codes found: "
            f"{[c for c in codes if codes.count(c) > 1]}"
        )

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_error_code_is_string(self, exc_cls):
        assert isinstance(exc_cls.error_code, str)

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_error_code_is_uppercase(self, exc_cls):
        assert exc_cls.error_code == exc_cls.error_code.upper()


class TestDetailsParameter:
    """The optional details dict must work correctly."""

    def test_default_details_is_empty_dict(self):
        err = KotoError("msg")
        assert err.details == {}

    def test_none_details_becomes_empty_dict(self):
        err = KotoError("msg", details=None)
        assert err.details == {}

    def test_custom_details_preserved(self):
        details = {"key": "value", "count": 42}
        err = KotoError("msg", details=details)
        assert err.details == details

    def test_details_not_shared_between_instances(self):
        err1 = KotoError("a")
        err2 = KotoError("b")
        err1.details["x"] = 1
        assert "x" not in err2.details


class TestRaiseAndCatch:
    """Exceptions can be raised and caught by parent class."""

    def test_catch_ollama_as_llm_provider(self):
        with pytest.raises(LLMProviderError):
            raise OllamaConnectionError("offline")

    def test_catch_ollama_as_koto_error(self):
        with pytest.raises(KotoError):
            raise OllamaConnectionError("offline")

    def test_catch_skill_not_found_as_skill_error(self):
        with pytest.raises(SkillError):
            raise SkillNotFoundError("missing")

    def test_catch_token_expired_as_auth_error(self):
        with pytest.raises(AuthenticationError):
            raise TokenExpiredError("expired")

    def test_catch_missing_config_as_config_error(self):
        with pytest.raises(ConfigurationError):
            raise MissingConfigError("no key")

    def test_catch_path_traversal_as_storage_error(self):
        with pytest.raises(StorageError):
            raise PathTraversalError("../../../etc/passwd")

    def test_catch_task_classification_as_routing_error(self):
        with pytest.raises(RoutingError):
            raise TaskClassificationError("unknown intent")

    def test_catch_any_as_base_exception(self):
        with pytest.raises(Exception):
            raise GeminiAPIError("quota exceeded")


class TestStringRepresentation:
    """str() and repr() must include the message."""

    def test_str_is_message(self):
        err = KotoError("test message")
        assert str(err) == "test message"

    def test_str_on_subclass(self):
        err = ModelNotFoundError("llama3 not found")
        assert str(err) == "llama3 not found"

    def test_message_attribute(self):
        err = SkillExecutionError("timeout in skill")
        assert err.message == "timeout in skill"

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_str_matches_message(self, exc_cls):
        msg = f"test for {exc_cls.__name__}"
        err = exc_cls(msg)
        assert str(err) == msg
        assert err.message == msg
