"""Startup configuration validation for Koto."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when configuration is invalid."""

    pass


def validate_startup_config():
    """Validate critical configuration on startup. Logs warnings for non-fatal issues,
    raises ConfigError for fatal issues."""

    errors = []
    warnings = []

    # 1. Validate PORT
    port_str = os.environ.get("KOTO_PORT", os.environ.get("PORT", "5000"))
    try:
        port = int(port_str)
        if not (1 <= port <= 65535):
            errors.append(f"KOTO_PORT must be 1-65535, got {port}")
    except ValueError:
        errors.append(f"KOTO_PORT must be an integer, got '{port_str}'")

    # 2. Check API keys (warn, don't fail — local-only mode is valid)
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        warnings.append("GEMINI_API_KEY not set — Gemini features will be unavailable")

    # 3. Validate workspace directory
    workspace = os.environ.get("KOTO_WORKSPACE", "workspace")
    workspace_path = Path(workspace)
    if not workspace_path.exists():
        try:
            workspace_path.mkdir(parents=True, exist_ok=True)
            logger.info("Created workspace directory: %s", workspace_path)
        except OSError as e:
            warnings.append(f"Cannot create workspace directory '{workspace}': {e}")

    # 4. Check config directory is writable
    config_dir = Path("config")
    if config_dir.exists() and not os.access(str(config_dir), os.W_OK):
        warnings.append(f"Config directory '{config_dir}' is not writable")

    # 5. Check Ollama URL format (if set)
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "")
    if ollama_url and not ollama_url.startswith(("http://", "https://")):
        errors.append(
            f"OLLAMA_BASE_URL must start with http:// or https://, got '{ollama_url}'"
        )

    # Log warnings
    for w in warnings:
        logger.warning("[Config] %s", w)

    # Raise on errors
    if errors:
        for e in errors:
            logger.error("[Config] %s", e)
        raise ConfigError(
            "Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    logger.info("[Config] Startup validation passed")
