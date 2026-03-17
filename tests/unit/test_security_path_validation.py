# -*- coding: utf-8 -*-
"""
Unit tests for path-traversal prevention and input-validation sandboxes
across the Koto codebase.

Covers:
  1. AnnotationPlugin.annotate_document — safe_dirs sandbox (workspace/uploads/dist)
  2. file_converter.convert() — _allowed_roots sandbox for output_dir
  3. WindowAPI.open_url() — URL scheme whitelist (http/https only)
  4. SkillCapabilityRegistry._load_entry_point — _ALLOWED_MODULE_PREFIXES whitelist

All tests are isolated: file-system and network operations are mocked so
nothing touches the real disk or opens a real browser.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. AnnotationPlugin  —  safe_dirs sandbox
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnnotationPluginSandbox:
    """annotate_document() must reject paths outside workspace/uploads/dist."""

    def _make_plugin(self):
        from app.core.agent.plugins.annotation_plugin import AnnotationPlugin

        return AnnotationPlugin()

    # -- relative paths --------------------------------------------------------

    def test_relative_path_rejected(self):
        """Relative paths should be rejected (must be absolute)."""
        plugin = self._make_plugin()
        result = plugin.annotate_document(file_path="../../../etc/passwd")
        assert "绝对路径" in result, "Relative path should be rejected as non-absolute"

    def test_relative_dot_path_rejected(self):
        """Dot-relative paths like ./file.docx should be rejected."""
        plugin = self._make_plugin()
        result = plugin.annotate_document(file_path="./some/file.docx")
        assert "绝对路径" in result

    def test_bare_filename_rejected(self):
        """A bare filename with no directory is relative and must be rejected."""
        plugin = self._make_plugin()
        result = plugin.annotate_document(file_path="secret.docx")
        assert "绝对路径" in result

    # -- absolute paths outside safe_dirs --------------------------------------

    def test_absolute_path_outside_safe_dirs_blocked(self):
        """Absolute path that resolves outside safe_dirs should be blocked."""
        plugin = self._make_plugin()
        # Use an OS-appropriate absolute path that is definitely outside safe_dirs
        if os.name == "nt":
            bad_path = "C:\\Windows\\system32\\evil.docx"
        else:
            bad_path = "/etc/passwd"
        result = plugin.annotate_document(file_path=bad_path)
        assert (
            "不在允许的目录范围内" in result
        ), "Absolute path outside safe_dirs must be rejected"

    def test_windows_absolute_path_outside_safe_dirs_blocked(self):
        """Windows-style absolute path outside safe_dirs should be blocked."""
        plugin = self._make_plugin()
        result = plugin.annotate_document(file_path="C:\\Windows\\system32\\cmd.exe")
        assert "不在允许的目录范围内" in result

    # -- path traversal via symlink / normalisation ----------------------------

    def test_traversal_normalised_outside_safe_dirs_blocked(self):
        """Path containing ../ that normalises outside safe_dirs must be blocked."""
        plugin = self._make_plugin()
        # Build a path that starts inside workspace but escapes via ../
        workspace = os.path.realpath(os.path.join(os.getcwd(), "workspace"))
        traversal = os.path.join(workspace, "..", "..", "..", "etc", "passwd")
        result = plugin.annotate_document(file_path=traversal)
        assert "不在允许的目录范围内" in result

    # -- valid paths inside safe_dirs ------------------------------------------

    def test_valid_workspace_path_accepted(self):
        """A .docx file under workspace/ should pass the sandbox check."""
        plugin = self._make_plugin()
        workspace = os.path.realpath(os.path.join(os.getcwd(), "workspace"))
        valid_path = os.path.join(workspace, "report.docx")

        with patch("os.path.exists", return_value=True), patch(
            "web.document_batch_annotator_v2.annotate_large_document"
        ) as mock_ann:
            mock_ann.return_value = iter(
                ['data:{"type":"complete","output_file":"out.docx","total_edits":3}']
            )
            result = plugin.annotate_document(file_path=valid_path)
        assert "不在允许的目录范围内" not in result
        assert "绝对路径" not in result

    def test_valid_uploads_path_accepted(self):
        """A .docx file under uploads/ should pass the sandbox check."""
        plugin = self._make_plugin()
        uploads = os.path.realpath(os.path.join(os.getcwd(), "uploads"))
        valid_path = os.path.join(uploads, "doc.docx")

        with patch("os.path.exists", return_value=True), patch(
            "web.document_batch_annotator_v2.annotate_large_document"
        ) as mock_ann:
            mock_ann.return_value = iter(
                ['data:{"type":"complete","output_file":"out.docx","total_edits":1}']
            )
            result = plugin.annotate_document(file_path=valid_path)
        assert "不在允许的目录范围内" not in result

    def test_valid_dist_path_accepted(self):
        """A .docx file under dist/ should pass the sandbox check."""
        plugin = self._make_plugin()
        dist = os.path.realpath(os.path.join(os.getcwd(), "dist"))
        valid_path = os.path.join(dist, "output.docx")

        with patch("os.path.exists", return_value=True), patch(
            "web.document_batch_annotator_v2.annotate_large_document"
        ) as mock_ann:
            mock_ann.return_value = iter(
                ['data:{"type":"complete","output_file":"out.docx","total_edits":0}']
            )
            result = plugin.annotate_document(file_path=valid_path)
        assert "不在允许的目录范围内" not in result

    # -- non-.docx extension ---------------------------------------------------

    def test_non_docx_rejected_even_in_safe_dir(self):
        """Even inside safe_dirs, non-.docx files must be rejected."""
        plugin = self._make_plugin()
        workspace = os.path.realpath(os.path.join(os.getcwd(), "workspace"))
        txt_path = os.path.join(workspace, "readme.txt")

        with patch("os.path.exists", return_value=True):
            result = plugin.annotate_document(file_path=txt_path)
        assert ".docx" in result, "Non-.docx files should be rejected"


# ---------------------------------------------------------------------------
# 2. file_converter.convert()  —  _allowed_roots sandbox for output_dir
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFileConverterOutputDirSandbox:
    """convert() must confine output_dir to workspace/uploads/dist."""

    def _call_convert(self, source_path, target_format, output_dir=None):
        """Import and call convert() with os.path.exists mocked for source."""
        from web.file_converter import convert

        return convert(
            source_path=source_path,
            target_format=target_format,
            output_dir=output_dir,
        )

    # -- output_dir outside allowed roots --------------------------------------

    def test_output_dir_outside_allowed_roots_blocked(self):
        """output_dir pointing to /tmp or C:\\Temp should be blocked."""
        outside_dir = os.path.abspath(os.sep + "tmp" + os.sep + "evil")
        source = os.path.join(os.path.abspath("workspace"), "file.docx")

        with patch("os.path.exists", return_value=True):
            result = self._call_convert(source, "pdf", output_dir=outside_dir)
        assert result.get("success") is not True or "不在允许的范围内" in result.get(
            "error", ""
        ), "output_dir outside allowed roots should be rejected"

    def test_output_dir_traversal_blocked(self):
        """output_dir using ../ to escape allowed roots should be blocked."""
        traversal_dir = os.path.join(os.path.abspath("workspace"), "..", "..", "tmp")
        source = os.path.join(os.path.abspath("workspace"), "file.docx")

        with patch("os.path.exists", return_value=True):
            result = self._call_convert(source, "pdf", output_dir=traversal_dir)
        # After abspath normalisation the traversal should resolve outside roots
        if os.path.abspath(traversal_dir) != os.path.abspath(os.path.dirname(source)):
            assert (
                result.get("success") is not True
            ), "Path-traversal output_dir should be blocked"

    # -- output_dir inside allowed roots ---------------------------------------

    def test_output_dir_workspace_accepted(self):
        """output_dir under workspace/ should be accepted."""
        workspace_out = os.path.abspath("workspace")
        source = os.path.join(workspace_out, "file.docx")

        with patch("os.path.exists", return_value=True), patch("os.makedirs"), patch(
            "web.file_converter._dispatch"
        ) as mock_dispatch:
            mock_dispatch.return_value = (os.path.join(workspace_out, "file.pdf"), "")
            result = self._call_convert(source, "pdf", output_dir=workspace_out)
        assert (
            result["success"] is True
        ), "output_dir inside workspace should be allowed"

    def test_output_dir_uploads_accepted(self):
        """output_dir under uploads/ should be accepted."""
        uploads_out = os.path.abspath("uploads")
        source = os.path.join(os.path.abspath("workspace"), "file.docx")

        with patch("os.path.exists", return_value=True), patch("os.makedirs"), patch(
            "web.file_converter._dispatch"
        ) as mock_dispatch:
            mock_dispatch.return_value = (os.path.join(uploads_out, "file.pdf"), "")
            result = self._call_convert(source, "pdf", output_dir=uploads_out)
        assert result["success"] is True

    def test_output_dir_dist_accepted(self):
        """output_dir under dist/ should be accepted."""
        dist_out = os.path.abspath("dist")
        source = os.path.join(os.path.abspath("workspace"), "file.docx")

        with patch("os.path.exists", return_value=True), patch("os.makedirs"), patch(
            "web.file_converter._dispatch"
        ) as mock_dispatch:
            mock_dispatch.return_value = (os.path.join(dist_out, "file.pdf"), "")
            result = self._call_convert(source, "pdf", output_dir=dist_out)
        assert result["success"] is True

    # -- fallback: same-directory as source ------------------------------------

    def test_output_dir_same_as_source_parent_allowed(self):
        """When output_dir equals the source file's parent, it's allowed
        even outside the standard roots (fallback logic)."""
        # Create a path that's outside workspace/uploads/dist
        source_parent = os.path.abspath(os.sep + "some" + os.sep + "project")
        source = os.path.join(source_parent, "file.docx")

        with patch("os.path.exists", return_value=True), patch("os.makedirs"), patch(
            "web.file_converter._dispatch"
        ) as mock_dispatch:
            mock_dispatch.return_value = (os.path.join(source_parent, "file.pdf"), "")
            result = self._call_convert(source, "pdf", output_dir=source_parent)
        assert (
            result["success"] is True
        ), "Same-directory-as-source fallback should be allowed"


# ---------------------------------------------------------------------------
# 3. WindowAPI.open_url()  —  URL scheme whitelist
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpenUrlSchemeValidation:
    """WindowAPI.open_url() must only allow http: and https: schemes."""

    def _make_api(self):
        from src.koto_app import WindowAPI

        window_mock = MagicMock()
        return WindowAPI(window=window_mock, base_url="http://127.0.0.1:5000")

    # -- blocked schemes -------------------------------------------------------

    def test_javascript_scheme_blocked(self):
        api = self._make_api()
        result = api.open_url("javascript:alert(1)")
        assert result["success"] is False, "javascript: scheme must be blocked"

    def test_file_scheme_blocked(self):
        api = self._make_api()
        result = api.open_url("file:///etc/passwd")
        assert result["success"] is False, "file: scheme must be blocked"

    def test_data_scheme_blocked(self):
        api = self._make_api()
        result = api.open_url("data:text/html,<script>alert(1)</script>")
        assert result["success"] is False, "data: scheme must be blocked"

    def test_ftp_scheme_blocked(self):
        api = self._make_api()
        result = api.open_url("ftp://evil.com/malware")
        assert result["success"] is False, "ftp: scheme must be blocked"

    def test_vbscript_scheme_blocked(self):
        api = self._make_api()
        result = api.open_url("vbscript:MsgBox(1)")
        assert result["success"] is False, "vbscript: scheme must be blocked"

    # -- allowed schemes -------------------------------------------------------

    def test_http_scheme_allowed(self):
        api = self._make_api()
        with patch("webbrowser.open") as mock_open:
            result = api.open_url("http://example.com")
        assert result["success"] is True, "http: scheme should be allowed"
        mock_open.assert_called_once_with("http://example.com")

    def test_https_scheme_allowed(self):
        api = self._make_api()
        with patch("webbrowser.open") as mock_open:
            result = api.open_url("https://example.com/page?q=1")
        assert result["success"] is True, "https: scheme should be allowed"
        mock_open.assert_called_once_with("https://example.com/page?q=1")

    # -- edge cases ------------------------------------------------------------

    def test_empty_url_handled_gracefully(self):
        """Empty string URL should not crash; parsed scheme is empty → blocked."""
        api = self._make_api()
        result = api.open_url("")
        assert result["success"] is False

    def test_scheme_only_no_netloc(self):
        """A bare 'http:' with no netloc is not a valid URL and should be rejected."""
        api = self._make_api()
        result = api.open_url("http:")
        # Implementation requires full 'http://' or 'https://' prefix
        assert result["success"] is False


# ---------------------------------------------------------------------------
# 4. SkillCapabilityRegistry._load_entry_point  —  module whitelist
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSkillCapabilityModuleWhitelist:
    """_load_entry_point() must only allow modules starting with app./web./src."""

    def _load(self, entry_point: str):
        from app.core.skills.skill_capability import SkillCapabilityRegistry

        return SkillCapabilityRegistry._load_entry_point(entry_point)

    # -- blocked modules -------------------------------------------------------

    def test_os_system_blocked(self):
        """'os:system' is not in the allowed prefix list and must raise."""
        with pytest.raises(ImportError, match="不在允许的模块前缀列表中"):
            self._load("os:system")

    def test_subprocess_run_blocked(self):
        """'subprocess:run' must be blocked."""
        with pytest.raises(ImportError, match="不在允许的模块前缀列表中"):
            self._load("subprocess:run")

    def test_sys_exit_blocked(self):
        """'sys:exit' must be blocked."""
        with pytest.raises(ImportError, match="不在允许的模块前缀列表中"):
            self._load("sys:exit")

    def test_builtins_exec_blocked(self):
        """'builtins:exec' must be blocked."""
        with pytest.raises(ImportError, match="不在允许的模块前缀列表中"):
            self._load("builtins:exec")

    def test_shutil_rmtree_blocked(self):
        """'shutil:rmtree' must be blocked."""
        with pytest.raises(ImportError, match="不在允许的模块前缀列表中"):
            self._load("shutil:rmtree")

    # -- allowed prefixes (mock importlib so we don't need real modules) -------

    def test_app_prefix_passes_whitelist(self):
        """'app.some.module:func' should pass the prefix check.
        We mock import to avoid needing the real module."""
        mock_fn = MagicMock()
        mock_module = MagicMock()
        mock_module.func = mock_fn

        with patch("importlib.import_module", return_value=mock_module):
            result = self._load("app.core.dummy:func")
        assert result is mock_fn

    def test_web_prefix_passes_whitelist(self):
        """'web.something:handler' should pass the prefix check."""
        mock_fn = MagicMock()
        mock_module = MagicMock()
        mock_module.handler = mock_fn

        with patch("importlib.import_module", return_value=mock_module):
            result = self._load("web.converter:handler")
        assert result is mock_fn

    def test_src_prefix_passes_whitelist(self):
        """'src.koto_app:some_func' should pass the prefix check."""
        mock_fn = MagicMock()
        mock_module = MagicMock()
        mock_module.some_func = mock_fn

        with patch("importlib.import_module", return_value=mock_module):
            result = self._load("src.koto_app:some_func")
        assert result is mock_fn

    # -- malformed entry points ------------------------------------------------

    def test_no_colon_separator_raises_value_error(self):
        """Entry point without ':' separator must raise ValueError."""
        with pytest.raises(ValueError, match="格式错误"):
            self._load("app.core.module.func")

    def test_empty_string_raises_value_error(self):
        """Empty entry point string must raise ValueError."""
        with pytest.raises(ValueError, match="格式错误"):
            self._load("")

    # -- dotted attr path (e.g. ClassName.method) ------------------------------

    def test_dotted_attr_path_resolved(self):
        """'app.mod:MyClass.run' should resolve to MyClass.run attribute."""
        mock_method = MagicMock()
        mock_cls = MagicMock()
        mock_cls.run = mock_method
        mock_module = MagicMock(spec=[])
        mock_module.MyClass = mock_cls

        with patch("importlib.import_module", return_value=mock_module):
            result = self._load("app.mod:MyClass.run")
        assert result is mock_method

    def test_non_callable_raises_type_error(self):
        """If the resolved object is not callable, TypeError must be raised."""
        mock_module = MagicMock()
        mock_module.CONFIG = "just-a-string"  # not callable

        with patch("importlib.import_module", return_value=mock_module):
            with pytest.raises(TypeError, match="不可调用"):
                self._load("app.settings:CONFIG")
