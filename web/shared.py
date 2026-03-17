"""
Shared state module for Koto web application.

This module centralizes shared globals that are used across multiple
blueprint modules. It avoids circular imports by providing a single
source of truth for application state.

Usage:
    from web.shared import get_app, settings_manager, session_manager, ...
"""
import json
import logging
import os
import sys
import threading

_logger = logging.getLogger("koto.shared")

# ─── Project root detection ──────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    PROJECT_ROOT = os.path.dirname(sys.executable)
else:
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

# ─── Directory paths ─────────────────────────────────────────────────────────
CHAT_DIR = os.path.join(PROJECT_ROOT, "chats")
UPLOAD_DIR = os.path.join(PROJECT_ROOT, "web", "uploads")

# ─── User settings cache ─────────────────────────────────────────────────────
_user_settings_cache = {}
_user_settings_lock = threading.Lock()


def _load_user_settings() -> dict:
    """Load user_settings.json with caching and safe fallbacks."""
    with _user_settings_lock:
        if "data" in _user_settings_cache:
            return _user_settings_cache["data"]
        settings_path = os.path.join(PROJECT_ROOT, "config", "user_settings.json")
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        _user_settings_cache["data"] = data
        return data


def get_workspace_root() -> str:
    """Return the workspace root directory from settings or default path."""
    settings = _load_user_settings()
    workspace_dir = settings.get("storage", {}).get("workspace_dir")
    if workspace_dir:
        return workspace_dir
    return os.path.join(PROJECT_ROOT, "workspace")


def get_organize_root() -> str:
    """Return the file organization root directory from settings or default path."""
    settings = _load_user_settings()
    organize_root = settings.get("storage", {}).get("organize_root")
    if organize_root:
        return organize_root
    return os.path.join(get_workspace_root(), "_organize")


def get_default_wechat_files_dir() -> str:
    """Return configured default WeChat files directory."""
    settings = _load_user_settings()
    return settings.get("storage", {}).get("wechat_files_dir", "")


def clear_user_settings_cache():
    """Invalidate user settings cache (e.g. after settings update)."""
    _user_settings_cache.clear()


WORKSPACE_DIR = get_workspace_root()

# ─── Ensure directories exist ────────────────────────────────────────────────
os.makedirs(CHAT_DIR, exist_ok=True)
os.makedirs(WORKSPACE_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ─── Flask app reference (set by app.py during init) ─────────────────────────
_flask_app = None


def set_app(app):
    """Store the Flask app reference for blueprints to use."""
    global _flask_app
    _flask_app = app


def get_app():
    """Get the Flask app reference."""
    return _flask_app


# ─── Settings Manager ────────────────────────────────────────────────────────
try:
    from settings import SettingsManager
except ImportError:
    from web.settings import SettingsManager

settings_manager = SettingsManager()

# ─── Error response helper ───────────────────────────────────────────────────


def _error_response(message: str, status_code: int = 500, error_type: str = None):
    """Create a standardized JSON error response."""
    from flask import jsonify
    payload = {"error": message, "success": False}
    if error_type:
        payload["type"] = error_type
    return jsonify(payload), status_code
