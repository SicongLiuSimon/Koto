"""Koto custom exception hierarchy.

Provides structured, catchable exceptions for all Koto subsystems.
Each exception carries an error_code for API responses.
"""


class KotoError(Exception):
    """Base exception for all Koto errors."""

    error_code = "KOTO_ERROR"

    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self):
        return {
            "error": self.error_code,
            "message": self.message,
            "details": self.details,
        }


# LLM Provider errors


class LLMProviderError(KotoError):
    """Base for LLM provider failures."""

    error_code = "LLM_ERROR"


class OllamaConnectionError(LLMProviderError):
    """Ollama server unreachable or returned error."""

    error_code = "OLLAMA_CONNECTION_ERROR"


class GeminiAPIError(LLMProviderError):
    """Gemini API call failed."""

    error_code = "GEMINI_API_ERROR"


class ModelNotFoundError(LLMProviderError):
    """Requested model not available."""

    error_code = "MODEL_NOT_FOUND"


class ModelTimeoutError(LLMProviderError):
    """LLM request timed out."""

    error_code = "MODEL_TIMEOUT"


# Skill errors


class SkillError(KotoError):
    """Base for skill-related failures."""

    error_code = "SKILL_ERROR"


class SkillNotFoundError(SkillError):
    """Requested skill not found."""

    error_code = "SKILL_NOT_FOUND"


class SkillExecutionError(SkillError):
    """Skill execution failed."""

    error_code = "SKILL_EXECUTION_ERROR"


class SkillLoadError(SkillError):
    """Skill could not be loaded."""

    error_code = "SKILL_LOAD_ERROR"


# Auth errors


class AuthenticationError(KotoError):
    """Base for authentication/authorization failures."""

    error_code = "AUTH_ERROR"


class TokenExpiredError(AuthenticationError):
    """Authentication token has expired."""

    error_code = "TOKEN_EXPIRED"


class InsufficientPermissionsError(AuthenticationError):
    """User lacks required permissions."""

    error_code = "INSUFFICIENT_PERMISSIONS"


# Configuration errors


class ConfigurationError(KotoError):
    """Base for configuration failures."""

    error_code = "CONFIG_ERROR"


class MissingConfigError(ConfigurationError):
    """Required configuration value is missing."""

    error_code = "MISSING_CONFIG"


# Storage/IO errors


class StorageError(KotoError):
    """Base for storage and I/O failures."""

    error_code = "STORAGE_ERROR"


class PathTraversalError(StorageError):
    """Attempted path traversal detected."""

    error_code = "PATH_TRAVERSAL"


# Routing errors


class RoutingError(KotoError):
    """Base for routing failures."""

    error_code = "ROUTING_ERROR"


class TaskClassificationError(RoutingError):
    """Task could not be classified to a route."""

    error_code = "TASK_CLASSIFICATION_ERROR"
