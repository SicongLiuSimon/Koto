"""Unit tests for src/ modules with low coverage:
  - koto_setup.py (0%)
  - model_downloader.py (0%)
  - local_model_installer.py (9%)
  - koto_app.py (14%)

All external dependencies (tkinter, subprocess, network, file I/O, GUI)
are mocked so tests run headless and offline.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock, Mock, PropertyMock, call, mock_open, patch

import pytest

# ---------------------------------------------------------------------------
# Pre-mock heavy GUI / optional modules before any src imports
# ---------------------------------------------------------------------------
_GUI_MOCKS = [
    "tkinter",
    "tkinter.ttk",
    "tkinter.font",
    "tkinter.messagebox",
    "tkinter.scrolledtext",
    "ttkbootstrap",
    "webview",
    "pystray",
    "pystray.Icon",
    "pystray.Menu",
    "pystray.MenuItem",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
]
for _mod in _GUI_MOCKS:
    sys.modules.setdefault(_mod, MagicMock())

# Ensure src/ is importable
if "src" not in sys.path:
    sys.path.insert(0, "src")


# ═══════════════════════════════════════════════════════════════════════════
#  TestKotoSetup
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestKotoSetup:
    """Tests for src/koto_setup.py utility functions."""

    # -- helpers to avoid importing module-level side effects ----------------
    @staticmethod
    def _import_module():
        """Import koto_setup with side effects suppressed."""
        with patch("pathlib.Path.mkdir"):
            import koto_setup
        return koto_setup

    # -- _write_gemini_config -----------------------------------------------

    def test_write_gemini_config_creates_file(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            (tmp_path / "config").mkdir(parents=True, exist_ok=True)
            mod._write_gemini_config(
                "AIzaTestKey12345678901234567890", "https://custom.api"
            )
            content = (tmp_path / "config" / "gemini_config.env").read_text(
                encoding="utf-8"
            )
            assert "GEMINI_API_KEY=AIzaTestKey12345678901234567890" in content
            assert "GEMINI_API_BASE=https://custom.api" in content
            assert "FORCE_PROXY=auto" in content
        finally:
            mod.APP_ROOT = orig

    def test_write_gemini_config_default_base(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            (tmp_path / "config").mkdir(parents=True, exist_ok=True)
            mod._write_gemini_config("AIzaKey123456789012345678901234")
            content = (tmp_path / "config" / "gemini_config.env").read_text(
                encoding="utf-8"
            )
            assert "GEMINI_API_BASE=\n" in content
        finally:
            mod.APP_ROOT = orig

    # -- _api_key_configured ------------------------------------------------

    def test_api_key_configured_returns_true_for_valid_key(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            cfg = tmp_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(
                "GEMINI_API_KEY=AIzaRealKeyValue1234567890\n", encoding="utf-8"
            )
            assert mod._api_key_configured() is True
        finally:
            mod.APP_ROOT = orig

    def test_api_key_configured_returns_false_no_file(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            assert mod._api_key_configured() is False
        finally:
            mod.APP_ROOT = orig

    def test_api_key_configured_returns_false_for_placeholder(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            cfg = tmp_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text("GEMINI_API_KEY=your_api_key_here\n", encoding="utf-8")
            assert mod._api_key_configured() is False
        finally:
            mod.APP_ROOT = orig

    def test_api_key_configured_returns_false_for_none(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            cfg = tmp_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text("GEMINI_API_KEY=None\n", encoding="utf-8")
            assert mod._api_key_configured() is False
        finally:
            mod.APP_ROOT = orig

    def test_api_key_configured_returns_false_for_empty(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            cfg = tmp_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text("GEMINI_API_KEY=\n", encoding="utf-8")
            assert mod._api_key_configured() is False
        finally:
            mod.APP_ROOT = orig

    # -- _read_config_values ------------------------------------------------

    def test_read_config_values_returns_key_and_base(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            cfg = tmp_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(
                "GEMINI_API_KEY=mykey123\nGEMINI_API_BASE=https://custom\n",
                encoding="utf-8",
            )
            key, base = mod._read_config_values()
            assert key == "mykey123"
            assert base == "https://custom"
        finally:
            mod.APP_ROOT = orig

    def test_read_config_values_empty_when_no_file(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            key, base = mod._read_config_values()
            assert key == ""
            assert base == ""
        finally:
            mod.APP_ROOT = orig

    # -- _validate_api_key --------------------------------------------------

    def test_validate_api_key_success(self):
        mod = self._import_module()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            ok, msg = mod._validate_api_key("AIzaTestKey")
            assert ok is True
            assert msg == ""

    def test_validate_api_key_http_400(self):
        mod = self._import_module()
        import urllib.error

        err = urllib.error.HTTPError("url", 400, "Bad", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            ok, msg = mod._validate_api_key("bad_key")
            assert ok is False
            assert "密钥无效" in msg

    def test_validate_api_key_http_403(self):
        mod = self._import_module()
        import urllib.error

        err = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            ok, msg = mod._validate_api_key("forbidden_key")
            assert ok is False
            assert "密钥被拒绝" in msg

    def test_validate_api_key_network_error(self):
        mod = self._import_module()
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            ok, msg = mod._validate_api_key("key")
            assert ok is False
            assert msg.startswith("⚠️")

    def test_validate_api_key_custom_base(self):
        mod = self._import_module()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            mod._validate_api_key("AIzaKey", "https://my-proxy.com/")
            # Verify custom base URL was used
            req_obj = mock_open.call_args[0][0]
            assert "my-proxy.com" in req_obj.full_url

    # -- _run_setup_if_needed -----------------------------------------------

    def test_run_setup_skips_when_key_valid(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            cfg = tmp_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(
                "GEMINI_API_KEY=AIzaValidKey1234567890123456\n", encoding="utf-8"
            )
            with patch.object(mod, "_validate_api_key", return_value=(True, "")):
                mod._run_setup_if_needed()
        finally:
            mod.APP_ROOT = orig

    def test_run_setup_skips_on_network_error(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            cfg = tmp_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(
                "GEMINI_API_KEY=AIzaValidKey1234567890123456\n", encoding="utf-8"
            )
            with patch.object(
                mod, "_validate_api_key", return_value=(False, "⚠️ timeout")
            ):
                mod._run_setup_if_needed()
                # Network error with ⚠️ prefix should not block startup
        finally:
            mod.APP_ROOT = orig

    def test_run_setup_shows_wizard_when_key_invalid(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            cfg = tmp_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(
                "GEMINI_API_KEY=AIzaValidKey1234567890123456\n", encoding="utf-8"
            )
            wizard_result = {"key": None, "base": "", "cancelled": True}
            with patch.object(
                mod, "_validate_api_key", return_value=(False, "❌ 密钥无效")
            ):
                with patch.object(
                    mod, "_show_api_setup_wizard", return_value=wizard_result
                ) as mock_wiz:
                    mod._run_setup_if_needed()
                    mock_wiz.assert_called_once()
                    # Verify status message passed to wizard
                    call_args = mock_wiz.call_args
                    assert "密钥无效" in call_args[1].get(
                        "initial_status", call_args[0][0] if call_args[0] else ""
                    )
        finally:
            mod.APP_ROOT = orig


# ═══════════════════════════════════════════════════════════════════════════
#  TestModelDownloader
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestModelDownloader:
    """Tests for src/model_downloader.py utility functions."""

    @staticmethod
    def _import_module():
        with patch("pathlib.Path.mkdir"):
            import model_downloader
        return model_downloader

    # -- MODEL_CATALOG structure -------------------------------------------

    def test_model_catalog_is_nonempty_list(self):
        mod = self._import_module()
        assert isinstance(mod.MODEL_CATALOG, list)
        assert len(mod.MODEL_CATALOG) >= 4

    def test_model_catalog_entries_have_required_keys(self):
        mod = self._import_module()
        required = {"tag", "name", "vram", "ram", "size_gb", "desc", "tier"}
        for m in mod.MODEL_CATALOG:
            assert required.issubset(m.keys()), f"Missing keys in {m.get('tag', '?')}"

    # -- recommend_models --------------------------------------------------

    def test_recommend_models_returns_list(self):
        mod = self._import_module()
        info = {"ram_gb": 8, "gpu_vram_gb": 0}
        result = mod.recommend_models(info)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_recommend_models_fallback_for_low_resources(self):
        mod = self._import_module()
        info = {"ram_gb": 0.5, "gpu_vram_gb": 0}
        result = mod.recommend_models(info)
        assert len(result) >= 1
        assert result[0]["tag"] == mod.MODEL_CATALOG[0]["tag"]

    def test_recommend_models_high_resources_gets_all(self):
        mod = self._import_module()
        info = {"ram_gb": 64, "gpu_vram_gb": 24}
        result = mod.recommend_models(info)
        assert len(result) == len(mod.MODEL_CATALOG)

    def test_recommend_models_gpu_vram_factor(self):
        """GPU VRAM is scaled by 1.5x as effective resource."""
        mod = self._import_module()
        # 4 GB VRAM * 1.5 = 6 effective → should match models requiring ≤6 RAM
        info = {"ram_gb": 0, "gpu_vram_gb": 4}
        result = mod.recommend_models(info)
        assert len(result) >= 1

    # -- is_ollama_running -------------------------------------------------

    def test_is_ollama_running_true(self):
        mod = self._import_module()
        mock_sock = MagicMock()
        with patch("socket.create_connection", return_value=mock_sock):
            assert mod.is_ollama_running() is True
            mock_sock.close.assert_called_once()

    def test_is_ollama_running_false(self):
        mod = self._import_module()
        with patch("socket.create_connection", side_effect=OSError):
            assert mod.is_ollama_running() is False

    # -- start_ollama_server -----------------------------------------------

    def test_start_ollama_server_already_running(self):
        mod = self._import_module()
        with patch.object(mod, "is_ollama_running", return_value=True):
            assert mod.start_ollama_server() is True

    def test_start_ollama_server_starts_and_succeeds(self):
        mod = self._import_module()
        call_count = 0

        def running_after_2():
            nonlocal call_count
            call_count += 1
            return call_count >= 2

        with patch.object(mod, "is_ollama_running", side_effect=running_after_2):
            with patch("subprocess.Popen"):
                with patch("time.sleep"):
                    cb = Mock()
                    assert mod.start_ollama_server(log_callback=cb) is True
                    cb.assert_any_call("正在启动 Ollama 服务...")

    def test_start_ollama_server_timeout(self):
        mod = self._import_module()
        with patch.object(mod, "is_ollama_running", return_value=False):
            with patch("subprocess.Popen"):
                with patch("time.sleep"):
                    assert mod.start_ollama_server() is False

    # -- pull_model --------------------------------------------------------

    def test_pull_model_success(self):
        mod = self._import_module()
        mock_proc = MagicMock()
        mock_proc.stdout = iter(["pulling 50%\n", "pulling 100%\n"])
        mock_proc.returncode = 0
        with patch("subprocess.Popen", return_value=mock_proc):
            prog_cb = Mock()
            log_cb = Mock()
            assert (
                mod.pull_model(
                    "gemma3:1b", progress_callback=prog_cb, log_callback=log_cb
                )
                is True
            )
            prog_cb.assert_called()

    def test_pull_model_not_found(self):
        mod = self._import_module()
        with patch("subprocess.Popen", side_effect=FileNotFoundError):
            log_cb = Mock()
            assert mod.pull_model("gemma3:1b", log_callback=log_cb) is False
            log_cb.assert_called_once()

    # -- save_setup_result -------------------------------------------------

    def test_save_setup_result_writes_flag(self, tmp_path):
        mod = self._import_module()
        orig_flag = mod.SETUP_FLAG
        orig_root = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            mod.SETUP_FLAG = tmp_path / "config" / "model_setup_done.json"
            mod.SETUP_FLAG.parent.mkdir(parents=True, exist_ok=True)
            mod.save_setup_result("gemma3:1b", mode="local")
            data = json.loads(mod.SETUP_FLAG.read_text(encoding="utf-8"))
            assert data["done"] is True
            assert data["model"] == "gemma3:1b"
            assert data["mode"] == "local"
        finally:
            mod.SETUP_FLAG = orig_flag
            mod.APP_ROOT = orig_root

    # -- is_setup_done -----------------------------------------------------

    def test_is_setup_done_true(self, tmp_path):
        mod = self._import_module()
        orig = mod.SETUP_FLAG
        try:
            flag = tmp_path / "flag.json"
            flag.write_text("{}", encoding="utf-8")
            mod.SETUP_FLAG = flag
            assert mod.is_setup_done() is True
        finally:
            mod.SETUP_FLAG = orig

    def test_is_setup_done_false(self, tmp_path):
        mod = self._import_module()
        orig = mod.SETUP_FLAG
        try:
            mod.SETUP_FLAG = tmp_path / "nonexistent.json"
            assert mod.is_setup_done() is False
        finally:
            mod.SETUP_FLAG = orig

    # -- maybe_run_setup ---------------------------------------------------

    def test_maybe_run_setup_already_done(self):
        mod = self._import_module()
        with patch.object(mod, "is_setup_done", return_value=True):
            assert mod.maybe_run_setup() is True


# ═══════════════════════════════════════════════════════════════════════════
#  TestLocalModelInstaller
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestLocalModelInstaller:
    """Tests for src/local_model_installer.py utility functions."""

    @staticmethod
    def _import_module():
        with patch("pathlib.Path.mkdir"):
            import local_model_installer
        return local_model_installer

    # -- MODEL_CATALOG structure-------------------------------------------

    def test_model_catalog_nonempty(self):
        mod = self._import_module()
        assert len(mod.MODEL_CATALOG) >= 5

    def test_model_catalog_has_badges(self):
        mod = self._import_module()
        for m in mod.MODEL_CATALOG:
            assert "badge" in m, f"Missing badge in {m['tag']}"

    def test_tier_color_maps_all_tiers(self):
        mod = self._import_module()
        tiers = {m["tier"] for m in mod.MODEL_CATALOG}
        for tier in tiers:
            assert tier in mod.TIER_COLOR, f"Tier {tier} not in TIER_COLOR"

    # -- recommend_models --------------------------------------------------

    def test_recommend_models_returns_non_empty(self):
        mod = self._import_module()
        info = {"ram_gb": 8, "gpu_vram_gb": 4}
        result = mod.recommend_models(info)
        assert isinstance(result, list)
        assert len(result) >= 1
        for m in result:
            assert "tag" in m and "ram" in m and "vram" in m

    def test_recommend_models_filters_by_resources(self):
        mod = self._import_module()
        info = {"ram_gb": 8, "gpu_vram_gb": 0}
        result = mod.recommend_models(info)
        eff = max(8, 0 * 1.5)
        for m in result:
            assert eff >= m["ram"] or 0 >= m["vram"]

    def test_recommend_models_low_resources_fallback(self):
        mod = self._import_module()
        result = mod.recommend_models({"ram_gb": 0.5, "gpu_vram_gb": 0})
        assert len(result) >= 1
        assert result[0]["tag"] == mod.MODEL_CATALOG[0]["tag"]

    def test_recommend_models_gpu_vram_factor(self):
        """GPU VRAM is scaled by 1.5× as effective resource."""
        mod = self._import_module()
        info = {"ram_gb": 0, "gpu_vram_gb": 4}
        result = mod.recommend_models(info)
        assert len(result) >= 1
        for m in result:
            assert 6.0 >= m["ram"] or 4 >= m["vram"]

    # -- _find_ollama_exe --------------------------------------------------

    def test_find_ollama_exe_in_path(self):
        mod = self._import_module()
        with patch("shutil.which", return_value="/usr/bin/ollama"):
            assert mod._find_ollama_exe() == "/usr/bin/ollama"

    def test_find_ollama_exe_not_found(self):
        mod = self._import_module()
        with patch("shutil.which", return_value=None):
            with patch("pathlib.Path.exists", return_value=False):
                assert mod._find_ollama_exe() is None

    # -- is_ollama_running -------------------------------------------------

    def test_is_ollama_running_true(self):
        mod = self._import_module()
        mock_sock = MagicMock()
        with patch("socket.create_connection", return_value=mock_sock):
            assert mod.is_ollama_running() is True

    def test_is_ollama_running_false(self):
        mod = self._import_module()
        with patch("socket.create_connection", side_effect=ConnectionRefusedError):
            assert mod.is_ollama_running() is False

    # -- start_ollama ------------------------------------------------------

    def test_start_ollama_already_running(self):
        mod = self._import_module()
        with patch.object(mod, "is_ollama_running", return_value=True):
            assert mod.start_ollama() is True

    def test_start_ollama_no_exe(self):
        mod = self._import_module()
        with patch.object(mod, "is_ollama_running", return_value=False):
            with patch.object(mod, "_find_ollama_exe", return_value=None):
                cb = Mock()
                assert mod.start_ollama(log_cb=cb) is False

    # -- save_result -------------------------------------------------------

    def test_save_result_writes_json(self, tmp_path):
        mod = self._import_module()
        orig = mod.RESULT_FILE
        try:
            result_file = tmp_path / "installed.json"
            mod.RESULT_FILE = result_file
            mod.save_result("gemma3:1b")
            data = json.loads(result_file.read_text(encoding="utf-8"))
            assert data["model"] == "gemma3:1b"
            assert "installed_at" in data
            assert data["ollama_endpoint"] == "http://127.0.0.1:11434"
        finally:
            mod.RESULT_FILE = orig

    # -- pull_model --------------------------------------------------------

    def test_pull_model_no_exe(self):
        mod = self._import_module()
        with patch.object(mod, "_find_ollama_exe", return_value=None):
            cb = Mock()
            assert mod.pull_model("gemma3:1b", log_cb=cb) is False

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_pull_model_success_parses_progress(self):
        mod = self._import_module()
        mock_proc = MagicMock()
        mock_proc.stdout = iter(
            ["pulling manifest\n", "\x1b[32m50%\x1b[0m done\n", "100% complete\n"]
        )
        mock_proc.returncode = 0
        mock_proc.wait = Mock()
        with patch.object(mod, "_find_ollama_exe", return_value="ollama"):
            with patch("subprocess.Popen", return_value=mock_proc):
                prog_cb = Mock()
                log_cb = Mock()
                assert (
                    mod.pull_model("gemma3:1b", prog_cb=prog_cb, log_cb=log_cb) is True
                )
                prog_cb.assert_called()


# ═══════════════════════════════════════════════════════════════════════════
#  TestKotoApp
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestKotoApp:
    """Tests for src/koto_app.py utility functions and classes."""

    @staticmethod
    def _import_module():
        """Import koto_app with heavy side effects suppressed."""
        # Mock psutil so the import doesn't fail
        mock_psutil = MagicMock()
        mock_psutil.virtual_memory.return_value = MagicMock(total=16 * 1024**3)
        mock_psutil.disk_usage.return_value = MagicMock(free=100 * 1024**3)
        sys.modules.setdefault("psutil", mock_psutil)

        # Mock faulthandler
        sys.modules.setdefault("faulthandler", MagicMock())

        with patch("pathlib.Path.mkdir"), patch("builtins.open", mock_open()), patch(
            "os.chdir"
        ):
            import koto_app
        return koto_app

    # -- DualOutput --------------------------------------------------------

    def test_dual_output_write(self, tmp_path):
        mod = self._import_module()
        log_file = tmp_path / "test.log"
        original = MagicMock()
        dual = mod.DualOutput(original, log_file)
        dual.write("hello")
        original.write.assert_called_with("hello")
        dual.close()

    def test_dual_output_flush(self):
        mod = self._import_module()
        original = MagicMock()
        dual = mod.DualOutput.__new__(mod.DualOutput)
        dual.original_stream = original
        dual._file = MagicMock()
        dual._lock = MagicMock()
        dual.flush()
        original.flush.assert_called_once()

    def test_dual_output_close(self):
        mod = self._import_module()
        dual = mod.DualOutput.__new__(mod.DualOutput)
        dual._file = MagicMock()
        dual.close()
        assert dual._file is None

    # -- WindowAPI ---------------------------------------------------------

    def test_window_api_get_mode_default(self):
        mod = self._import_module()
        window = MagicMock()
        api = mod.WindowAPI(window, "http://127.0.0.1:5000")
        assert api.get_mode() == {"mode": "full"}

    def test_window_api_get_mode_after_switch_mini(self):
        mod = self._import_module()
        window = MagicMock()
        window.width = 1200
        window.height = 800
        api = mod.WindowAPI(window, "http://127.0.0.1:5000")
        with patch.dict("sys.modules", {"ctypes": MagicMock()}):
            import ctypes

            mock_user32 = MagicMock()
            mock_user32.GetSystemMetrics.return_value = 1920
            with patch("ctypes.windll", create=True) as mock_windll:
                mock_windll.user32 = mock_user32
                api.switch_to_mini()
        assert api.is_mini_mode is True
        assert api.get_mode() == {"mode": "mini"}

    def test_window_api_minimize(self):
        mod = self._import_module()
        window = MagicMock()
        api = mod.WindowAPI(window, "http://127.0.0.1:5000")
        api.minimize()
        window.minimize.assert_called_once()

    def test_window_api_close(self):
        mod = self._import_module()
        window = MagicMock()
        api = mod.WindowAPI(window, "http://127.0.0.1:5000")
        api.close()
        window.destroy.assert_called_once()

    def test_window_api_open_url_success(self):
        mod = self._import_module()
        window = MagicMock()
        api = mod.WindowAPI(window, "http://127.0.0.1:5000")
        with patch("webbrowser.open"):
            result = api.open_url("https://example.com")
            assert result["success"] is True

    def test_window_api_open_url_rejects_bad_scheme(self):
        mod = self._import_module()
        window = MagicMock()
        api = mod.WindowAPI(window, "http://127.0.0.1:5000")
        result = api.open_url("ftp://example.com")
        assert result["success"] is False

    def test_window_api_init(self):
        mod = self._import_module()
        window = MagicMock()
        api = mod.WindowAPI(window, "http://127.0.0.1:5000/")
        assert api.base_url == "http://127.0.0.1:5000"
        assert api.is_mini_mode is False
        assert api.full_size == (1200, 800)

    # -- _pre_check_syntax -------------------------------------------------

    def test_pre_check_syntax_valid(self, tmp_path):
        mod = self._import_module()
        py_file = tmp_path / "valid.py"
        py_file.write_text("x = 1\nprint(x)\n", encoding="utf-8")
        ok, err = mod._pre_check_syntax(str(py_file))
        assert ok is True
        assert err is None

    def test_pre_check_syntax_invalid(self, tmp_path):
        mod = self._import_module()
        py_file = tmp_path / "invalid.py"
        py_file.write_text("def foo(\n", encoding="utf-8")
        ok, err = mod._pre_check_syntax(str(py_file))
        assert ok is False
        assert err is not None

    # -- _auto_fix_syntax --------------------------------------------------

    def test_auto_fix_syntax_no_match(self, tmp_path):
        mod = self._import_module()
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n", encoding="utf-8")
        result = mod._auto_fix_syntax(str(py_file), "some other error")
        assert result is False

    # -- _wait_for_port ----------------------------------------------------

    def test_wait_for_port_immediate_success(self):
        mod = self._import_module()
        with patch("socket.socket") as mock_cls:
            sock = MagicMock()
            sock.connect_ex.return_value = 0
            mock_cls.return_value = sock
            with patch("time.time", side_effect=[0, 0, 1]):
                with patch("time.sleep"):
                    assert mod._wait_for_port("127.0.0.1", 5000, 5) is True

    def test_wait_for_port_timeout(self):
        mod = self._import_module()
        call_count = [0]

        def advancing_time():
            call_count[0] += 1
            return call_count[0] * 2

        with patch("socket.socket") as mock_cls:
            sock = MagicMock()
            sock.connect_ex.return_value = 1  # not connected
            mock_cls.return_value = sock
            with patch("time.time", side_effect=advancing_time):
                with patch("time.sleep"):
                    assert mod._wait_for_port("127.0.0.1", 5000, 3) is False

    # -- _check_http_ok ----------------------------------------------------

    def test_check_http_ok_success(self):
        mod = self._import_module()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        with patch("urllib.request.build_opener", return_value=mock_opener):
            assert mod._check_http_ok("http://127.0.0.1:5000/api/health") is True

    def test_check_http_ok_failure(self):
        mod = self._import_module()
        with patch("urllib.request.build_opener", side_effect=OSError):
            assert mod._check_http_ok("http://bad") is False

    # -- _find_available_port ----------------------------------------------

    def test_find_available_port_first_free(self):
        mod = self._import_module()
        with patch("socket.socket") as mock_cls:
            sock = MagicMock()
            sock.__enter__ = Mock(return_value=sock)
            sock.__exit__ = Mock(return_value=False)
            sock.connect_ex.return_value = 1  # refused = free
            mock_cls.return_value = sock
            assert mod._find_available_port("127.0.0.1", 5000) == 5000

    def test_find_available_port_none_available(self):
        mod = self._import_module()
        with patch("socket.socket") as mock_cls:
            sock = MagicMock()
            sock.__enter__ = Mock(return_value=sock)
            sock.__exit__ = Mock(return_value=False)
            sock.connect_ex.return_value = 0  # all in use
            mock_cls.return_value = sock
            assert mod._find_available_port("127.0.0.1", 5000, max_tries=3) is None

    # -- ensure_directories ------------------------------------------------

    def test_ensure_directories_creates_dirs(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            with patch.object(mod, "_write_log"):
                mod.ensure_directories()
            assert (tmp_path / "workspace").exists()
            assert (tmp_path / "chats").exists()
            assert (tmp_path / "logs").exists()
            assert (tmp_path / "config").exists()
        finally:
            mod.APP_ROOT = orig

    # -- check_config ------------------------------------------------------

    def test_check_config_creates_placeholder(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            with patch.object(mod, "_write_log"):
                mod.check_config()
            cfg = tmp_path / "config" / "gemini_config.env"
            assert cfg.exists()
            content = cfg.read_text(encoding="utf-8")
            assert "your_api_key_here" in content
        finally:
            mod.APP_ROOT = orig

    def test_check_config_existing_file_untouched(self, tmp_path):
        mod = self._import_module()
        orig = mod.APP_ROOT
        try:
            mod.APP_ROOT = tmp_path
            cfg = tmp_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text("GEMINI_API_KEY=real_key\n", encoding="utf-8")
            with patch.object(mod, "_write_log"):
                mod.check_config()
            assert cfg.read_text(encoding="utf-8") == "GEMINI_API_KEY=real_key\n"
        finally:
            mod.APP_ROOT = orig

    # -- VoiceAPI ----------------------------------------------------------

    def test_voice_api_get_engines_fallback(self):
        mod = self._import_module()
        api = mod.VoiceAPI()
        # Without the real module, should return empty list
        result = api.get_available_engines()
        assert isinstance(result, list)
