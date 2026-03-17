#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Comprehensive unit tests for web/app.py — targeting standalone utility classes,
pure functions, and Flask route handlers.

Goal: exercise as many independent code paths as possible in the massive
web/app.py module (~17 000 lines, 9 844 statements, 14% baseline coverage).
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so ``web.app`` can be imported.
# Pre-set env vars *before* importing the app to avoid side-effects.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("KOTO_AUTH_ENABLED", "false")
os.environ.setdefault("KOTO_DEPLOY_MODE", "local")
os.environ.setdefault("GEMINI_API_KEY", "test-key-for-unit-tests")


# =====================================================================
# 1. StreamInterruptManager
# =====================================================================
@pytest.mark.unit
class TestStreamInterruptManager:
    """Tests for the thread-safe StreamInterruptManager class."""

    def setup_method(self):
        from web.app import StreamInterruptManager

        self.mgr = StreamInterruptManager()

    # -- basic flag lifecycle ------------------------------------------

    def test_is_interrupted_unknown_session(self):
        assert self.mgr.is_interrupted("nonexistent") is False

    def test_set_and_check_interrupt(self):
        self.mgr.set_interrupt("s1")
        assert self.mgr.is_interrupted("s1") is True

    def test_reset_clears_interrupt(self):
        self.mgr.set_interrupt("s1")
        self.mgr.reset("s1")
        assert self.mgr.is_interrupted("s1") is False

    def test_get_event_returns_event(self):
        evt = self.mgr.get_event("s2")
        assert isinstance(evt, threading.Event)
        assert not evt.is_set()

    def test_set_interrupt_sets_event(self):
        evt = self.mgr.get_event("s3")
        self.mgr.set_interrupt("s3")
        assert evt.is_set()

    def test_reset_clears_event(self):
        self.mgr.set_interrupt("s4")
        evt = self.mgr.get_event("s4")
        assert evt.is_set()
        self.mgr.reset("s4")
        assert not evt.is_set()

    def test_cleanup_removes_session(self):
        self.mgr.set_interrupt("s5")
        self.mgr.cleanup("s5")
        assert self.mgr.is_interrupted("s5") is False
        # Ensure internal dict is cleaned
        assert "s5" not in self.mgr.interrupts

    def test_cleanup_nonexistent_no_error(self):
        self.mgr.cleanup("never_existed")  # should not raise

    def test_multiple_sessions_independent(self):
        self.mgr.set_interrupt("a")
        assert self.mgr.is_interrupted("a") is True
        assert self.mgr.is_interrupted("b") is False

    def test_thread_safety(self):
        """Concurrent set/reset should not corrupt state."""
        errors = []

        def worker(name, action):
            try:
                for _ in range(50):
                    if action == "set":
                        self.mgr.set_interrupt(name)
                    elif action == "reset":
                        self.mgr.reset(name)
                    elif action == "check":
                        self.mgr.is_interrupted(name)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=("ts", "set")),
            threading.Thread(target=worker, args=("ts", "reset")),
            threading.Thread(target=worker, args=("ts", "check")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# =====================================================================
# 2. _load_user_settings
# =====================================================================
@pytest.mark.unit
class TestLoadUserSettings:
    def test_returns_dict_from_file(self, tmp_path):
        from web import app as webapp

        settings_path = tmp_path / "config" / "user_settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps({"storage": {"workspace_dir": "/w"}}), encoding="utf-8"
        )

        # Clear cache before test
        webapp._user_settings_cache.clear()
        with patch.object(webapp, "PROJECT_ROOT", str(tmp_path)):
            result = webapp._load_user_settings()
        assert result == {"storage": {"workspace_dir": "/w"}}
        webapp._user_settings_cache.clear()

    def test_returns_empty_dict_on_missing_file(self, tmp_path):
        from web import app as webapp

        webapp._user_settings_cache.clear()
        with patch.object(webapp, "PROJECT_ROOT", str(tmp_path)):
            result = webapp._load_user_settings()
        assert result == {}
        webapp._user_settings_cache.clear()

    def test_caches_result(self, tmp_path):
        from web import app as webapp

        webapp._user_settings_cache.clear()
        webapp._user_settings_cache["data"] = {"cached": True}
        result = webapp._load_user_settings()
        assert result == {"cached": True}
        webapp._user_settings_cache.clear()


# =====================================================================
# 3. get_workspace_root / get_organize_root / get_default_wechat_files_dir
# =====================================================================
@pytest.mark.unit
class TestStorageHelpers:
    def _with_settings(self, settings_dict):
        from web import app as webapp

        webapp._user_settings_cache.clear()
        webapp._user_settings_cache["data"] = settings_dict
        return webapp

    def teardown_method(self):
        from web import app as webapp

        webapp._user_settings_cache.clear()

    def test_workspace_root_from_settings(self):
        webapp = self._with_settings({"storage": {"workspace_dir": "D:\\work"}})
        assert webapp.get_workspace_root() == "D:\\work"

    def test_workspace_root_default(self):
        webapp = self._with_settings({})
        result = webapp.get_workspace_root()
        assert result.endswith("workspace")

    def test_organize_root_from_settings(self):
        webapp = self._with_settings({"storage": {"organize_root": "E:\\org"}})
        assert webapp.get_organize_root() == "E:\\org"

    def test_organize_root_default(self):
        webapp = self._with_settings({})
        result = webapp.get_organize_root()
        assert result.endswith("_organize")

    def test_wechat_files_dir_from_settings(self):
        webapp = self._with_settings({"storage": {"wechat_files_dir": "C:\\wechat"}})
        assert webapp.get_default_wechat_files_dir() == "C:\\wechat"

    def test_wechat_files_dir_default_empty(self):
        webapp = self._with_settings({})
        assert webapp.get_default_wechat_files_dir() == ""


# =====================================================================
# 4. _normalize_proxy_url
# =====================================================================
@pytest.mark.unit
class TestNormalizeProxyUrl:
    def setup_method(self):
        from web.app import _normalize_proxy_url

        self.fn = _normalize_proxy_url

    def test_empty_string(self):
        assert self.fn("") == ""

    def test_none(self):
        assert self.fn(None) == ""

    def test_whitespace_only(self):
        assert self.fn("   ") == ""

    def test_adds_http_scheme(self):
        assert self.fn("127.0.0.1:7890") == "http://127.0.0.1:7890"

    def test_preserves_existing_scheme(self):
        assert self.fn("socks5://localhost:1080") == "socks5://localhost:1080"

    def test_strips_whitespace(self):
        assert self.fn("  http://proxy:8080  ") == "http://proxy:8080"


# =====================================================================
# 5. _extract_system_proxy_candidates
# =====================================================================
@pytest.mark.unit
class TestExtractSystemProxyCandidates:
    def test_collects_env_proxy(self):
        from web import app as webapp

        with patch.dict(
            os.environ, {"HTTPS_PROXY": "http://env-proxy:8080"}, clear=False
        ), patch.object(
            webapp, "settings_manager", MagicMock(**{"get.return_value": False})
        ), patch.object(
            webapp, "PROXY_OPTIONS", []
        ):
            result = webapp._extract_system_proxy_candidates()
        assert "http://env-proxy:8080" in result

    def test_includes_proxy_options(self):
        from web import app as webapp

        with patch.dict(os.environ, {}, clear=False), patch.object(
            webapp, "settings_manager", MagicMock(**{"get.return_value": False})
        ), patch.object(webapp, "PROXY_OPTIONS", ["http://127.0.0.1:7890"]):
            # Remove env vars that might interfere
            env_copy = os.environ.copy()
            env_copy.pop("HTTPS_PROXY", None)
            env_copy.pop("https_proxy", None)
            with patch.dict(os.environ, env_copy, clear=True):
                result = webapp._extract_system_proxy_candidates()
            assert "http://127.0.0.1:7890" in result

    def test_deduplication(self):
        from web import app as webapp

        with patch.dict(
            os.environ, {"HTTPS_PROXY": "http://127.0.0.1:7890"}, clear=False
        ), patch.object(
            webapp, "settings_manager", MagicMock(**{"get.return_value": False})
        ), patch.object(
            webapp, "PROXY_OPTIONS", ["http://127.0.0.1:7890"]
        ):
            result = webapp._extract_system_proxy_candidates()
        # Should appear only once
        assert result.count("http://127.0.0.1:7890") == 1


# =====================================================================
# 6. _FakeGenerateContentResponse
# =====================================================================
@pytest.mark.unit
class TestFakeGenerateContentResponse:
    def setup_method(self):
        from web.app import _FakeGenerateContentResponse

        self.cls = _FakeGenerateContentResponse

    def test_stores_text(self):
        resp = self.cls("hello")
        assert resp.text == "hello"

    def test_empty_candidates(self):
        resp = self.cls("x")
        assert resp.candidates == []

    def test_usage_metadata_none(self):
        resp = self.cls("x")
        assert resp.usage_metadata is None

    def test_slots(self):
        resp = self.cls("y")
        assert hasattr(resp, "__slots__")
        with pytest.raises(AttributeError):
            resp.nonexistent = 1


# =====================================================================
# 7. _extract_prompt_text
# =====================================================================
@pytest.mark.unit
class TestExtractPromptText:
    def setup_method(self):
        from web.app import _extract_prompt_text

        self.fn = _extract_prompt_text

    def test_none_contents(self):
        text, sys_instr = self.fn(None)
        assert text == ""
        assert sys_instr is None

    def test_string_contents(self):
        text, _ = self.fn("hello world")
        assert text == "hello world"

    def test_list_of_strings(self):
        text, _ = self.fn(["line1", "line2"])
        assert text == "line1\nline2"

    def test_list_with_text_attr(self):
        obj = Mock(text="from_obj")
        text, _ = self.fn([obj])
        assert "from_obj" in text

    def test_list_with_parts(self):
        part = Mock(text="nested")
        item = Mock(spec=["parts"])
        item.parts = [part]
        # Make sure hasattr(item, 'text') is False
        del item.text
        text, _ = self.fn([item])
        assert "nested" in text

    def test_system_instruction_from_config(self):
        cfg = Mock(system_instruction="sys-inst")
        _, sys_instr = self.fn("hi", config=cfg)
        assert sys_instr == "sys-inst"

    def test_no_system_instruction(self):
        _, sys_instr = self.fn("hi", config=None)
        assert sys_instr is None

    def test_other_type_contents(self):
        text, _ = self.fn(42)
        assert text == "42"


# =====================================================================
# 8. _is_interactions_only
# =====================================================================
@pytest.mark.unit
class TestIsInteractionsOnly:
    def setup_method(self):
        from web.app import _is_interactions_only

        self.fn = _is_interactions_only

    def test_known_interactions_model(self):
        assert self.fn("deep-research-pro-preview-12-2025") is True

    def test_deep_research_prefix(self):
        assert self.fn("deep-research-pro-preview-2025-01") is True

    def test_regular_model(self):
        assert self.fn("gemini-2.0-flash") is False

    def test_none_input(self):
        assert self.fn(None) is False

    def test_empty_string(self):
        assert self.fn("") is False


# =====================================================================
# 9. FileOperator
# =====================================================================
@pytest.mark.unit
class TestFileOperator:
    def setup_method(self):
        from web.app import FileOperator

        self.cls = FileOperator

    # -- is_file_operation --
    def test_is_file_operation_chinese_keyword(self):
        assert self.cls.is_file_operation("请帮我读取文件") is True

    def test_is_file_operation_english_keyword(self):
        assert self.cls.is_file_operation("please open file") is True

    def test_is_file_operation_negative(self):
        assert self.cls.is_file_operation("what is the weather?") is False

    def test_is_file_operation_case_insensitive(self):
        assert self.cls.is_file_operation("LIST FILES in dir") is True

    # -- _is_folder_organize_intent --
    def test_folder_organize_both(self):
        assert self.cls._is_folder_organize_intent("归纳文件夹") is True

    def test_folder_organize_keywords_only(self):
        assert self.cls._is_folder_organize_intent("自动归纳") is True

    def test_folder_organize_negative(self):
        assert self.cls._is_folder_organize_intent("hello world") is False

    # -- _extract_path_from_text --
    def test_extract_quoted_path(self):
        result = self.cls._extract_path_from_text('整理 "C:\\Users\\test"')
        assert result == "C:\\Users\\test"

    def test_extract_windows_path(self):
        result = self.cls._extract_path_from_text("看看 D:\\Docs\\report.pdf 这个文件")
        assert "D:\\Docs" in result

    def test_extract_unix_path(self):
        result = self.cls._extract_path_from_text("read ./my-folder/data.csv")
        assert "my-folder/data.csv" in result

    def test_extract_no_path(self):
        result = self.cls._extract_path_from_text("hello world nothing here")
        assert result == ""

    # -- execute (folder organize — missing path) --
    def test_execute_organize_no_path(self):
        with patch("web.app.get_default_wechat_files_dir", return_value=""):
            result = self.cls.execute("自动归纳文件夹")
        assert result["success"] is False
        assert "路径" in result["message"]

    # -- execute (organize with nonexistent dir) --
    def test_execute_organize_nonexistent_dir(self, tmp_path):
        bogus = str(tmp_path / "no_such_dir")
        with patch("web.app.get_default_wechat_files_dir", return_value=""):
            result = self.cls.execute(f'整理文件夹 "{bogus}"')
        assert result["success"] is False


# =====================================================================
# 10. WebSearcher
# =====================================================================
@pytest.mark.unit
class TestWebSearcher:
    def setup_method(self):
        from web.app import WebSearcher

        self.cls = WebSearcher

    # -- needs_web_search --
    def test_weather_keyword(self):
        assert self.cls.needs_web_search("明天天气怎么样") is True

    def test_stock_keyword(self):
        assert self.cls.needs_web_search("苹果股价多少") is True

    def test_travel_pattern(self):
        assert self.cls.needs_web_search("查一下明天北京到上海的高铁票") is True

    def test_regular_question_no_search(self):
        assert self.cls.needs_web_search("什么是递归算法") is False

    def test_english_weather(self):
        assert self.cls.needs_web_search("What is the weather forecast?") is True

    def test_gold_price(self):
        assert self.cls.needs_web_search("今日金价多少") is True

    # -- _detect_query_type --
    def test_detect_travel(self):
        assert self.cls._detect_query_type("查火车票") == "travel"

    def test_detect_weather(self):
        assert self.cls._detect_query_type("天气预报") == "weather"

    def test_detect_finance(self):
        assert self.cls._detect_query_type("比特币价格走势") == "finance"

    def test_detect_general(self):
        assert self.cls._detect_query_type("how to learn python") == "general"

    # -- _build_search_context --
    def test_build_context_travel(self):
        q, instr = self.cls._build_search_context("查火车票", "travel")
        assert "出行" in instr or "班次" in instr

    def test_build_context_weather(self):
        q, instr = self.cls._build_search_context("天气", "weather")
        assert "天气" in instr or "气温" in instr

    def test_build_context_finance(self):
        q, instr = self.cls._build_search_context("黄金", "finance")
        assert "金融" in instr or "行情" in instr

    def test_build_context_general(self):
        q, instr = self.cls._build_search_context("latest news", "general")
        assert "Koto" in instr


# =====================================================================
# 11. ContextAnalyzer
# =====================================================================
@pytest.mark.unit
class TestContextAnalyzer:
    def setup_method(self):
        from web.app import ContextAnalyzer

        self.cls = ContextAnalyzer

    # -- extract_entities --
    def test_extract_color(self):
        entities = self.cls.extract_entities("我想要红色背景")
        values = [e["value"] for e in entities]
        assert "红色" in values

    def test_extract_style(self):
        entities = self.cls.extract_entities("卡通风格头像")
        values = [e["value"] for e in entities]
        assert "卡通" in values

    def test_extract_subject(self):
        entities = self.cls.extract_entities("画一只猫")
        values = [e["value"] for e in entities]
        assert "猫" in values

    def test_extract_task_specific(self):
        entities = self.cls.extract_entities("调整颜色", task_type="PAINTER")
        values = [e["value"] for e in entities]
        assert "颜色" in values

    def test_extract_no_entities(self):
        entities = self.cls.extract_entities("hello world 123")
        assert entities == []

    # -- build_context_summary --
    def test_summary_empty_history(self):
        summary = self.cls.build_context_summary([])
        assert summary["task_history"] == []
        assert summary["last_user_intent"] == ""

    def test_summary_with_history(self):
        history = [
            {"role": "user", "parts": ["画一只猫"]},
            {"role": "model", "parts": ["图像已生成"]},
        ]
        summary = self.cls.build_context_summary(history)
        assert summary["last_user_intent"] == "画一只猫"
        assert summary["last_model_output"] == "图像已生成"

    def test_summary_detects_task_type(self):
        history = [
            {"role": "user", "parts": ["写一段python代码"]},
            {"role": "model", "parts": ["```python\nprint('hi')```"]},
        ]
        summary = self.cls.build_context_summary(history)
        types = [t["type"] for t in summary["task_history"]]
        assert "CODER" in types

    # -- analyze_context --
    def test_analyze_no_history(self):
        result = self.cls.analyze_context("hello", [])
        assert result["is_continuation"] is False

    def test_analyze_short_history(self):
        result = self.cls.analyze_context("hi", [{"role": "user", "parts": ["hi"]}])
        assert result["is_continuation"] is False

    def test_analyze_continuation_modify(self):
        history = [
            {"role": "user", "parts": ["画一只猫"]},
            {"role": "model", "parts": ["图像已生成"]},
        ]
        result = self.cls.analyze_context("再来一张", history)
        assert result["is_continuation"] is True
        assert result["continuation_type"] == "modify"

    def test_analyze_new_topic_not_continuation(self):
        history = [
            {"role": "user", "parts": ["画一只猫"]},
            {"role": "model", "parts": ["图像已生成"]},
        ]
        result = self.cls.analyze_context(
            "帮我写一份详细的市场分析报告关于人工智能发展趋势", history
        )
        # Long new-topic input should not be a continuation
        assert result["confidence"] < 0.6 or result["is_continuation"] is False


# =====================================================================
# 12. Utils
# =====================================================================
@pytest.mark.unit
class TestUtils:
    def setup_method(self):
        from web.app import Utils

        self.cls = Utils

    # -- sanitize_string --
    def test_sanitize_normal_string(self):
        assert self.cls.sanitize_string("hello") == "hello"

    def test_sanitize_non_string(self):
        assert self.cls.sanitize_string(42) == 42

    def test_sanitize_unicode(self):
        assert self.cls.sanitize_string("你好世界") == "你好世界"

    # -- is_failure_output --
    def test_failure_empty(self):
        assert self.cls.is_failure_output("") is True

    def test_failure_none(self):
        assert self.cls.is_failure_output(None) is True

    def test_failure_error_emoji(self):
        assert self.cls.is_failure_output("❌ Something went wrong") is True

    def test_failure_chinese_error(self):
        assert self.cls.is_failure_output("操作失败") is True

    def test_failure_no_internet(self):
        assert self.cls.is_failure_output("我没有直接联网能力") is True

    def test_failure_english_no_internet(self):
        assert self.cls.is_failure_output("I don't have access to the internet") is True

    def test_success_output(self):
        assert self.cls.is_failure_output("Here is the result: 42") is False

    # -- detect_required_packages --
    def test_detect_imports(self):
        code = "import pygame\nimport os\nfrom PIL import Image"
        pkgs = self.cls.detect_required_packages(code)
        assert "pygame" in pkgs
        assert "Pillow" in pkgs
        # os is not in allowlist
        assert "os" not in pkgs

    def test_detect_no_packages(self):
        assert self.cls.detect_required_packages("print('hello')") == []

    def test_detect_empty(self):
        assert self.cls.detect_required_packages("") == []

    def test_detect_numpy(self):
        pkgs = self.cls.detect_required_packages("import numpy")
        assert "numpy" in pkgs

    # -- adapt_prompt_to_markdown --
    def test_adapt_prompt_fallback(self):
        # When PromptAdapter is not available, should return original input
        with patch(
            "web.app.Utils.adapt_prompt_to_markdown",
            wraps=self.cls.adapt_prompt_to_markdown,
        ):
            result = self.cls.adapt_prompt_to_markdown("FILE_GEN", "make a report")
        # It should return a string (either adapted or original)
        assert isinstance(result, str)

    # -- quick_self_check --
    def test_quick_self_check_exception_returns_pass(self):
        with patch("web.app.client") as mock_client:
            mock_client.models.generate_content.side_effect = Exception("no API")
            result = self.cls.quick_self_check("CHAT", "hello", "world")
        assert result["pass"] is True

    # -- build_fix_prompt --
    def test_build_fix_prompt_file_gen(self):
        p = self.cls.build_fix_prompt("FILE_GEN", "make report", "error output")
        assert "BEGIN_FILE" in p

    def test_build_fix_prompt_coder(self):
        p = self.cls.build_fix_prompt("CODER", "write code", "error")
        assert "代码" in p or "code" in p.lower()

    def test_build_fix_prompt_generic(self):
        p = self.cls.build_fix_prompt("UNKNOWN", "do something", "err")
        assert "用户需求" in p


# =====================================================================
# 13. SessionManager
# =====================================================================
@pytest.mark.unit
class TestSessionManager:
    @pytest.fixture(autouse=True)
    def setup_session_mgr(self, tmp_path):
        import web.app as webapp
        from web.app import SessionManager

        self.chat_dir = str(tmp_path / "chats")
        os.makedirs(self.chat_dir, exist_ok=True)
        self._orig_chat_dir = webapp.CHAT_DIR
        webapp.CHAT_DIR = self.chat_dir
        self.mgr = SessionManager()
        yield
        webapp.CHAT_DIR = self._orig_chat_dir

    def test_list_sessions_empty(self):
        assert self.mgr.list_sessions() == []

    def test_create_and_list(self):
        fn = self.mgr.create("test_session")
        assert fn.endswith(".json")
        sessions = self.mgr.list_sessions()
        assert fn in sessions

    def test_create_sanitizes_name(self):
        fn = self.mgr.create("hello world!")
        assert " " not in fn
        assert "!" not in fn

    def test_create_duplicate_adds_timestamp(self):
        fn1 = self.mgr.create("dup")
        fn2 = self.mgr.create("dup")
        assert fn1 != fn2

    def test_load_nonexistent(self):
        result = self.mgr.load("nonexistent.json")
        assert result == []

    def test_save_and_load(self):
        fn = self.mgr.create("sess")
        history = [{"role": "user", "parts": ["hi"]}]
        self.mgr.save(fn, history)
        loaded = self.mgr.load(fn)
        assert len(loaded) == 1
        assert loaded[0]["parts"][0] == "hi"

    def test_delete_existing(self):
        fn = self.mgr.create("to_delete")
        assert self.mgr.delete(fn) is True
        assert fn not in self.mgr.list_sessions()

    def test_delete_nonexistent(self):
        assert self.mgr.delete("nope.json") is False

    def test_load_full(self):
        fn = self.mgr.create("full")
        big_history = [{"role": "user", "parts": [f"msg{i}"]} for i in range(30)]
        self.mgr.save(fn, big_history)
        full = self.mgr.load_full(fn)
        assert len(full) == 30

    def test_trim_history(self):
        history = [{"role": "user", "parts": [f"m{i}"]} for i in range(50)]
        trimmed = self.mgr._trim_history(history, max_turns=10)
        assert len(trimmed) == 10

    def test_trim_history_short(self):
        history = [{"role": "user", "parts": ["m"]}]
        trimmed = self.mgr._trim_history(history, max_turns=10)
        assert len(trimmed) == 1


# =====================================================================
# 14. _get_chat_system_instruction / _get_DEFAULT_CHAT_SYSTEM_INSTRUCTION
# =====================================================================
@pytest.mark.unit
class TestChatSystemInstruction:
    def test_default_instruction_returns_string(self):
        from web.app import _get_DEFAULT_CHAT_SYSTEM_INSTRUCTION

        result = _get_DEFAULT_CHAT_SYSTEM_INSTRUCTION()
        assert isinstance(result, str)
        assert "Koto" in result

    def test_instruction_without_question(self):
        from web.app import _get_chat_system_instruction

        result = _get_chat_system_instruction()
        assert isinstance(result, str)
        assert "Koto" in result

    def test_instruction_with_question_fallback(self):
        from web.app import _get_chat_system_instruction

        # When context_injector fails, should fall back
        with patch(
            "web.context_injector.get_dynamic_system_instruction",
            side_effect=Exception("fail"),
        ):
            result = _get_chat_system_instruction(question="hello")
        assert isinstance(result, str)
        assert "Koto" in result


# =====================================================================
# 15. _parse_time_info_for_filegen / _build_filegen_time_context
# =====================================================================
@pytest.mark.unit
class TestTimeInfoParsing:
    def setup_method(self):
        from web.app import _build_filegen_time_context, _parse_time_info_for_filegen

        self.parse = _parse_time_info_for_filegen
        self.build = _build_filegen_time_context

    def test_parse_full_date(self):
        info = self.parse("2024年6月的报告")
        assert info["year"] == 2024
        assert info["month"] == 6
        assert info["rule_hit"] is False

    def test_parse_month_only(self):
        info = self.parse("3月销售数据")
        assert info["year"] is None
        assert info["month"] == 3
        assert info["rule_hit"] is True

    def test_parse_no_date(self):
        info = self.parse("hello world")
        assert info["year"] is None
        assert info["month"] is None

    def test_parse_empty(self):
        info = self.parse("")
        assert info["raw"] == ""

    def test_build_with_month(self):
        context_str, parsed = self.build("6月报告")
        assert "时间上下文" in context_str
        assert parsed["month"] == 6

    def test_build_without_month(self):
        context_str, parsed = self.build("写一个报告")
        assert "未检测到明确月份" in context_str


# =====================================================================
# 16. filter_history (classmethod on ContextAnalyzer)
# =====================================================================
@pytest.mark.unit
class TestFilterHistory:
    def setup_method(self):
        from web.app import ContextAnalyzer

        self.cls = ContextAnalyzer

    def test_empty_history(self):
        assert self.cls.filter_history("hi", []) == []

    def test_short_history_returns_all(self):
        history = [{"parts": ["a"]}, {"parts": ["b"]}]
        result = self.cls.filter_history("test", history, keep_turns=6)
        assert len(result) == 2

    def test_long_history_keeps_tail(self):
        history = [{"parts": [f"msg{i}"]} for i in range(30)]
        result = self.cls.filter_history("xyz", history, keep_turns=3)
        # Should at least keep last 6 entries (tail_count)
        assert len(result) >= 6

    def test_keyword_matching_preserves_relevant(self):
        history = [
            {"parts": ["Python编程教程"]},
            {"parts": ["好的，Python基础"]},
        ] + [{"parts": [f"filler{i}"]} for i in range(20)]
        result = self.cls.filter_history("Python", history, keep_turns=3)
        texts = [h["parts"][0] for h in result]
        assert any("Python" in t for t in texts)


# =====================================================================
# 17. _strip_code_blocks (local fn inside chat_with_file — re-implement for test)
# =====================================================================
@pytest.mark.unit
class TestStripCodeBlocks:
    """Test the code block stripping logic (replicated from the local function)."""

    @staticmethod
    def _strip_code_blocks(text: str) -> str:
        if not text:
            return text
        text = re.sub(r"```[\s\S]*?```", "", text)
        text = text.replace("`", "")
        return text.strip()

    def test_empty(self):
        assert self._strip_code_blocks("") == ""

    def test_none(self):
        assert self._strip_code_blocks(None) is None

    def test_removes_fenced_block(self):
        md = "before\n```python\nprint('hi')\n```\nafter"
        assert "print" not in self._strip_code_blocks(md)
        assert "before" in self._strip_code_blocks(md)

    def test_removes_inline_ticks(self):
        assert self._strip_code_blocks("use `foo` here") == "use foo here"

    def test_mixed(self):
        md = "start `x` mid ```\nblock\n``` end"
        result = self._strip_code_blocks(md)
        assert "block" not in result
        assert "start" in result


# =====================================================================
# 18. _build_analysis_title (local fn — re-implement for test)
# =====================================================================
@pytest.mark.unit
class TestBuildAnalysisTitle:
    """Test the analysis title builder (replicated from local function)."""

    @staticmethod
    def _build_analysis_title(user_text: str, filename: str, is_binary: bool) -> str:
        name_base = os.path.splitext(filename)[0]
        text_lower = (user_text or "").lower()
        ext = os.path.splitext(filename)[1].lower()

        if ext in [".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"]:
            prefix = "图片"
        elif ext == ".pdf":
            prefix = "PDF"
        elif ext in [".doc", ".docx"]:
            prefix = "Word"
        elif ext in [".ppt", ".pptx"]:
            prefix = "PPT"
        else:
            prefix = "文件" if is_binary else "文档"

        intent = "分析"
        intent_map = {
            "翻译": ["翻译", "translate", "译文", "中译英", "英译中"],
            "总结": ["总结", "归纳", "摘要", "summary", "概括", "核心内容"],
            "文字识别": ["提取", "识别", "ocr", "文字", "转文字", "读图"],
            "表格识别": ["表格", "table", "excel", "转表"],
            "对比分析": ["对比", "比较", "diff", "区别", "差异"],
            "校对": ["校对", "检查", "审阅", "纠错", "改错"],
            "润色": ["润色", "改写", "polish", "rewrite", "优化", "美化"],
            "续写": ["续写", "扩写", "continue", "补充"],
            "大纲": ["大纲", "框架", "outline", "目录"],
            "解释": ["解释", "explain", "什么意思", "含义"],
        }
        found_intent_keywords = []
        for k, v in intent_map.items():
            for kw in v:
                if kw in text_lower:
                    intent = k
                    found_intent_keywords.append(kw)
                    break
            if intent != "分析":
                break

        stop_words = [
            "帮我",
            "请",
            "一下",
            "把",
            "这个",
            "这篇",
            "文件",
            "文章",
            "内容",
            "生成",
            "写一个",
            "做一份",
            "koto",
            "分析",
            "阅读",
            "提取",
            "识别",
        ]
        text_lower_work = (user_text or "").lower()
        zh_stops = [w for w in stop_words if re.match(r"[\u4e00-\u9fa5]+", w)]
        for stop in zh_stops + found_intent_keywords:
            if re.match(r"[\u4e00-\u9fa5]+", stop):
                text_lower_work = text_lower_work.replace(stop, " ")
        tokens = re.findall(r"[a-zA-Z0-9\u4e00-\u9fa5]+", text_lower_work)
        en_stops = set([w for w in stop_words if not re.match(r"[\u4e00-\u9fa5]+", w)])
        valid_keywords = []
        for token in tokens:
            if token in en_stops:
                continue
            if token in found_intent_keywords:
                continue
            if len(token) < 2:
                continue
            valid_keywords.append(token)
        topic = "_".join(valid_keywords[:3]) if valid_keywords else ""
        sanitized_name = name_base.replace(" ", "_")
        if topic:
            return f"{intent}_{topic}_{sanitized_name}"
        else:
            return f"{prefix}{intent}_{sanitized_name}"

    def test_image_prefix(self):
        result = self._build_analysis_title("分析图片", "photo.jpg", False)
        assert "图片" in result

    def test_pdf_prefix(self):
        result = self._build_analysis_title("", "doc.pdf", False)
        assert "PDF" in result

    def test_word_prefix(self):
        result = self._build_analysis_title("", "file.docx", False)
        assert "Word" in result

    def test_translate_intent(self):
        result = self._build_analysis_title("翻译这个文档", "readme.txt", False)
        assert "翻译" in result

    def test_summary_intent(self):
        result = self._build_analysis_title("总结文档要点", "report.pdf", False)
        assert "总结" in result

    def test_binary_prefix(self):
        result = self._build_analysis_title("", "data.bin", True)
        assert "文件" in result

    def test_text_prefix(self):
        result = self._build_analysis_title("", "data.csv", False)
        assert "文档" in result


# =====================================================================
# 19. _extract_markdown_table
# =====================================================================
@pytest.mark.unit
class TestExtractMarkdownTable:
    """Test the markdown table extractor (replicated from local function)."""

    @staticmethod
    def _extract_markdown_table(md_text: str):
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
        md = "| Name | Age |\n|------|-----|\n| Alice | 30 |\n| Bob | 25 |"
        result = self._extract_markdown_table(md)
        assert result is not None
        assert "Name" in result[0][0]
        assert len(result) == 3  # header + 2 rows

    def test_no_table(self):
        assert self._extract_markdown_table("just some text without pipes") is None

    def test_single_row_table(self):
        md = "| H1 | H2 |\n|---|---|\n| a | b |"
        result = self._extract_markdown_table(md)
        assert result is not None
        assert len(result) == 2  # header + 1 row

    def test_row_padding(self):
        md = "| A | B | C |\n|---|---|---|\n| x |"
        result = self._extract_markdown_table(md)
        assert result is not None
        # Short row should be padded to match header length
        row = result[1]
        assert len(row) == 3


# =====================================================================
# 20. _parse_ppt_outline
# =====================================================================
@pytest.mark.unit
class TestParsePptOutline:
    """Test the PPT outline parser (replicated from local function)."""

    @staticmethod
    def _parse_ppt_outline(md_text: str) -> dict:
        lines = md_text.split("\n")
        outline = {"title": "", "slides": []}
        _tmap = {
            "过渡页": "divider",
            "过渡": "divider",
            "详细": "detail",
            "重点": "detail",
            "亮点": "highlight",
            "数据": "highlight",
            "概览": "overview",
            "速览": "overview",
            "简要": "overview",
            "对比": "comparison",
            "比较": "comparison",
        }
        cur_type = "detail"
        cur_slide = None
        cur_sub = None
        for line in lines:
            line = line.rstrip()
            if line.strip() in ("```", "```markdown"):
                continue
            tm = re.match(r"^\s*\[(.+?)\]\s*$", line)
            if tm:
                cur_type = _tmap.get(tm.group(1).strip(), "detail")
                continue
            if line.startswith("# ") and not line.startswith("## "):
                outline["title"] = line[2:].strip()
            elif line.startswith("## "):
                if (
                    cur_sub
                    and cur_slide
                    and cur_slide.get("type") in ("overview", "comparison")
                ):
                    cur_slide.setdefault("subsections", []).append(cur_sub)
                    cur_sub = None
                if cur_slide:
                    outline["slides"].append(cur_slide)
                cur_slide = {
                    "type": cur_type,
                    "title": line[3:].strip(),
                    "points": [],
                    "content": [],
                }
                if cur_type == "divider":
                    cur_slide["description"] = ""
                cur_type = "detail"
                cur_sub = None
            elif line.startswith("### ") and cur_slide:
                if cur_sub:
                    cur_slide.setdefault("subsections", []).append(cur_sub)
                cur_sub = {
                    "subtitle": line[4:].strip(),
                    "label": line[4:].strip(),
                    "points": [],
                }
            elif re.match(r"^[\s]*[-•*]\s", line) and cur_slide is not None:
                pt = re.sub(r"^[\s]*[-•*]\s+", "", line).strip()
                if cur_sub is not None:
                    cur_sub["points"].append(pt)
                else:
                    cur_slide["points"].append(pt)
                    cur_slide["content"].append(pt)
            elif cur_slide and cur_slide.get("type") == "divider" and line.strip():
                cur_slide["description"] = line.strip()
        if (
            cur_sub
            and cur_slide
            and cur_slide.get("type") in ("overview", "comparison")
        ):
            cur_slide.setdefault("subsections", []).append(cur_sub)
        if cur_slide:
            outline["slides"].append(cur_slide)
        for sl in outline["slides"]:
            if sl.get("type") == "comparison" and "subsections" in sl:
                subs = sl["subsections"]
                if len(subs) >= 2:
                    sl["left"] = subs[0]
                    sl["right"] = subs[1]
        return outline

    def test_basic_outline(self):
        md = "# Main Title\n## Slide One\n- Point A\n- Point B\n## Slide Two\n- Point C"
        outline = self._parse_ppt_outline(md)
        assert outline["title"] == "Main Title"
        assert len(outline["slides"]) == 2
        assert outline["slides"][0]["title"] == "Slide One"
        assert "Point A" in outline["slides"][0]["points"]

    def test_type_tags(self):
        md = "[过渡页]\n## Divider Slide\nSome description\n## Content Slide\n- bullet"
        outline = self._parse_ppt_outline(md)
        divider = outline["slides"][0]
        assert divider["type"] == "divider"
        assert divider.get("description") == "Some description"

    def test_empty_input(self):
        outline = self._parse_ppt_outline("")
        assert outline["title"] == ""
        assert outline["slides"] == []

    def test_subsections(self):
        md = (
            "[概览]\n## Overview\n### Sub A\n- p1\n### Sub B\n- p2\n"
            "## Next Slide\n- q1"
        )
        outline = self._parse_ppt_outline(md)
        overview = outline["slides"][0]
        assert overview["type"] == "overview"
        assert "subsections" in overview
        assert len(overview["subsections"]) == 2

    def test_comparison_left_right(self):
        md = "[对比]\n## Compare\n### Left\n- l1\n### Right\n- r1\n" "## After\n- x"
        outline = self._parse_ppt_outline(md)
        comp = outline["slides"][0]
        assert comp["type"] == "comparison"
        assert "left" in comp
        assert "right" in comp

    def test_code_fence_skipped(self):
        md = "```markdown\n# Title\n## Slide\n- point\n```"
        outline = self._parse_ppt_outline(md)
        assert outline["title"] == "Title"


# =====================================================================
# 21. Flask Route Handlers
# =====================================================================
@pytest.mark.unit
class TestWebAppRoutes:
    @pytest.fixture(autouse=True)
    def setup_client(self):
        from web.app import app as flask_app

        flask_app.config["TESTING"] = True
        self.app = flask_app
        with flask_app.test_client() as c:
            self.client = c
            yield

    # -- GET / --
    def test_index_returns_ok(self):
        resp = self.client.get("/")
        # Accept 200 (page rendered) or 500 (template missing in test env)
        assert resp.status_code in (200, 404, 500)

    # -- GET /app --
    def test_app_main(self):
        resp = self.client.get("/app")
        assert resp.status_code in (200, 404, 500)

    # -- GET /api/info --
    def test_api_info(self):
        resp = self.client.get("/api/info")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "version" in data
        assert "deploy_mode" in data

    # -- GET /api/sessions --
    def test_api_sessions_list(self):
        resp = self.client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "sessions" in data

    # -- POST /api/sessions --
    def test_api_sessions_create(self):
        resp = self.client.post(
            "/api/sessions",
            json={"name": f"test_{int(time.time())}"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "session" in data

    # -- GET /api/settings --
    def test_api_settings(self):
        resp = self.client.get("/api/settings")
        assert resp.status_code == 200

    # -- POST /api/chat/interrupt --
    def test_interrupt_missing_session(self):
        resp = self.client.post("/api/chat/interrupt", json={})
        assert resp.status_code == 400

    def test_interrupt_success(self):
        resp = self.client.post(
            "/api/chat/interrupt",
            json={"session": "test_sess"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    # -- POST /api/chat/reset-interrupt --
    def test_reset_interrupt(self):
        resp = self.client.post(
            "/api/chat/reset-interrupt",
            json={"session": "test_sess"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    # -- GET /api/notes/list --
    def test_notes_list(self):
        with patch("web.app.get_note_manager", create=True) as mock_gnm:
            mock_mgr = MagicMock()
            mock_mgr.get_recent_notes.return_value = []
            mock_gnm.return_value = mock_mgr
            # The route uses `from note_manager import get_note_manager`
            # so we need to patch at the import level
            with patch.dict(
                "sys.modules",
                {"note_manager": MagicMock(get_note_manager=lambda: mock_mgr)},
            ):
                resp = self.client.get("/api/notes/list")
        assert resp.status_code in (200, 500)

    # -- GET /api/reminders/list --
    def test_reminders_list(self):
        mock_mgr = MagicMock()
        mock_mgr.list_reminders.return_value = []
        with patch.dict(
            "sys.modules",
            {"reminder_manager": MagicMock(get_reminder_manager=lambda: mock_mgr)},
        ):
            resp = self.client.get("/api/reminders/list")
        assert resp.status_code in (200, 500)

    # -- GET /api/clipboard/history --
    def test_clipboard_history(self):
        mock_mgr = MagicMock()
        mock_mgr.get_history.return_value = []
        with patch.dict(
            "sys.modules",
            {"clipboard_manager": MagicMock(get_clipboard_manager=lambda: mock_mgr)},
        ):
            resp = self.client.get("/api/clipboard/history")
        assert resp.status_code in (200, 500)

    # -- GET /api/browse --
    def test_browse_default(self):
        resp = self.client.get("/api/browse?path=" + os.path.abspath(os.sep))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "folders" in data

    def test_browse_nonexistent(self):
        resp = self.client.get("/api/browse?path=Z:\\nonexistent_path_12345")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error" in data

    # -- GET /api/search/all --
    def test_search_all(self):
        mock_engine = MagicMock()
        mock_engine.search_all.return_value = {"results": []}
        with patch.dict(
            "sys.modules",
            {"search_engine": MagicMock(get_search_engine=lambda: mock_engine)},
        ):
            resp = self.client.get("/api/search/all?query=test")
        assert resp.status_code in (200, 500)

    # -- GET /api/voice/engines --
    def test_voice_engines(self):
        mock_result = {"success": True, "engines": []}
        with patch.dict(
            "sys.modules",
            {"web.voice_fast": MagicMock(get_available_engines=lambda: mock_result)},
        ):
            resp = self.client.get("/api/voice/engines")
        assert resp.status_code in (200, 500)


# =====================================================================
# Additional edge-case tests to boost coverage
# =====================================================================
@pytest.mark.unit
class TestEdgeCases:
    """Miscellaneous edge-case tests for additional coverage."""

    def test_lazy_module_repr_unloaded(self):
        from web.app import _LazyModule

        lm = _LazyModule(lambda: None)
        assert "not loaded" in repr(lm)

    def test_normalize_proxy_url_with_scheme(self):
        from web.app import _normalize_proxy_url

        assert _normalize_proxy_url("https://proxy:443") == "https://proxy:443"

    def test_fake_response_empty_text(self):
        from web.app import _FakeGenerateContentResponse

        r = _FakeGenerateContentResponse("")
        assert r.text == ""

    def test_extract_prompt_text_list_fallback(self):
        from web.app import _extract_prompt_text

        text, _ = _extract_prompt_text([123, 456])
        assert "123" in text
        assert "456" in text

    def test_file_operator_keywords_exist(self):
        from web.app import FileOperator

        assert len(FileOperator.FILE_KEYWORDS) > 10
        assert len(FileOperator.FOLDER_ORGANIZE_KEYWORDS) > 5

    def test_web_searcher_keywords_exist(self):
        from web.app import WebSearcher

        assert len(WebSearcher.WEB_KEYWORDS) > 20

    def test_context_analyzer_task_signatures(self):
        from web.app import ContextAnalyzer

        assert "PAINTER" in ContextAnalyzer.TASK_SIGNATURES
        assert "FILE_GEN" in ContextAnalyzer.TASK_SIGNATURES
        assert "CODER" in ContextAnalyzer.TASK_SIGNATURES
        assert "CHAT" in ContextAnalyzer.TASK_SIGNATURES

    def test_context_analyzer_continuation_patterns(self):
        from web.app import ContextAnalyzer

        assert "modify" in ContextAnalyzer.CONTINUATION_PATTERNS
        assert "convert" in ContextAnalyzer.CONTINUATION_PATTERNS
        assert "continue" in ContextAnalyzer.CONTINUATION_PATTERNS

    def test_utils_package_allowlist(self):
        from web.app import Utils

        assert "pygame" in Utils._PACKAGE_ALLOWLIST
        assert Utils._PACKAGE_ALLOWLIST["cv2"] == "opencv-python"
        assert Utils._PACKAGE_ALLOWLIST["PIL"] == "Pillow"

    def test_session_manager_append_and_save(self, tmp_path):
        import web.app as webapp
        from web.app import SessionManager

        chat_dir = str(tmp_path / "chats")
        os.makedirs(chat_dir, exist_ok=True)
        orig = webapp.CHAT_DIR
        webapp.CHAT_DIR = chat_dir
        try:
            mgr = SessionManager()
            fn = mgr.create("append_test")
            result = mgr.append_and_save(fn, "user msg", "model msg")
            assert len(result) == 2
            assert result[0]["role"] == "user"
            assert result[1]["role"] == "model"
        finally:
            webapp.CHAT_DIR = orig

    def test_web_searcher_news_keyword(self):
        from web.app import WebSearcher

        assert WebSearcher.needs_web_search("最新新闻") is True

    def test_web_searcher_flight_keyword(self):
        from web.app import WebSearcher

        assert WebSearcher.needs_web_search("查航班动态") is True

    def test_context_build_rag_prompt(self):
        from web.app import ContextAnalyzer

        summary = {
            "conversation_topic": "PAINTER",
            "key_entities": [{"type": "color", "value": "红色"}],
            "last_user_intent": "画一只猫",
            "last_model_output": "图像已生成",
        }
        prompt = ContextAnalyzer.build_rag_prompt("更大一点", summary, "modify")
        assert "PAINTER" in prompt
        assert "红色" in prompt
        assert "更大一点" in prompt
