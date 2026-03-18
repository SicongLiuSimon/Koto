#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Deep coverage tests for web/app.py internal classes and functions.

Targets large, under-tested components: KotoBrain, TaskOrchestrator,
_TrackedModels, run_with_timeout, run_with_heartbeat,
stream_with_keepalive, LocalDispatcher, auto_save_files, and many more.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, PropertyMock, call, patch

import pytest

try:
    import google.genai._api_client  # noqa: F401

    HAS_GENAI = True
except (ImportError, ModuleNotFoundError):
    HAS_GENAI = False

# ---------------------------------------------------------------------------
# Env setup before importing the app
# ---------------------------------------------------------------------------
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("KOTO_AUTH_ENABLED", "false")
os.environ.setdefault("KOTO_DEPLOY_MODE", "local")
os.environ.setdefault("GEMINI_API_KEY", "test-key-for-unit-tests")


# =====================================================================
# Helpers
# =====================================================================
def _import_app():
    """Import web.app lazily so env vars are already set."""
    import web.app as app_mod

    return app_mod


# =====================================================================
# 1. _get_local_model_config
# =====================================================================
@pytest.mark.unit
class TestGetLocalModelConfig:
    def test_returns_cloud_none_on_missing_file(self, tmp_path):
        app = _import_app()
        with patch.object(app, "PROJECT_ROOT", str(tmp_path)):
            mode, tag = app._get_local_model_config()
        assert mode == "cloud"
        assert tag is None

    def test_reads_local_mode(self, tmp_path):
        app = _import_app()
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "user_settings.json").write_text(
            json.dumps({"model_mode": "local", "local_model": "qwen2.5:7b"}),
            encoding="utf-8",
        )
        with patch.object(app, "PROJECT_ROOT", str(tmp_path)):
            mode, tag = app._get_local_model_config()
        assert mode == "local"
        assert tag == "qwen2.5:7b"

    def test_defaults_cloud_when_mode_missing(self, tmp_path):
        app = _import_app()
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "user_settings.json").write_text("{}", encoding="utf-8")
        with patch.object(app, "PROJECT_ROOT", str(tmp_path)):
            mode, tag = app._get_local_model_config()
        assert mode == "cloud"
        assert tag is None

    def test_handles_corrupt_json(self, tmp_path):
        app = _import_app()
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "user_settings.json").write_text("NOT JSON", encoding="utf-8")
        with patch.object(app, "PROJECT_ROOT", str(tmp_path)):
            mode, tag = app._get_local_model_config()
        assert mode == "cloud"
        assert tag is None


# =====================================================================
# 2. _extract_prompt_text
# =====================================================================
@pytest.mark.unit
class TestExtractPromptText:
    def test_none_contents(self):
        app = _import_app()
        text, sys = app._extract_prompt_text(None)
        assert text == ""
        assert sys is None

    def test_string_contents(self):
        app = _import_app()
        text, sys = app._extract_prompt_text("hello world")
        assert text == "hello world"
        assert sys is None

    def test_list_of_strings(self):
        app = _import_app()
        text, _ = app._extract_prompt_text(["a", "b", "c"])
        assert text == "a\nb\nc"

    def test_list_with_text_attr(self):
        app = _import_app()
        obj = Mock()
        obj.text = "from obj"
        obj.parts = None  # so hasattr(item, "parts") won't recurse
        text, _ = app._extract_prompt_text([obj])
        assert "from obj" in text

    def test_list_with_parts(self):
        app = _import_app()
        part = Mock()
        part.text = "part_text"
        item = Mock(spec=[])  # no .text attribute
        item.text = None
        item.parts = [part]

        # Need to make hasattr work correctly
        class ItemWithParts:
            text = None
            parts = [part]

        text, _ = app._extract_prompt_text([ItemWithParts()])
        assert "part_text" in text

    def test_non_string_non_list(self):
        app = _import_app()
        text, _ = app._extract_prompt_text(12345)
        assert text == "12345"

    def test_with_system_instruction(self):
        app = _import_app()
        cfg = Mock()
        cfg.system_instruction = "be helpful"
        text, sys = app._extract_prompt_text("hello", config=cfg)
        assert text == "hello"
        assert sys == "be helpful"

    def test_config_without_system_instruction(self):
        app = _import_app()
        cfg = Mock(spec=[])  # no system_instruction attribute
        text, sys = app._extract_prompt_text("hello", config=cfg)
        assert text == "hello"
        assert sys is None

    def test_empty_list(self):
        app = _import_app()
        text, _ = app._extract_prompt_text([])
        assert text == ""

    def test_list_with_fallback_str(self):
        app = _import_app()

        # Object without .text or .parts
        class Opaque:
            def __str__(self):
                return "opaque_value"

        text, _ = app._extract_prompt_text([Opaque()])
        assert "opaque_value" in text


# =====================================================================
# 3. _FakeGenerateContentResponse
# =====================================================================
@pytest.mark.unit
class TestFakeGenerateContentResponse:
    def test_attributes(self):
        app = _import_app()
        resp = app._FakeGenerateContentResponse("hello")
        assert resp.text == "hello"
        assert resp.candidates == []
        assert resp.usage_metadata is None


# =====================================================================
# 4. _TrackedModels
# =====================================================================
@pytest.mark.unit
class TestTrackedModels:
    def _make(self):
        app = _import_app()
        real = MagicMock()
        return app._TrackedModels(real), real, app

    def test_generate_content_normal(self):
        tm, real, app = self._make()
        real.generate_content.return_value = Mock(text="ok", usage_metadata=None)
        with patch.object(app, "_is_interactions_only", return_value=False):
            resp = tm.generate_content(model="gemini-2.5-flash", contents="hi")
        assert resp.text == "ok"

    def test_generate_content_interactions_only_passthrough(self):
        """After refactor, generate_content no longer pre-checks _is_interactions_only;
        it always delegates to the real SDK's generate_content."""
        tm, real, app = self._make()
        real.generate_content.return_value = Mock(
            text="sdk_result", usage_metadata=None
        )
        resp = tm.generate_content(model="gemini-3-flash-preview", contents="hi")
        assert resp.text == "sdk_result"
        real.generate_content.assert_called_once()

    def test_generate_content_error_propagates(self):
        """After refactor, generate_content no longer catches 'Interactions API required'
        errors internally; they propagate to the caller."""
        tm, real, app = self._make()
        real.generate_content.side_effect = Exception("Interactions API required")
        with pytest.raises(Exception, match="Interactions API required"):
            tm.generate_content(model="some-model", contents="hi")

    def test_generate_content_non_interactions_error_raises(self):
        tm, real, app = self._make()
        real.generate_content.side_effect = ValueError("bad input")
        with patch.object(app, "_is_interactions_only", return_value=False):
            with pytest.raises(ValueError, match="bad input"):
                tm.generate_content(model="m", contents="hi")

    def test_generate_content_stream_normal(self):
        tm, real, app = self._make()
        chunk = Mock(text="chunk1", usage_metadata=None)
        real.generate_content_stream.return_value = iter([chunk])
        with patch.object(app, "_is_interactions_only", return_value=False):
            chunks = list(tm.generate_content_stream(model="m", contents="hi"))
        assert len(chunks) == 1
        assert chunks[0].text == "chunk1"

    def test_generate_content_stream_passthrough(self):
        """After refactor, generate_content_stream always delegates to the real SDK."""
        tm, real, app = self._make()
        chunk = Mock(text="stream_chunk", usage_metadata=None)
        real.generate_content_stream.return_value = iter([chunk])
        chunks = list(
            tm.generate_content_stream(model="gemini-3-flash-preview", contents="hi")
        )
        assert len(chunks) == 1
        assert chunks[0].text == "stream_chunk"

    def test_generate_images_records_usage(self):
        tm, real, app = self._make()
        img_resp = Mock()
        img_resp.generated_images = [Mock(), Mock()]
        real.generate_images.return_value = img_resp
        with patch.object(app, "_TOKEN_TRACKER_ENABLED", True), patch.object(
            app, "_record_token_usage"
        ) as mock_rec:
            resp = tm.generate_images(model="imagen-3.0", prompt="cat")
        assert resp == img_resp
        mock_rec.assert_called_once()
        call_kwargs = mock_rec.call_args[1]
        assert call_kwargs["prompt_tokens"] == 2000  # 2 images * 1000

    def test_embed_content_with_usage_metadata(self):
        tm, real, app = self._make()
        usage = Mock()
        usage.prompt_token_count = 42
        embed_resp = Mock(usage_metadata=usage)
        real.embed_content.return_value = embed_resp
        with patch.object(app, "_TOKEN_TRACKER_ENABLED", True), patch.object(
            app, "_record_token_usage"
        ) as mock_rec:
            resp = tm.embed_content(model="text-embedding-004", contents="text")
        assert resp == embed_resp
        mock_rec.assert_called_once()
        assert mock_rec.call_args[1]["prompt_tokens"] == 42

    def test_embed_content_no_usage_estimates(self):
        tm, real, app = self._make()
        embed_resp = Mock(usage_metadata=None)
        real.embed_content.return_value = embed_resp
        with patch.object(app, "_TOKEN_TRACKER_ENABLED", True), patch.object(
            app, "_record_token_usage"
        ) as mock_rec:
            resp = tm.embed_content(
                model="text-embedding-004", contents="hello world test"
            )
        mock_rec.assert_called_once()
        # Estimation: len("hello world test") // 4 = 4
        assert mock_rec.call_args[1]["prompt_tokens"] >= 1

    def test_getattr_passthrough(self):
        tm, real, app = self._make()
        real.some_method.return_value = "val"
        assert tm.some_method() == "val"

    def test_generate_content_with_positional_model(self):
        """Test fallback when model=None and model is first positional arg."""
        tm, real, app = self._make()
        real.generate_content.return_value = Mock(text="pos", usage_metadata=None)
        with patch.object(app, "_is_interactions_only", return_value=False):
            resp = tm.generate_content(None, "positional-model", contents="hi")
        # model should be extracted from args
        assert resp.text == "pos"


# =====================================================================
# 5. _is_interactions_only
# =====================================================================
@pytest.mark.unit
class TestInteractionsChecks:
    def test_is_interactions_only_known(self):
        app = _import_app()
        # Only deep-research model is in the static default set
        assert app._is_interactions_only("deep-research-pro-preview-12-2025") is True

    def test_is_interactions_only_unknown(self):
        app = _import_app()
        assert app._is_interactions_only("gemini-2.5-flash") is False

    def test_is_interactions_only_deep_research_prefix(self):
        app = _import_app()
        assert app._is_interactions_only("deep-research-pro-preview-99") is True

    def test_is_interactions_only_deep_research_exact(self):
        app = _import_app()
        assert app._is_interactions_only("deep-research-pro-preview-12-2025") is True

    def test_is_interactions_only_gemini3_flash_not_static(self):
        """gemini-3-flash-preview is NOT in the static default set; it may be
        added dynamically by ModelManager at runtime."""
        app = _import_app()
        assert app._is_interactions_only("gemini-3-flash-preview") is False

    def test_is_interactions_only_gemini3_pro_not_static(self):
        """gemini-3-pro-preview is NOT in the static default set."""
        app = _import_app()
        assert app._is_interactions_only("gemini-3-pro-preview") is False

    def test_is_interactions_only_regular_model_false(self):
        app = _import_app()
        assert app._is_interactions_only("gemini-2.0-flash") is False

    def test_is_interactions_only_none(self):
        app = _import_app()
        assert app._is_interactions_only(None) is False

    def test_is_interactions_only_empty_string(self):
        app = _import_app()
        assert app._is_interactions_only("") is False


# =====================================================================
# 6. run_with_timeout
# =====================================================================
@pytest.mark.unit
class TestRunWithTimeout:
    def test_success(self):
        app = _import_app()
        result, error, timed_out = app.run_with_timeout(lambda: 42, 5)
        assert result == 42
        assert error is None
        assert timed_out is False

    def test_exception_captured(self):
        app = _import_app()

        def boom():
            raise ValueError("fail")

        result, error, timed_out = app.run_with_timeout(boom, 5)
        assert result is None
        assert isinstance(error, ValueError)
        assert timed_out is False

    def test_timeout(self):
        app = _import_app()

        def slow():
            time.sleep(10)

        result, error, timed_out = app.run_with_timeout(slow, 0.1)
        assert timed_out is True
        assert isinstance(error, TimeoutError)


# =====================================================================
# 7. run_with_heartbeat
# =====================================================================
@pytest.mark.unit
class TestRunWithHeartbeat:
    def test_success(self):
        app = _import_app()
        result, error, timed_out = app.run_with_heartbeat(
            lambda: "ok",
            start_time=time.time(),
            heartbeat_callback=lambda e: None,
            heartbeat_interval=1,
            timeout_seconds=5,
        )
        assert result == "ok"
        assert error is None
        assert timed_out is False

    def test_error(self):
        app = _import_app()

        def boom():
            raise RuntimeError("fail")

        result, error, timed_out = app.run_with_heartbeat(
            boom,
            start_time=time.time(),
            heartbeat_callback=lambda e: None,
            timeout_seconds=5,
        )
        assert result is None
        assert isinstance(error, RuntimeError)
        assert timed_out is False

    def test_timeout(self):
        app = _import_app()

        def slow():
            time.sleep(10)

        result, error, timed_out = app.run_with_heartbeat(
            slow,
            start_time=time.time() - 100,  # Already elapsed
            heartbeat_callback=lambda e: None,
            timeout_seconds=1,
        )
        assert timed_out is True

    def test_heartbeat_called(self):
        app = _import_app()
        beats = []

        def slow():
            time.sleep(3)
            return "done"

        result, error, timed_out = app.run_with_heartbeat(
            slow,
            start_time=time.time(),
            heartbeat_callback=lambda e: beats.append(e),
            heartbeat_interval=1,
            timeout_seconds=10,
        )
        assert result == "done"
        assert len(beats) >= 1  # At least one heartbeat


# =====================================================================
# 8. stream_with_keepalive
# =====================================================================
@pytest.mark.unit
class TestStreamWithKeepalive:
    def test_normal_stream(self):
        app = _import_app()
        chunks = [Mock(text="a"), Mock(text="b")]
        results = list(app.stream_with_keepalive(iter(chunks), time.time()))
        types_ = [r[0] for r in results]
        assert "chunk" in types_

    def test_timeout_waiting_for_first_token(self):
        app = _import_app()

        def slow_stream():
            time.sleep(10)
            yield Mock(text="late")

        results = list(
            app.stream_with_keepalive(
                slow_stream(),
                start_time=time.time() - 100,  # Already past timeout
                max_wait_first_token=1,
            )
        )
        types_ = [r[0] for r in results]
        assert "timeout" in types_

    def test_heartbeat_emitted(self):
        app = _import_app()
        import queue

        # Simulate a slow stream with delayed chunks
        def delayed_stream():
            time.sleep(2)
            yield Mock(text="delayed")

        results = list(
            app.stream_with_keepalive(
                delayed_stream(),
                start_time=time.time(),
                keepalive_interval=1,
                max_wait_first_token=10,
            )
        )
        types_ = [r[0] for r in results]
        assert "heartbeat" in types_ or "chunk" in types_

    def test_error_in_stream(self):
        app = _import_app()

        def error_stream():
            raise RuntimeError("stream error")
            yield  # pragma: no cover

        with pytest.raises(RuntimeError, match="stream error"):
            list(app.stream_with_keepalive(error_stream(), time.time()))


# =====================================================================
# 9–10. _poll_interaction and _extract_interaction_text_global were
# removed from module level in PR #45; tests removed accordingly.
# =====================================================================


# =====================================================================
# 11. _build_filegen_time_context / _parse_time_info_for_filegen
# =====================================================================
@pytest.mark.unit
class TestFilegenTimeContext:
    def test_no_month_detected(self):
        app = _import_app()
        ctx_text, parsed = app._build_filegen_time_context("做一个文档")
        assert "未检测到明确月份" in ctx_text
        assert parsed["rule_hit"] is False

    def test_month_only(self):
        app = _import_app()
        ctx_text, parsed = app._build_filegen_time_context("1月新番列表")
        assert parsed["resolved_month"] == 1
        assert parsed["rule_hit"] is True
        assert "按当前年份解析" in ctx_text

    def test_year_and_month(self):
        app = _import_app()
        ctx_text, parsed = app._build_filegen_time_context("2024年3月报告")
        assert parsed["resolved_year"] == 2024
        assert parsed["resolved_month"] == 3
        assert parsed["rule_hit"] is False

    def test_parse_time_info_no_match(self):
        app = _import_app()
        info = app._parse_time_info_for_filegen("no time info here")
        assert info["rule_hit"] is False
        assert info["month"] is None

    def test_parse_time_info_month_12(self):
        app = _import_app()
        info = app._parse_time_info_for_filegen("12月动漫推荐")
        assert info["month"] == 12
        assert info["rule_hit"] is True


# =====================================================================
# 12. _error_response (Flask context required)
# =====================================================================
@pytest.mark.unit
class TestErrorResponse:
    def test_basic_error(self):
        app_mod = _import_app()
        flask_app = app_mod.app
        with flask_app.test_request_context("/test"):
            flask_app.preprocess_request()  # triggers _assign_request_id
            resp, status = app_mod._error_response("bad request", 400)
            data = resp.get_json()
        assert status == 400
        assert data["error"] == "bad request"
        assert "request_id" in data

    def test_error_with_details(self):
        app_mod = _import_app()
        flask_app = app_mod.app
        with flask_app.test_request_context("/test"):
            flask_app.preprocess_request()
            resp, status = app_mod._error_response(
                "fail", 422, details={"field": "name"}
            )
            data = resp.get_json()
        assert data["details"]["field"] == "name"
        assert status == 422

    def test_error_without_request_id(self):
        app_mod = _import_app()
        flask_app = app_mod.app
        with flask_app.test_request_context("/test"):
            # Don't call preprocess_request, so g.request_id is not set
            resp, status = app_mod._error_response("oops", 500)
            data = resp.get_json()
        assert data["error"] == "oops"
        assert "request_id" not in data


# =====================================================================
# 13. _assign_request_id
# =====================================================================
@pytest.mark.unit
class TestAssignRequestId:
    def test_generates_uuid(self):
        app_mod = _import_app()
        flask_app = app_mod.app
        with flask_app.test_request_context("/"):
            flask_app.preprocess_request()
            from flask import g

            assert hasattr(g, "request_id")
            assert len(g.request_id) > 0

    def test_reads_from_header(self):
        app_mod = _import_app()
        flask_app = app_mod.app
        with flask_app.test_request_context(
            "/", headers={"X-Request-ID": "custom-123"}
        ):
            flask_app.preprocess_request()
            from flask import g

            assert g.request_id == "custom-123"


# =====================================================================
# 14. TaskOrchestrator._merge_results
# =====================================================================
@pytest.mark.unit
class TestTaskOrchestratorMergeResults:
    def test_merge_with_completed_subtasks(self):
        app = _import_app()
        subtasks = [
            {
                "task_type": "WEB_SEARCH",
                "status": "completed",
                "description": "search",
                "result": {"output": "found"},
                "error": None,
            },
            {
                "task_type": "FILE_GEN",
                "status": "completed",
                "description": "gen",
                "result": {"output": "generated"},
                "error": None,
            },
        ]
        merged = app.TaskOrchestrator._merge_results(subtasks, {})
        assert len(merged["steps"]) == 2
        assert merged["final_output"] == "generated"

    def test_merge_with_failed_subtask(self):
        app = _import_app()
        subtasks = [
            {
                "task_type": "WEB_SEARCH",
                "status": "failed",
                "description": "search",
                "result": None,
                "error": "timeout",
            },
        ]
        merged = app.TaskOrchestrator._merge_results(subtasks, {})
        assert merged["steps"][0]["error"] == "timeout"
        assert merged["final_output"] == ""

    def test_merge_empty(self):
        app = _import_app()
        merged = app.TaskOrchestrator._merge_results([], {})
        assert merged["final_output"] == ""
        assert len(merged["steps"]) == 0


# =====================================================================
# 15. TaskOrchestrator._validate_quality
# =====================================================================
@pytest.mark.unit
class TestTaskOrchestratorValidateQuality:
    def test_no_output(self):
        app = _import_app()
        combined = {"steps": [], "final_output": ""}
        score = asyncio.get_event_loop().run_until_complete(
            app.TaskOrchestrator._validate_quality("test", combined, {})
        )
        assert 0 <= score <= 100

    @pytest.mark.skipif(not HAS_GENAI, reason="google.genai not properly installed")
    def test_with_completed_steps(self):
        app = _import_app()
        combined = {
            "steps": [{"status": "completed"}, {"status": "completed"}],
            "final_output": "some output",
        }
        mock_resp = Mock()
        mock_resp.text = "25"
        with patch.object(app, "client") as mock_client:
            mock_client.models.generate_content.return_value = mock_resp
            score = asyncio.get_event_loop().run_until_complete(
                app.TaskOrchestrator._validate_quality("test", combined, {})
            )
        assert score >= 40

    @pytest.mark.skipif(not HAS_GENAI, reason="google.genai not properly installed")
    def test_semantic_scoring_fails_gracefully(self):
        app = _import_app()
        combined = {
            "steps": [{"status": "completed"}],
            "final_output": "output",
        }
        with patch.object(app, "client") as mock_client:
            mock_client.models.generate_content.side_effect = Exception("API down")
            score = asyncio.get_event_loop().run_until_complete(
                app.TaskOrchestrator._validate_quality("test", combined, {})
            )
        assert 0 <= score <= 100


# =====================================================================
# 16. TaskOrchestrator.execute_compound_task
# =====================================================================
@pytest.mark.unit
class TestExecuteCompoundTask:
    @pytest.mark.skipif(not HAS_GENAI, reason="google.genai not properly installed")
    def test_unknown_task_type(self):
        app = _import_app()
        subtasks = [
            {"task_type": "UNKNOWN", "description": "mystery", "input": "x"},
        ]
        mock_resp = Mock()
        mock_resp.text = "20"
        with patch.object(app, "client") as mc:
            mc.models.generate_content.return_value = mock_resp
            result = asyncio.get_event_loop().run_until_complete(
                app.TaskOrchestrator.execute_compound_task("test", subtasks)
            )
        assert result["success"] is False or any(
            "未知" in e for e in result.get("errors", [])
        )

    @pytest.mark.skipif(not HAS_GENAI, reason="google.genai not properly installed")
    def test_subtask_exception(self):
        app = _import_app()
        subtasks = [
            {"task_type": "WEB_SEARCH", "description": "search", "input": "x"},
        ]
        mock_resp = Mock()
        mock_resp.text = "10"
        with patch.object(
            app.TaskOrchestrator, "_execute_web_search", side_effect=Exception("boom")
        ), patch.object(app, "client") as mc:
            mc.models.generate_content.return_value = mock_resp
            result = asyncio.get_event_loop().run_until_complete(
                app.TaskOrchestrator.execute_compound_task("test", subtasks)
            )
        assert len(result["errors"]) > 0


# =====================================================================
# 17. LocalDispatcher
# =====================================================================
@pytest.mark.unit
class TestLocalDispatcher:
    def test_is_ollama_running_cloud(self):
        app = _import_app()
        with patch.dict(os.environ, {"KOTO_DEPLOY_MODE": "cloud"}):
            assert app.LocalDispatcher.is_ollama_running() is False

    def test_is_ollama_running_connection_error(self):
        app = _import_app()
        with patch.dict(os.environ, {"KOTO_DEPLOY_MODE": "local"}), patch.object(
            app, "requests"
        ) as mock_req:
            mock_req.get.side_effect = Exception("no conn")
            assert app.LocalDispatcher.is_ollama_running() is False

    def test_analyze_delegates(self):
        app = _import_app()
        with patch.object(
            app.SmartDispatcher, "analyze", return_value=("CHAT", "auto", {})
        ):
            result = app.LocalDispatcher.analyze("hello")
        assert result == ("CHAT", "auto", {})


# =====================================================================
# 18. Utils.auto_save_files
# =====================================================================
@pytest.mark.unit
class TestAutoSaveFiles:
    def test_begin_end_file_pattern(self, tmp_path):
        app = _import_app()
        with patch.object(app, "WORKSPACE_DIR", str(tmp_path)):
            text = "---BEGIN_FILE: test_output.py---\nprint('hello')\n---END_FILE---"
            saved = app.Utils.auto_save_files(text)
        assert "test_output.py" in saved

    def test_no_match(self, tmp_path):
        app = _import_app()
        with patch.object(app, "WORKSPACE_DIR", str(tmp_path)):
            saved = app.Utils.auto_save_files("just plain text, no files")
        assert saved == []

    def test_python_code_block_with_filename(self, tmp_path):
        app = _import_app()
        with patch.object(app, "WORKSPACE_DIR", str(tmp_path)):
            text = "```python\n# filename: my_script.py\nimport os\nprint(os.getcwd())\nprint('hello world this is a test')\n```"
            saved = app.Utils.auto_save_files(text)
        assert "my_script.py" in saved

    def test_file_without_extension(self, tmp_path):
        app = _import_app()
        with patch.object(app, "WORKSPACE_DIR", str(tmp_path)):
            text = "---BEGIN_FILE: noext---\ncontent here\n---END_FILE---"
            saved = app.Utils.auto_save_files(text)
        assert any("noext" in f for f in saved)

    def test_get_save_dir_code_ext(self, tmp_path):
        app = _import_app()
        code_dir = str(tmp_path / "code")
        os.makedirs(code_dir, exist_ok=True)
        with patch.object(app, "WORKSPACE_DIR", str(tmp_path)):
            text = "---BEGIN_FILE: script.py---\nprint(1)\n---END_FILE---"
            saved = app.Utils.auto_save_files(text)
        assert "script.py" in saved

    def test_get_save_dir_non_code_ext(self, tmp_path):
        app = _import_app()
        with patch.object(app, "WORKSPACE_DIR", str(tmp_path)):
            text = "---BEGIN_FILE: report.pdf---\nbinary content\n---END_FILE---"
            saved = app.Utils.auto_save_files(text)
        assert "report.pdf" in saved


# =====================================================================
# 19. Utils helpers
# =====================================================================
@pytest.mark.unit
class TestUtilsMethods:
    def test_sanitize_string(self):
        app = _import_app()
        assert app.Utils.sanitize_string("hello") == "hello"
        assert app.Utils.sanitize_string(123) == 123

    def test_is_failure_output_empty(self):
        app = _import_app()
        assert app.Utils.is_failure_output("") is True
        assert app.Utils.is_failure_output(None) is True

    def test_is_failure_output_error_prefix(self):
        app = _import_app()
        assert app.Utils.is_failure_output("❌ something failed") is True

    def test_is_failure_output_chinese_error(self):
        app = _import_app()
        assert app.Utils.is_failure_output("操作失败了") is True

    def test_is_failure_output_no_internet(self):
        app = _import_app()
        assert (
            app.Utils.is_failure_output("I don't have access to the internet") is True
        )

    def test_is_failure_output_normal(self):
        app = _import_app()
        assert app.Utils.is_failure_output("Here is your answer") is False

    def test_build_fix_prompt_file_gen(self):
        app = _import_app()
        prompt = app.Utils.build_fix_prompt("FILE_GEN", "make doc", "bad output")
        assert "BEGIN_FILE" in prompt

    def test_build_fix_prompt_coder(self):
        app = _import_app()
        prompt = app.Utils.build_fix_prompt("CODER", "write code")
        assert "代码" in prompt or "code" in prompt.lower()

    def test_build_fix_prompt_research(self):
        app = _import_app()
        prompt = app.Utils.build_fix_prompt("RESEARCH", "research topic")
        assert "报告" in prompt or "report" in prompt.lower()

    def test_build_fix_prompt_web_search(self):
        app = _import_app()
        prompt = app.Utils.build_fix_prompt("WEB_SEARCH", "search query")
        assert "实时" in prompt or "信息" in prompt

    def test_build_fix_prompt_generic(self):
        app = _import_app()
        prompt = app.Utils.build_fix_prompt("OTHER", "input")
        assert "用户需求" in prompt


# =====================================================================
# 20. get_memory_manager
# =====================================================================
@pytest.mark.unit
class TestGetMemoryManager:
    def test_returns_instance(self):
        app = _import_app()
        old = app._memory_manager
        try:
            app._memory_manager = None
            mock_mgr = MagicMock()
            with patch(
                "web.app.EnhancedMemoryManager", create=True, return_value=mock_mgr
            ) as mock_cls, patch.dict(
                "sys.modules",
                {"enhanced_memory_manager": MagicMock(EnhancedMemoryManager=mock_cls)},
            ):
                mgr = app.get_memory_manager()
                assert mgr is not None
                # Second call returns same instance
                assert app.get_memory_manager() is mgr
        finally:
            app._memory_manager = old

    def test_fallback_to_basic(self):
        app = _import_app()
        old = app._memory_manager
        try:
            app._memory_manager = None
            mock_basic = MagicMock()
            with patch.dict(
                "sys.modules",
                {
                    "enhanced_memory_manager": None,
                    "web.enhanced_memory_manager": None,
                },
            ):
                # Will try to import MemoryManager as fallback
                mgr = app.get_memory_manager()
                assert mgr is not None
        finally:
            app._memory_manager = old


# =====================================================================
# 21. get_knowledge_base
# =====================================================================
@pytest.mark.unit
class TestGetKnowledgeBase:
    def test_returns_instance(self):
        app = _import_app()
        app._kb = None
        kb = app.get_knowledge_base()
        assert kb is not None
        assert app.get_knowledge_base() is kb


# =====================================================================
# 22. KotoBrain.chat - SYSTEM route
# =====================================================================
@pytest.mark.unit
class TestKotoBrainChat:
    def _make_brain(self):
        app = _import_app()
        return app.KotoBrain(), app

    def test_system_route(self):
        brain, app = self._make_brain()
        with patch.object(app, "SmartDispatcher") as mock_sd:
            mock_sd.analyze.return_value = ("SYSTEM", "auto", {})
            mock_sd.get_model_for_task.return_value = "gemini-2.5-flash"
            with patch.object(app, "LocalExecutor") as mock_exec:
                mock_exec.execute.return_value = {
                    "message": "opened notepad",
                    "details": "pid=123",
                }
                result = brain.chat([], "open notepad")
        assert result["task"] == "SYSTEM"
        assert "notepad" in result["response"]

    @pytest.mark.skipif(not HAS_GENAI, reason="google.genai not properly installed")
    def test_manual_model_selection(self):
        brain, app = self._make_brain()
        mock_resp = Mock()
        mock_resp.text = "manual response"
        mock_resp.usage_metadata = None
        with patch.object(app, "client") as mock_client, patch.object(
            app, "_start_memory_extraction"
        ):
            mock_client.models.generate_content.return_value = mock_resp
            result = brain.chat(
                [],
                "hello",
                model="gemini-2.5-flash",
                auto_model=False,
                task_type="CHAT",
            )
        assert result["model"] == "gemini-2.5-flash"

    @pytest.mark.skipif(not HAS_GENAI, reason="google.genai not properly installed")
    def test_image_file_edit_route(self):
        brain, app = self._make_brain()
        file_data = {"mime_type": "image/png", "data": b"fake_img"}
        with patch.object(app, "SmartDispatcher") as mock_sd, patch.object(
            app, "client"
        ) as mock_client, patch.object(
            app, "settings_manager", Mock(images_dir="/tmp/images")
        ), patch.object(
            app, "_start_memory_extraction"
        ):
            mock_sd.get_model_for_task.return_value = "gemini-2.5-flash"
            mock_resp = Mock()
            mock_resp.text = "no code generated"
            mock_resp.usage_metadata = None
            mock_client.models.generate_content.return_value = mock_resp
            result = brain.chat([], "修改背景为蓝色", file_data=file_data)
        assert result["task"] == "PAINTER"

    @pytest.mark.skipif(not HAS_GENAI, reason="google.genai not properly installed")
    def test_image_file_analysis_route(self):
        brain, app = self._make_brain()
        file_data = {"mime_type": "image/jpeg", "data": b"fake_img"}
        mock_resp = Mock()
        mock_resp.text = "This is a cat photo"
        mock_resp.usage_metadata = None
        with patch.object(app, "SmartDispatcher") as mock_sd, patch.object(
            app, "client"
        ) as mock_client, patch.object(app, "_start_memory_extraction"):
            mock_sd.get_model_for_task.return_value = "gemini-2.5-flash"
            mock_client.models.generate_content.return_value = mock_resp
            result = brain.chat([], "describe this image", file_data=file_data)
        assert result["task"] == "VISION"

    @pytest.mark.skipif(not HAS_GENAI, reason="google.genai not properly installed")
    def test_binary_doc_route(self):
        brain, app = self._make_brain()
        file_data = {"mime_type": "application/pdf", "data": b"fake_pdf"}
        mock_resp = Mock()
        mock_resp.text = "PDF contents summary"
        mock_resp.usage_metadata = None
        with patch.object(app, "client") as mock_client, patch.object(
            app, "_start_memory_extraction"
        ):
            mock_client.models.generate_content.return_value = mock_resp
            result = brain.chat([], "summarize this PDF", file_data=file_data)
        assert result["task"] == "CHAT"
        assert result["route_method"] == "📄 Binary-Doc-Read"


# =====================================================================
# 23. _process_code_response (nested inside KotoBrain.chat)
# =====================================================================
@pytest.mark.unit
class TestProcessCodeResponse:
    """Test the code extraction logic used in _process_code_response."""

    def test_extract_begin_file_pattern(self):
        import re

        text = "---BEGIN_FILE: edit.py---\nimport cv2\nprint('hi')\n---END_FILE---"
        pattern = (
            r"---BEGIN_FILE:\s*([a-zA-Z0-9_.-]+)\s*---\s*(.*?)---\s*END_FILE\s*---"
        )
        matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
        assert len(matches) == 1
        assert matches[0][0] == "edit.py"

    def test_extract_python_code_block(self):
        import re

        text = "```python\nimport os\nprint('hello')\n```"
        pattern = r"```python\s*(.*?)```"
        matches = re.findall(pattern, text, re.DOTALL)
        assert len(matches) == 1
        assert "import os" in matches[0]

    def test_no_code_found(self):
        import re

        text = "No code here, just a plain text response."
        patterns = [
            r"---BEGIN_FILE:\s*([a-zA-Z0-9_.-]+)\s*---\s*(.*?)---\s*END_FILE\s*---",
            r"```python\s*(.*?)```",
            r"```\s*(.*?)```",
        ]
        found = False
        for pattern in patterns:
            if re.findall(pattern, text, re.DOTALL | re.IGNORECASE):
                found = True
                break
        assert found is False


# =====================================================================
# 24. _clean_filegen_text logic (extracted for testing)
# =====================================================================
@pytest.mark.unit
class TestCleanFilegenText:
    """Test the markdown cleaning logic used in _clean_filegen_text."""

    def _clean(self, text):
        import re

        if not text:
            return text
        cleaned = text
        cleaned = re.sub(r"```[a-zA-Z0-9_-]*\n", "", cleaned)
        cleaned = cleaned.replace("```", "")
        cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", cleaned)
        cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)
        cleaned = re.sub(r"\*(.+?)\*", r"\1", cleaned)
        cleaned = re.sub(r"_(.+?)_", r"\1", cleaned)
        cleaned = cleaned.replace("`", "")
        cleaned = re.sub(r"^\s{0,3}#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"^\s*>\s?", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"^\s*[-_*]{3,}\s*$", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"^\s*[-*+]\s+", "  ", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"^\s*\d+\.\s+", "  ", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = cleaned.replace("**", "").replace("__", "")
        return cleaned

    def test_removes_code_blocks(self):
        assert "```" not in self._clean("```python\ncode\n```")

    def test_removes_bold(self):
        assert self._clean("**bold text**") == "bold text"

    def test_removes_links(self):
        assert self._clean("[click](http://example.com)") == "click"

    def test_removes_headings(self):
        result = self._clean("# Heading\nText")
        assert "#" not in result

    def test_removes_blockquote(self):
        result = self._clean("> quoted text")
        assert ">" not in result

    def test_removes_hr(self):
        result = self._clean("---\n\ncontent")
        assert "---" not in result

    def test_empty_input(self):
        assert self._clean("") == ""
        assert self._clean(None) is None

    def test_normalizes_blank_lines(self):
        result = self._clean("a\n\n\n\nb")
        assert "\n\n\n" not in result


# =====================================================================
# 25. _extract_markdown_table logic
# =====================================================================
@pytest.mark.unit
class TestExtractMarkdownTable:
    def _extract(self, md_text):
        import re

        lines = [line.strip() for line in md_text.splitlines() if "|" in line]
        for i in range(len(lines) - 1):
            header_line = lines[i]
            sep_line = lines[i + 1]
            if re.match(r"^\s*\|?\s*[-:|\s]+\|\s*$", sep_line):
                headers = [c.strip() for c in header_line.strip("|").split("|")]
                rows = []
                j = i + 2
                while j < len(lines) and "|" in lines[j]:
                    row = [c.strip() for c in lines[j].strip("|").split("|")]
                    if len(row) < len(headers):
                        row += [""] * (len(headers) - len(row))
                    rows.append(row[: len(headers)])
                    j += 1
                return [headers] + rows
        return None

    def test_basic_table(self):
        md = "| Name | Age |\n|---|---|\n| Alice | 30 |\n| Bob | 25 |"
        result = self._extract(md)
        assert result is not None
        assert "Name" in result[0][0]
        assert len(result) == 3  # header + 2 rows

    def test_no_table(self):
        assert self._extract("Just text\nNo table") is None

    def test_short_row_padded(self):
        md = "| A | B | C |\n|---|---|---|\n| 1 |"
        result = self._extract(md)
        assert result is not None
        assert len(result[1]) == 3  # padded to match headers


# =====================================================================
# 26. _ClientProxy
# =====================================================================
@pytest.mark.unit
class TestClientProxy:
    def test_models_returns_tracked(self):
        app = _import_app()
        proxy = app._ClientProxy()
        with patch.object(app, "get_client") as mock_gc:
            mock_gc.return_value = Mock()
            models = proxy.models
        assert isinstance(models, app._TrackedModels)

    def test_non_models_passthrough(self):
        app = _import_app()
        proxy = app._ClientProxy()
        mock_client = Mock()
        mock_client.other_attr = "value"
        with patch.object(app, "get_client", return_value=mock_client):
            assert proxy.other_attr == "value"


# =====================================================================
# 27. KotoBrain.IMAGE_EDIT_KEYWORDS
# =====================================================================
@pytest.mark.unit
class TestKotoBrainKeywords:
    def test_keywords_exist(self):
        app = _import_app()
        brain = app.KotoBrain()
        assert "修改" in brain.IMAGE_EDIT_KEYWORDS
        assert "edit" in brain.IMAGE_EDIT_KEYWORDS
        assert len(brain.IMAGE_EDIT_KEYWORDS) > 10


# =====================================================================
# 28. SessionManager
# =====================================================================
@pytest.mark.unit
class TestSessionManager:
    def test_delete_nonexistent(self, tmp_path):
        app = _import_app()
        with patch.object(app, "CHAT_DIR", str(tmp_path)):
            mgr = app.SessionManager()
            assert mgr.delete("nonexistent.json") is False

    def test_load_missing_file(self, tmp_path):
        app = _import_app()
        with patch.object(app, "CHAT_DIR", str(tmp_path)):
            mgr = app.SessionManager()
            assert mgr.load("missing.json") == []

    def test_load_corrupt_json(self, tmp_path):
        app = _import_app()
        (tmp_path / "bad.json").write_text("NOT JSON", encoding="utf-8")
        with patch.object(app, "CHAT_DIR", str(tmp_path)):
            mgr = app.SessionManager()
            assert mgr.load("bad.json") == []


# =====================================================================
# 29. Flask error handlers
# =====================================================================
@pytest.mark.unit
class TestFlaskErrorHandlers:
    def test_404_handler(self):
        app_mod = _import_app()
        client = app_mod.app.test_client()
        resp = client.get("/this-does-not-exist-at-all-404")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "Not found"

    def test_405_handler(self):
        app_mod = _import_app()
        # POST to a GET-only route (like static)
        client = app_mod.app.test_client()
        resp = client.delete("/")  # DELETE on root
        # May return 404 or 405 depending on route config
        assert resp.status_code in (404, 405)


# =====================================================================
# 30. _LazyModule
# =====================================================================
@pytest.mark.unit
class TestLazyModule:
    def test_lazy_module_defers_import(self):
        app = _import_app()
        calls = []

        def import_fn():
            calls.append(1)
            return Mock(attr="value")

        lm = app._LazyModule(import_fn)
        assert len(calls) == 0  # Not yet imported
        _ = lm.attr
        assert len(calls) == 1  # Now imported

    def test_lazy_module_repr_before_load(self):
        app = _import_app()
        lm = app._LazyModule(lambda: Mock())
        assert "not loaded" in repr(lm)


# =====================================================================
# 31. TASK_PROMPTS
# =====================================================================
@pytest.mark.unit
class TestTaskPrompts:
    def test_all_task_types_have_prompts(self):
        app = _import_app()
        expected = {"CHAT", "CODER", "FILE_GEN", "PAINTER", "RESEARCH", "SYSTEM"}
        assert expected.issubset(set(app.TASK_PROMPTS.keys()))

    def test_prompts_are_non_empty(self):
        app = _import_app()
        for key, val in app.TASK_PROMPTS.items():
            assert len(val.strip()) > 0, f"TASK_PROMPTS[{key}] is empty"


# =====================================================================
# 32. _attach_request_id
# =====================================================================
@pytest.mark.unit
class TestAttachRequestId:
    def test_response_has_header(self):
        app_mod = _import_app()
        flask_app = app_mod.app
        client = flask_app.test_client()
        # Any request should get X-Request-ID in response
        resp = client.get("/this-does-not-exist-at-all-test-rid")
        assert "X-Request-ID" in resp.headers
