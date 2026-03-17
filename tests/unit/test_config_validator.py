# -*- coding: utf-8 -*-
"""Unit tests for src.config_validator.validate_startup_config."""

import logging
import os
from unittest.mock import patch

import pytest

from src.config_validator import ConfigError, validate_startup_config

_LOGGER = "src.config_validator"


def _clear_env(monkeypatch):
    """Remove all env vars that validate_startup_config reads."""
    for key in (
        "KOTO_PORT",
        "PORT",
        "GEMINI_API_KEY",
        "API_KEY",
        "KOTO_WORKSPACE",
        "OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.unit
class TestValidConfig:
    """Validation passes with reasonable defaults and explicit good values."""

    def test_valid_config_no_exception(self, monkeypatch, tmp_path):
        _clear_env(monkeypatch)
        monkeypatch.setenv("KOTO_PORT", "5000")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
        monkeypatch.setenv("KOTO_WORKSPACE", str(tmp_path / "ws"))
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
        validate_startup_config()  # should not raise

    def test_no_env_vars_uses_defaults(self, monkeypatch, tmp_path):
        _clear_env(monkeypatch)
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("KOTO_WORKSPACE", str(tmp_path / "ws"))
        validate_startup_config()  # defaults to port 5000, no OLLAMA_BASE_URL


@pytest.mark.unit
class TestPortValidation:
    """KOTO_PORT must be a valid integer in 1-65535."""

    def test_bad_port_non_numeric(self, monkeypatch, tmp_path):
        _clear_env(monkeypatch)
        monkeypatch.setenv("KOTO_PORT", "abc")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("KOTO_WORKSPACE", str(tmp_path / "ws"))
        with pytest.raises(ConfigError, match="must be an integer"):
            validate_startup_config()

    def test_bad_port_zero(self, monkeypatch, tmp_path):
        _clear_env(monkeypatch)
        monkeypatch.setenv("KOTO_PORT", "0")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("KOTO_WORKSPACE", str(tmp_path / "ws"))
        with pytest.raises(ConfigError, match="must be 1-65535"):
            validate_startup_config()

    def test_bad_port_too_high(self, monkeypatch, tmp_path):
        _clear_env(monkeypatch)
        monkeypatch.setenv("KOTO_PORT", "99999")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("KOTO_WORKSPACE", str(tmp_path / "ws"))
        with pytest.raises(ConfigError, match="must be 1-65535"):
            validate_startup_config()

    def test_valid_port(self, monkeypatch, tmp_path):
        _clear_env(monkeypatch)
        monkeypatch.setenv("KOTO_PORT", "8080")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("KOTO_WORKSPACE", str(tmp_path / "ws"))
        validate_startup_config()  # should not raise


@pytest.mark.unit
class TestGeminiApiKeyWarning:
    """Missing GEMINI_API_KEY logs a warning but does not fail."""

    def test_missing_gemini_key_warns(self, monkeypatch, tmp_path, caplog):
        _clear_env(monkeypatch)
        monkeypatch.setenv("KOTO_PORT", "5000")
        monkeypatch.setenv("KOTO_WORKSPACE", str(tmp_path / "ws"))
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            validate_startup_config()  # should NOT raise
        assert any("GEMINI_API_KEY not set" in m for m in caplog.messages)


@pytest.mark.unit
class TestOllamaUrlValidation:
    """OLLAMA_BASE_URL must start with http:// or https:// when set."""

    def test_bad_ollama_url(self, monkeypatch, tmp_path):
        _clear_env(monkeypatch)
        monkeypatch.setenv("KOTO_PORT", "5000")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("KOTO_WORKSPACE", str(tmp_path / "ws"))
        monkeypatch.setenv("OLLAMA_BASE_URL", "not-a-url")
        with pytest.raises(ConfigError, match="OLLAMA_BASE_URL must start with"):
            validate_startup_config()

    def test_valid_ollama_url(self, monkeypatch, tmp_path):
        _clear_env(monkeypatch)
        monkeypatch.setenv("KOTO_PORT", "5000")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("KOTO_WORKSPACE", str(tmp_path / "ws"))
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
        validate_startup_config()  # should not raise


@pytest.mark.unit
class TestConfigDirWritability:
    """Non-writable config directory produces a warning, not an error."""

    def test_non_writable_config_dir_warns(self, monkeypatch, tmp_path, caplog):
        _clear_env(monkeypatch)
        monkeypatch.setenv("KOTO_PORT", "5000")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("KOTO_WORKSPACE", str(tmp_path / "ws"))

        with patch("src.config_validator.Path") as MockPath, patch(
            "src.config_validator.os.access", return_value=False
        ):
            config_sentinel = MockPath.return_value
            config_sentinel.exists.return_value = True

            # workspace path must still behave normally
            workspace_sentinel = MockPath.return_value
            # Because Path() is called twice (config + workspace), we use side_effect
            real_path_cls = pytest.importorskip("pathlib").Path
            ws_path = real_path_cls(str(tmp_path / "ws"))

            def path_side_effect(arg):
                if arg == "config":
                    mock_obj = type(
                        "FakePath",
                        (),
                        {
                            "exists": lambda self: True,
                            "__str__": lambda self: "config",
                        },
                    )()
                    return mock_obj
                return real_path_cls(arg)

            MockPath.side_effect = path_side_effect

            with caplog.at_level(logging.WARNING, logger=_LOGGER):
                validate_startup_config()

        assert any("not writable" in m for m in caplog.messages)
