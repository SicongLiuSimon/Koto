"""Unit tests for graceful shutdown handlers in src/server.py."""

from __future__ import annotations

import signal
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers – import the functions under test without triggering module-level
# side-effects (signal registration, atexit, Flask app import, etc.)
# We therefore read them from the module *after* heavy-patching the imports.
# ---------------------------------------------------------------------------


def _import_cleanup_and_handler():
    """Return (_cleanup, _shutdown_handler) imported from src.server.

    Because server.py registers signals and runs setup at import time we
    need to patch several things so the import succeeds in an isolated
    test environment.
    """
    import importlib
    import types

    # Build lightweight stubs for modules that server.py imports at the
    # top level so we don't need the full application available.
    fake_app_core_logging = types.ModuleType("app.core.logging_setup")
    fake_app_core_logging.setup_logging = MagicMock()

    fake_config_validator = types.ModuleType("src.config_validator")
    fake_config_validator.validate_startup_config = MagicMock()

    fake_web_app = types.ModuleType("web.app")
    fake_web_app.app = MagicMock()

    fake_web_settings = types.ModuleType("web.settings")
    fake_web_settings.SettingsManager = MagicMock()

    fake_langsmith = types.ModuleType("app.core.monitoring.langsmith_tracer")
    fake_langsmith.init_langsmith = MagicMock()

    stubs = {
        "app.core.logging_setup": fake_app_core_logging,
        "app.core": types.ModuleType("app.core"),
        "app": types.ModuleType("app"),
        "src.config_validator": fake_config_validator,
        "web.app": fake_web_app,
        "web": types.ModuleType("web"),
        "web.settings": fake_web_settings,
        "app.core.monitoring": types.ModuleType("app.core.monitoring"),
        "app.core.monitoring.langsmith_tracer": fake_langsmith,
    }

    with patch.dict("sys.modules", stubs):
        with patch("signal.signal"):
            with patch("atexit.register"):
                import src.server as server_mod

                importlib.reload(server_mod)

    return server_mod._cleanup, server_mod._shutdown_handler


_cleanup, _shutdown_handler = _import_cleanup_and_handler()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupCallsFlush:
    """_cleanup should flush SettingsManager when it is initialised."""

    def test_flush_called(self):
        mock_instance = MagicMock()
        mock_sm = MagicMock()
        mock_sm._instance = mock_instance

        with patch.dict(
            "sys.modules",
            {"web.settings": MagicMock(SettingsManager=mock_sm)},
        ):
            _cleanup()

        mock_instance.flush.assert_called_once()

    def test_flush_not_called_when_no_instance(self):
        mock_sm = MagicMock()
        mock_sm._instance = None

        with patch.dict(
            "sys.modules",
            {"web.settings": MagicMock(SettingsManager=mock_sm)},
        ):
            _cleanup()

        # No crash, and flush never called (instance is None)


@pytest.mark.unit
class TestCleanupHandlesMissingSettings:
    """_cleanup must not crash when SettingsManager is unavailable."""

    def test_import_error(self):
        """Simulate web.settings not importable."""
        with patch.dict("sys.modules", {"web.settings": None}):
            # Should swallow the ImportError / ModuleNotFoundError
            _cleanup()  # no exception

    def test_settings_manager_attr_missing(self):
        """SettingsManager exists but has no _instance attribute."""
        mod = MagicMock(spec=[])  # empty spec → no attributes
        with patch.dict("sys.modules", {"web.settings": mod}):
            _cleanup()  # no exception


@pytest.mark.unit
class TestCleanupHandlesFlushException:
    """If flush() raises, _cleanup must swallow the exception."""

    def test_flush_raises_runtime_error(self):
        mock_instance = MagicMock()
        mock_instance.flush.side_effect = RuntimeError("disk full")
        mock_sm = MagicMock()
        mock_sm._instance = mock_instance

        with patch.dict(
            "sys.modules",
            {"web.settings": MagicMock(SettingsManager=mock_sm)},
        ):
            _cleanup()  # must not propagate

    def test_flush_raises_os_error(self):
        mock_instance = MagicMock()
        mock_instance.flush.side_effect = OSError("permission denied")
        mock_sm = MagicMock()
        mock_sm._instance = mock_instance

        with patch.dict(
            "sys.modules",
            {"web.settings": MagicMock(SettingsManager=mock_sm)},
        ):
            _cleanup()  # must not propagate


@pytest.mark.unit
class TestShutdownHandlerRaisesSystemExit:
    """_shutdown_handler must raise SystemExit(0)."""

    def test_raises_system_exit_zero(self):
        with pytest.raises(SystemExit) as exc_info:
            _shutdown_handler(signal.SIGINT, None)
        assert exc_info.value.code == 0

    def test_raises_system_exit_on_sigterm(self):
        with pytest.raises(SystemExit) as exc_info:
            _shutdown_handler(signal.SIGTERM, None)
        assert exc_info.value.code == 0


@pytest.mark.unit
class TestSignalHandlersRegistered:
    """Verify that server.py registers signal handlers for SIGINT/SIGTERM."""

    def test_signal_handlers_registered(self):
        mock_signal = MagicMock()

        with patch("signal.signal", mock_signal), patch("atexit.register"):
            import importlib
            import src.server as server_mod

            importlib.reload(server_mod)

        # Collect all (signum, handler) pairs passed to signal.signal
        registered = {call.args[0] for call in mock_signal.call_args_list}
        assert signal.SIGINT in registered, "SIGINT handler not registered"
        assert signal.SIGTERM in registered, "SIGTERM handler not registered"

    def test_atexit_registered(self):
        mock_atexit = MagicMock()

        with patch("signal.signal"), patch("atexit.register", mock_atexit):
            import importlib
            import src.server as server_mod

            importlib.reload(server_mod)

        mock_atexit.assert_called()
