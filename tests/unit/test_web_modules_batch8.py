#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Batch 8 – Unit tests for 12 web modules at 0% coverage.

Modules covered:
  1. web.feedback_loop
  2. web.proactive_dialogue
  3. web.context_awareness
  4. web.context_injector
  5. web.concept_extractor
  6. web.clipboard_manager
  7. web.insight_reporter
  8. web.prompt_adapter
  9. web.memory_integration
 10. web.doc_planner
 11. web.doc_converter
 12. web.data_pipeline
"""

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, mock_open, patch

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# 1. FeedbackLoopManager
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFeedbackLoopManager:
    """Tests for web.feedback_loop.FeedbackLoopManager"""

    def _make_manager(self):
        from web.feedback_loop import FeedbackLoopManager

        client = MagicMock()
        mgr = FeedbackLoopManager(get_client_func=lambda: client)
        return mgr, client

    def test_init_attributes(self):
        mgr, _ = self._make_manager()
        assert mgr.improvement_iterations == 0
        assert mgr.max_iterations == 2

    def test_improve_document_no_improvement_needed(self):
        mgr, _ = self._make_manager()
        evaluation = {"needs_improvement": False, "overall_score": 90}
        cb = MagicMock()
        result = mgr.improve_document_content(
            "content", evaluation, "title", progress_callback=cb
        )
        assert result["iterations"] == 0
        assert result["improved_content"] == "content"
        cb.assert_called_once()

    def test_improve_document_no_improvement_needed_no_callback(self):
        mgr, _ = self._make_manager()
        evaluation = {"needs_improvement": False, "overall_score": 85}
        result = mgr.improve_document_content("content", evaluation, "title")
        assert result["message"] == "文档已达到质量标准"

    def test_improve_document_gemini_returns_none(self):
        mgr, client = self._make_manager()
        client.models.generate_content.return_value = MagicMock(text=None)
        evaluation = {
            "needs_improvement": True,
            "overall_score": 40,
            "improvement_priority": ["a"],
        }
        result = mgr.improve_document_content("content", evaluation, "title")
        assert result["iterations"] == 1
        assert any(not h["success"] for h in result["improvement_history"])

    def test_improve_document_gemini_exception(self):
        """When _call_gemini_for_improvement catches an internal error and returns None,
        the outer loop records success=False."""
        mgr, client = self._make_manager()
        # _call_gemini_for_improvement catches exceptions internally → returns None
        client.models.generate_content.side_effect = RuntimeError("API error")
        evaluation = {
            "needs_improvement": True,
            "overall_score": 30,
            "improvement_priority": [],
            "issues": [],
            "suggestions": [],
        }
        cb = MagicMock()
        result = mgr.improve_document_content(
            "content", evaluation, "title", progress_callback=cb
        )
        assert result["improvement_history"][0]["success"] is False

    def test_improve_ppt_outline_no_improvement_needed(self):
        mgr, _ = self._make_manager()
        evaluation = {"needs_improvement": False, "overall_score": 95}
        result = mgr.improve_ppt_outline([], evaluation, "PPT title")
        assert result["iterations"] == 0

    def test_improve_ppt_outline_json_decode_error(self):
        mgr, client = self._make_manager()
        resp = MagicMock()
        resp.text = "not valid json"
        client.models.generate_content.return_value = resp
        evaluation = {
            "needs_improvement": True,
            "overall_score": 30,
            "improvement_priority": [],
        }
        result = mgr.improve_ppt_outline([{"slide": 1}], evaluation, "PPT title")
        assert any(not h["success"] for h in result["improvement_history"])

    def test_improve_ppt_outline_success(self):
        mgr, client = self._make_manager()
        new_outline = [{"slide": 1, "title": "Improved"}]
        resp = MagicMock()
        resp.text = json.dumps(new_outline)
        client.models.generate_content.return_value = resp
        evaluation = {
            "needs_improvement": True,
            "overall_score": 50,
            "improvement_priority": ["structure"],
        }
        cb = MagicMock()
        result = mgr.improve_ppt_outline(
            [{"slide": 1}], evaluation, "PPT title", progress_callback=cb
        )
        assert result["improved_outline"] == new_outline

    def test_build_improvement_prompt(self):
        mgr, _ = self._make_manager()
        evaluation = {
            "overall_score": 50,
            "issues": ["too short"],
            "suggestions": ["add details"],
        }
        prompt = mgr._build_improvement_prompt("content", evaluation, "Title", 1)
        assert "too short" in prompt
        assert "add details" in prompt
        assert "Title" in prompt

    def test_create_feedback_manager_factory(self):
        from web.feedback_loop import create_feedback_manager

        mgr = create_feedback_manager(lambda: MagicMock())
        from web.feedback_loop import FeedbackLoopManager

        assert isinstance(mgr, FeedbackLoopManager)


# ──────────────────────────────────────────────────────────────────────────────
# 2. ProactiveDialogueEngine
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestProactiveDialogueEngine:
    """Tests for web.proactive_dialogue.ProactiveDialogueEngine"""

    def _make_engine(self, tmp_path):
        from web.proactive_dialogue import ProactiveDialogueEngine

        db_path = str(tmp_path / "dialogue.db")
        engine = ProactiveDialogueEngine(db_path=db_path)
        return engine

    def test_init_creates_database(self, tmp_path):
        engine = self._make_engine(tmp_path)
        assert os.path.exists(engine.db_path)
        conn = sqlite3.connect(engine.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert "dialogue_history" in tables
        assert "trigger_rules" in tables
        assert "user_states" in tables

    def test_start_and_stop_monitoring(self, tmp_path):
        engine = self._make_engine(tmp_path)
        engine.start_monitoring(check_interval=9999)
        assert engine.running is True
        assert engine.thread is not None
        engine.stop_monitoring()
        assert engine.running is False

    def test_start_monitoring_idempotent(self, tmp_path):
        engine = self._make_engine(tmp_path)
        engine.start_monitoring(check_interval=9999)
        thread1 = engine.thread
        engine.start_monitoring(check_interval=9999)
        assert engine.thread is thread1
        engine.stop_monitoring()

    def test_get_scene_title(self, tmp_path):
        engine = self._make_engine(tmp_path)
        assert engine._get_scene_title("morning_greeting") == "Koto 问候"
        assert engine._get_scene_title("work_too_long") == "休息提醒"
        assert engine._get_scene_title("unknown") == "Koto 提醒"

    def test_trigger_dialogue_and_history(self, tmp_path):
        engine = self._make_engine(tmp_path)
        engine._trigger_dialogue("user1", "tips", {"key": "val"})
        history = engine.get_dialogue_history("user1")
        assert len(history) >= 1
        assert history[0]["scene_type"] == "tips"

    def test_manual_trigger(self, tmp_path):
        engine = self._make_engine(tmp_path)
        engine.manual_trigger("user1", "tips", foo="bar")
        history = engine.get_dialogue_history("user1")
        assert len(history) >= 1

    def test_update_user_state_new_user(self, tmp_path):
        engine = self._make_engine(tmp_path)
        engine._update_user_state("new_user")
        state = engine._get_user_state("new_user")
        assert state is not None
        assert state["user_id"] == "new_user"

    def test_update_user_state_existing_user(self, tmp_path):
        engine = self._make_engine(tmp_path)
        engine._update_user_state("u1")
        engine._update_user_state("u1")
        state = engine._get_user_state("u1")
        assert state is not None

    def test_get_enabled_rules(self, tmp_path):
        engine = self._make_engine(tmp_path)
        rules = engine._get_enabled_rules()
        assert len(rules) > 0
        assert all("scene_type" in r for r in rules)


# ──────────────────────────────────────────────────────────────────────────────
# 3. ContextAwarenessSystem
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestContextAwarenessSystem:
    """Tests for web.context_awareness.ContextAwarenessSystem"""

    def _make_system(self, tmp_path):
        from web.context_awareness import ContextAwarenessSystem

        db_path = str(tmp_path / "ctx_awareness.db")
        return ContextAwarenessSystem(db_path=db_path)

    def test_init_creates_database(self, tmp_path):
        system = self._make_system(tmp_path)
        assert os.path.exists(system.db_path)

    def test_detect_context_without_monitor(self, tmp_path):
        system = self._make_system(tmp_path)
        result = system.detect_context("user1")
        assert "context_type" in result
        assert "confidence" in result
        assert "behavior_config" in result

    def test_get_current_context_initially_none(self, tmp_path):
        system = self._make_system(tmp_path)
        assert system.get_current_context() is None

    def test_get_behavior_config_default(self, tmp_path):
        system = self._make_system(tmp_path)
        config = system.get_behavior_config()
        assert "suggestion_frequency" in config

    def test_get_behavior_config_specific(self, tmp_path):
        system = self._make_system(tmp_path)
        config = system.get_behavior_config("learning")
        assert config["focus_areas"] == [
            "knowledge_management",
            "concept_extraction",
            "related_content",
        ]

    def test_set_and_get_user_preference(self, tmp_path):
        system = self._make_system(tmp_path)
        system.set_user_preference("u1", "professional", "theme", "dark")
        prefs = system.get_user_preferences("u1", "professional")
        assert prefs.get("theme") == "dark"

    def test_get_context_history_empty(self, tmp_path):
        system = self._make_system(tmp_path)
        history = system.get_context_history("u1", days=7)
        assert history == []

    def test_predict_next_context_no_current(self, tmp_path):
        system = self._make_system(tmp_path)
        assert system.predict_next_context("u1") is None


# ──────────────────────────────────────────────────────────────────────────────
# 4. ContextInjector
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestContextInjector:
    """Tests for web.context_injector (QuestionClassifier, ContextSelector, ContextInjector)"""

    def test_question_classifier_empty(self):
        from web.context_injector import QuestionClassifier, TaskType

        c = QuestionClassifier()
        task, conf = c.classify("")
        assert task == TaskType.GENERAL
        assert conf == 0.0

    def test_question_classifier_code(self):
        from web.context_injector import QuestionClassifier, TaskType

        c = QuestionClassifier()
        task, conf = c.classify("运行 Python 脚本并安装 pip 包")
        assert task == TaskType.CODE_EXECUTION
        assert conf > 0

    def test_question_classifier_file(self):
        from web.context_injector import QuestionClassifier, TaskType

        c = QuestionClassifier()
        task, conf = c.classify("找到最大的文件并删除")
        assert task == TaskType.FILE_OPERATION

    def test_question_classifier_system_diagnosis(self):
        from web.context_injector import QuestionClassifier, TaskType

        c = QuestionClassifier()
        task, conf = c.classify("电脑很卡 CPU 很高 内存满了")
        assert task == TaskType.SYSTEM_DIAGNOSIS

    def test_context_selector_code(self):
        from web.context_injector import ContextSelector, ContextType, TaskType

        s = ContextSelector()
        ctxs = s.select_contexts(TaskType.CODE_EXECUTION)
        assert ContextType.PYTHON_ENV in ctxs

    def test_context_selector_general(self):
        from web.context_injector import ContextSelector, TaskType

        s = ContextSelector()
        ctxs = s.select_contexts(TaskType.GENERAL)
        assert len(ctxs) == 0

    def test_context_builder_time(self):
        from web.context_injector import ContextBuilder

        result = ContextBuilder.build_time_context()
        assert "当前时间" in result

    def test_context_injector_general(self):
        from web.context_injector import ContextInjector

        injector = ContextInjector()
        instruction = injector.get_injected_instruction()
        assert "Koto" in instruction

    def test_context_injector_with_question(self):
        from web.context_injector import ContextInjector

        injector = ContextInjector()
        instruction = injector.get_injected_instruction("你好，今天天气怎么样？")
        assert "Koto" in instruction

    def test_classify_question_helper(self):
        # Reset singleton
        import web.context_injector as ci
        from web.context_injector import TaskType, classify_question

        ci._context_injector = None
        task, conf = classify_question("运行脚本")
        assert task == TaskType.CODE_EXECUTION

    def test_get_dynamic_system_instruction(self):
        import web.context_injector as ci
        from web.context_injector import get_dynamic_system_instruction

        ci._context_injector = None
        result = get_dynamic_system_instruction()
        assert isinstance(result, str)


# ──────────────────────────────────────────────────────────────────────────────
# 5. ConceptExtractor
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestConceptExtractor:
    """Tests for web.concept_extractor.ConceptExtractor"""

    def _make_extractor(self, tmp_path):
        from web.concept_extractor import ConceptExtractor

        db_path = str(tmp_path / "concepts.db")
        return ConceptExtractor(db_path=db_path)

    def test_init_creates_db(self, tmp_path):
        ext = self._make_extractor(tmp_path)
        assert os.path.exists(ext.db_path)

    def test_tokenize_english(self, tmp_path):
        ext = self._make_extractor(tmp_path)
        tokens = ext.tokenize("Machine learning and deep learning are important")
        assert "machine" in tokens
        assert "learning" in tokens

    def test_tokenize_empty(self, tmp_path):
        ext = self._make_extractor(tmp_path)
        tokens = ext.tokenize("")
        assert tokens == []

    def test_calculate_tf(self, tmp_path):
        ext = self._make_extractor(tmp_path)
        words = ["apple", "banana", "apple"]
        tf = ext.calculate_tf(words)
        assert abs(tf["apple"] - 2 / 3) < 0.01
        assert abs(tf["banana"] - 1 / 3) < 0.01

    def test_calculate_tf_empty(self, tmp_path):
        ext = self._make_extractor(tmp_path)
        assert ext.calculate_tf([]) == {}

    def test_extract_concepts(self, tmp_path):
        ext = self._make_extractor(tmp_path)
        text = "Machine learning deep learning neural network artificial intelligence"
        concepts = ext.extract_concepts(text, top_n=5)
        assert len(concepts) > 0
        assert all(isinstance(c, tuple) and len(c) == 2 for c in concepts)

    def test_get_statistics(self, tmp_path):
        ext = self._make_extractor(tmp_path)
        stats = ext.get_statistics()
        assert "total_files_analyzed" in stats
        assert "total_unique_concepts" in stats
        assert stats["total_files_analyzed"] == 0

    def test_get_file_concepts_empty(self, tmp_path):
        ext = self._make_extractor(tmp_path)
        concepts = ext.get_file_concepts("nonexistent.txt")
        assert concepts == []

    def test_find_related_files_empty(self, tmp_path):
        ext = self._make_extractor(tmp_path)
        related = ext.find_related_files("nonexistent.txt")
        assert related == []


# ──────────────────────────────────────────────────────────────────────────────
# 6. ClipboardManager
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestClipboardManager:
    """Tests for web.clipboard_manager.ClipboardManager"""

    def _make_manager(self, tmp_path):
        # pyperclip is imported at module top-level; mock it in sys.modules
        mock_pyperclip = MagicMock()
        with patch.dict(sys.modules, {"pyperclip": mock_pyperclip}):
            # Force re-import so the module picks up the mock
            import importlib

            import web.clipboard_manager as cm_mod

            importlib.reload(cm_mod)
            history_file = str(tmp_path / "clipboard_history.json")
            mgr = cm_mod.ClipboardManager(history_file=history_file, max_items=10)
        return mgr

    def test_init(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert mgr.max_items == 10
        assert mgr.history == []

    def test_add_to_history(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr._add_to_history("hello world test content")
        assert len(mgr.history) == 1
        assert mgr.history[0]["content"] == "hello world test content"

    def test_add_to_history_ignores_short(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr._add_to_history("x")
        assert len(mgr.history) == 0

    def test_add_to_history_deduplicates(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr._add_to_history("hello world content")
        mgr._add_to_history("hello world content")
        assert len(mgr.history) == 1

    def test_search_history(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr._add_to_history("Python programming language")
        mgr._add_to_history("JavaScript runtime environment")
        results = mgr.search_history("Python")
        assert len(results) == 1

    def test_clear_history(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr._add_to_history("some content here")
        mgr.clear_history()
        assert mgr.history == []

    def test_get_recent(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        for i in range(5):
            mgr._add_to_history(f"clipboard item number {i}")
        recent = mgr.get_recent(3)
        assert len(recent) == 3

    def test_classify_content(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert mgr._classify_content("https://example.com/page") == "url"
        assert mgr._classify_content("user@example.com") == "email"
        assert mgr._classify_content("just plain text") == "text"
        assert mgr._classify_content('{"key": "val"}') == "json"

    def test_extract_entities(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        entities = mgr._extract_entities(
            "Contact me at test@example.com or visit https://site.com"
        )
        assert len(entities["emails"]) == 1
        assert len(entities["urls"]) == 1

    def test_max_items_enforced(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        for i in range(15):
            mgr._add_to_history(f"item number {i:04d} with enough length")
        assert len(mgr.history) <= mgr.max_items


# ──────────────────────────────────────────────────────────────────────────────
# 7. InsightReporter
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestInsightReporter:
    """Tests for web.insight_reporter.InsightReporter"""

    def _make_reporter(self, tmp_path):
        with patch.dict(
            sys.modules,
            {
                "behavior_monitor": MagicMock(),
                "knowledge_graph": MagicMock(),
                "suggestion_engine": MagicMock(),
            },
        ):
            from web.insight_reporter import InsightReporter

            bm = MagicMock()
            kg = MagicMock()
            se = MagicMock()
            db_path = str(tmp_path / "insights.db")
            reporter = InsightReporter(
                behavior_monitor=bm,
                knowledge_graph=kg,
                suggestion_engine=se,
                db_path=db_path,
            )
        return reporter, bm, kg, se

    def test_init_creates_db(self, tmp_path):
        reporter, _, _, _ = self._make_reporter(tmp_path)
        assert os.path.exists(reporter.db_path)

    def test_interpret_productivity_score(self, tmp_path):
        reporter, _, _, _ = self._make_reporter(tmp_path)
        assert "高效" in reporter._interpret_productivity_score(60)
        assert "良好" in reporter._interpret_productivity_score(35)
        assert "中等" in reporter._interpret_productivity_score(20)
        assert "较低" in reporter._interpret_productivity_score(5)

    def test_interpret_graph_density(self, tmp_path):
        reporter, _, _, _ = self._make_reporter(tmp_path)
        assert "很高" in reporter._interpret_graph_density(0.4)
        assert "一定" in reporter._interpret_graph_density(0.15)
        assert "中等" in reporter._interpret_graph_density(0.06)
        assert "分散" in reporter._interpret_graph_density(0.01)

    def test_interpret_trend(self, tmp_path):
        reporter, _, _, _ = self._make_reporter(tmp_path)
        assert "大幅提升" in reporter._interpret_trend(25)
        assert "稳步增长" in reporter._interpret_trend(10)
        assert "稳定" in reporter._interpret_trend(0)
        assert "有所下降" in reporter._interpret_trend(-10)
        assert "显著下降" in reporter._interpret_trend(-25)

    def test_interpret_ctr(self, tmp_path):
        reporter, _, _, _ = self._make_reporter(tmp_path)
        assert "优秀" in reporter._interpret_ctr(80)
        assert "良好" in reporter._interpret_ctr(55)
        assert "中等" in reporter._interpret_ctr(35)
        assert "较低" in reporter._interpret_ctr(10)

    def test_determine_work_style_empty(self, tmp_path):
        reporter, _, _, _ = self._make_reporter(tmp_path)
        with patch.dict(sys.modules, {"behavior_monitor": MagicMock()}):
            style = reporter._determine_work_style({"operation_types": []})
            assert "探索者" in style

    def test_get_latest_report_none(self, tmp_path):
        reporter, _, _, _ = self._make_reporter(tmp_path)
        result = reporter.get_latest_report("weekly")
        assert result is None

    def test_save_and_get_report(self, tmp_path):
        reporter, _, _, _ = self._make_reporter(tmp_path)
        report = {
            "type": "weekly",
            "period": {"start": "2024-01-01", "end": "2024-01-07", "days": 7},
            "summary_markdown": "# Test Report",
            "sections": {},
        }
        reporter._save_report(report)
        result = reporter.get_latest_report("weekly")
        assert result is not None


# ──────────────────────────────────────────────────────────────────────────────
# 8. PromptAdapter
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPromptAdapter:
    """Tests for web.prompt_adapter.PromptAdapter"""

    def test_adapt_short_input(self):
        from web.prompt_adapter import PromptAdapter

        result = PromptAdapter.adapt("hi", "CHAT")
        assert result == "hi"

    def test_adapt_chat_passthrough(self):
        from web.prompt_adapter import PromptAdapter

        text = "What is machine learning and how does it work in practice?"
        result = PromptAdapter.adapt(text, "CHAT")
        assert result == text

    def test_adapt_web_search_passthrough(self):
        from web.prompt_adapter import PromptAdapter

        text = "Search for the latest Python version and tell me about it"
        result = PromptAdapter.adapt(text, "WEB_SEARCH")
        assert result == text

    def test_adapt_already_markdown(self):
        from web.prompt_adapter import PromptAdapter

        text = "## My Task\n- Step one\n- Step two\n**Important**: something"
        result = PromptAdapter.adapt(text, "FILE_GEN")
        assert result == text

    def test_adapt_file_gen(self):
        from web.prompt_adapter import PromptAdapter

        text = "需要生成一份详细的项目报告，包含进度分析和预算审查。要求格式规范。"
        result = PromptAdapter.adapt(text, "FILE_GEN")
        assert "任务解析" in result
        assert "文档/文件生成" in result

    def test_adapt_with_model_generate(self):
        from web.prompt_adapter import PromptAdapter

        text = "需要生成一份详细的项目报告，包含进度分析和预算审查。要求格式规范。"
        gen = MagicMock(return_value="# Refined Markdown\n## 目标\n- 生成报告")
        result = PromptAdapter.adapt(text, "FILE_GEN", model_generate=gen)
        assert "Refined" in result
        gen.assert_called_once()

    def test_adapt_model_generate_fails(self):
        from web.prompt_adapter import PromptAdapter

        text = "需要生成一份详细的项目报告，包含进度分析和预算审查。要求格式规范。"
        gen = MagicMock(side_effect=Exception("LLM error"))
        result = PromptAdapter.adapt(text, "FILE_GEN", model_generate=gen)
        assert "任务解析" in result

    def test_extract_candidates(self):
        from web.prompt_adapter import PromptAdapter

        text = "需要生成一份报告。注意格式要求。"
        candidates = PromptAdapter._extract_candidates(text)
        assert len(candidates["目标"]) > 0

    def test_summarize_history_disabled(self):
        from web.prompt_adapter import PromptAdapter

        result = PromptAdapter._summarize_history(
            [{"role": "user", "parts": ["hello"]}]
        )
        assert result == ""


# ──────────────────────────────────────────────────────────────────────────────
# 9. MemoryIntegration
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMemoryIntegration:
    """Tests for web.memory_integration.MemoryIntegration"""

    def test_create_extraction_prompt(self):
        from web.memory_integration import MemoryIntegration

        prompt = MemoryIntegration.create_extraction_prompt("我喜欢 Python", "好的")
        assert "Python" in prompt
        assert "JSON" in prompt

    def test_should_extract_short_msg(self):
        from web.memory_integration import MemoryIntegration

        assert MemoryIntegration.should_extract("hi", "") is False

    def test_should_extract_greeting(self):
        from web.memory_integration import MemoryIntegration

        assert MemoryIntegration.should_extract("你好", "") is False
        assert MemoryIntegration.should_extract("hello", "") is False

    def test_should_extract_strong_signal(self):
        from web.memory_integration import MemoryIntegration

        assert MemoryIntegration.should_extract("我非常喜欢简洁的代码风格", "") is True
        assert (
            MemoryIntegration.should_extract("以后请记住不要再使用这种方式了", "")
            is True
        )

    def test_should_extract_tech_content(self):
        from web.memory_integration import MemoryIntegration

        assert MemoryIntegration.should_extract("帮我写一个Python爬虫", "") is True

    def test_should_extract_long_msg(self):
        from web.memory_integration import MemoryIntegration

        # len() > 40 triggers extraction for longer messages
        long_msg = "这是一段比较长的消息，包含了一些有价值的信息，足够触发记忆提取的阈值，需要有超过四十个字符才能通过检测"
        assert len(long_msg) > 40
        assert MemoryIntegration.should_extract(long_msg, "") is True

    def test_enhance_system_instruction(self):
        from web.memory_integration import MemoryIntegration

        result = MemoryIntegration.enhance_system_instruction(
            "You are an assistant.", "Memory: user likes Python", "Profile: developer"
        )
        assert "Profile: developer" in result
        assert "Memory: user likes Python" in result
        assert "回复调整建议" in result

    def test_enhance_system_instruction_empty(self):
        from web.memory_integration import MemoryIntegration

        result = MemoryIntegration.enhance_system_instruction(
            "Base instruction.", "", ""
        )
        assert "Base instruction." in result


# ──────────────────────────────────────────────────────────────────────────────
# 10. DocumentPlanner / doc_planner
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDocPlanner:
    """Tests for web.doc_planner"""

    def test_section_plan_defaults(self):
        from web.doc_planner import SectionPlan

        s = SectionPlan()
        assert s.heading == ""
        assert s.section_type == "text"
        assert s.rough_length == "medium"

    def test_document_plan_to_context_str(self):
        from web.doc_planner import DocumentPlan, SectionPlan

        plan = DocumentPlan(
            title="Test Plan",
            doc_type="word",
            sections=[
                SectionPlan(heading="Intro", purpose="Introduce", key_points=["a", "b"])
            ],
            table_schema=["col1", "col2"],
            visual_hints=["chart A"],
        )
        ctx = plan.to_context_str()
        assert "Test Plan" in ctx
        assert "Intro" in ctx
        assert "col1" in ctx

    def test_document_planner_fallback(self):
        from web.doc_planner import DocumentPlanner

        client = MagicMock()
        client.models.generate_content.side_effect = Exception("API down")
        planner = DocumentPlanner(ai_client=client)
        plan = planner.plan_sync("生成一份PPT报告")
        assert plan.success is False
        assert plan.doc_type == "ppt"

    def test_detect_doc_type(self):
        from web.doc_planner import DocumentPlanner

        planner = DocumentPlanner(ai_client=MagicMock())
        assert planner._detect_doc_type("做一个PPT") == "ppt"
        assert planner._detect_doc_type("生成Excel表格") == "excel"
        assert planner._detect_doc_type("创建pdf文件") == "pdf"
        assert planner._detect_doc_type("写一份报告") == "word"

    def test_extract_json_from_code_block(self):
        from web.doc_planner import DocumentPlanner

        planner = DocumentPlanner(ai_client=MagicMock())
        text = '```json\n{"doc_type": "word"}\n```'
        result = planner._extract_json(text)
        assert result is not None
        assert "word" in result

    def test_extract_json_bare(self):
        from web.doc_planner import DocumentPlanner

        planner = DocumentPlanner(ai_client=MagicMock())
        text = 'Here is the plan: {"doc_type": "excel"}'
        result = planner._extract_json(text)
        assert result is not None

    def test_parse_plan_success(self):
        from web.doc_planner import DocumentPlanner

        planner = DocumentPlanner(ai_client=MagicMock())
        raw = json.dumps(
            {
                "doc_type": "word",
                "title": "Test",
                "target_audience": "All",
                "tone": "正式",
                "sections": [
                    {
                        "heading": "Intro",
                        "section_type": "text",
                        "purpose": "Introduce",
                        "key_points": ["a"],
                        "rough_length": "short",
                    }
                ],
                "table_schema": [],
                "visual_hints": [],
                "generation_notes": "None",
            }
        )
        plan = planner._parse_plan(raw, "写一份报告")
        assert plan.success is True
        assert plan.title == "Test"
        assert len(plan.sections) == 1

    def test_build_generation_prompt_from_plan(self):
        from web.doc_planner import (
            DocumentPlan,
            SectionPlan,
            build_generation_prompt_from_plan,
        )

        plan = DocumentPlan(
            doc_type="word",
            title="Demo",
            sections=[
                SectionPlan(
                    heading="Overview",
                    section_type="text",
                    purpose="intro",
                    key_points=["point1"],
                )
            ],
        )
        prompt = build_generation_prompt_from_plan(plan, "Create a document")
        assert "Overview" in prompt
        assert "Create a document" in prompt


# ──────────────────────────────────────────────────────────────────────────────
# 11. doc_converter
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDocConverter:
    """Tests for web.doc_converter"""

    def test_needs_conversion(self):
        from web.doc_converter import needs_conversion

        assert needs_conversion(".pdf") is True
        assert needs_conversion(".doc") is True
        assert needs_conversion(".txt") is True
        assert needs_conversion(".md") is True
        assert needs_conversion(".rtf") is True
        assert needs_conversion(".docx") is False

    def test_convert_to_docx_already_docx(self, tmp_path):
        from web.doc_converter import convert_to_docx

        docx_file = tmp_path / "test.docx"
        docx_file.write_text("dummy", encoding="utf-8")
        path, warning = convert_to_docx(str(docx_file))
        assert warning == ""
        assert path == str(docx_file)

    def test_convert_to_docx_unsupported_format(self, tmp_path):
        from web.doc_converter import convert_to_docx

        file = tmp_path / "test.xyz"
        file.write_text("data", encoding="utf-8")
        with pytest.raises(ValueError, match="不支持的格式"):
            convert_to_docx(str(file))

    @patch("web.doc_converter._build_docx_from_text")
    def test_convert_txt(self, mock_build, tmp_path):
        from web.doc_converter import convert_to_docx

        txt_file = tmp_path / "test.txt"
        txt_file.write_text("Hello world content", encoding="utf-8")
        mock_build.return_value = str(tmp_path / "_converted" / "test_converted.docx")
        path, warning = convert_to_docx(str(txt_file), str(tmp_path / "_converted"))
        mock_build.assert_called_once()
        assert warning == ""

    @patch("web.doc_converter._build_docx_from_text")
    def test_convert_md(self, mock_build, tmp_path):
        from web.doc_converter import convert_to_docx

        md_file = tmp_path / "readme.md"
        md_file.write_text("# Title\nContent", encoding="utf-8")
        mock_build.return_value = str(tmp_path / "_converted" / "readme_converted.docx")
        path, warning = convert_to_docx(str(md_file), str(tmp_path / "_converted"))
        mock_build.assert_called_once()

    def test_supported_input_exts(self):
        from web.doc_converter import SUPPORTED_INPUT_EXTS

        assert ".pdf" in SUPPORTED_INPUT_EXTS
        assert ".docx" in SUPPORTED_INPUT_EXTS
        assert ".odt" in SUPPORTED_INPUT_EXTS

    def test_accept_attr(self):
        from web.doc_converter import ACCEPT_ATTR

        assert ".docx" in ACCEPT_ATTR
        assert ".pdf" in ACCEPT_ATTR


# ──────────────────────────────────────────────────────────────────────────────
# 12. DataPipeline
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDataPipeline:
    """Tests for web.data_pipeline"""

    def test_wechat_extractor_extract_from_text(self):
        from web.data_pipeline import WeChatContactExtractor

        ext = WeChatContactExtractor()
        result = ext.extract_from_text("联系我 13812345678 邮箱 test@example.com")
        assert "13812345678" in result["phones"]
        assert "test@example.com" in result["emails"]

    def test_wechat_extractor_no_matches(self):
        from web.data_pipeline import WeChatContactExtractor

        ext = WeChatContactExtractor()
        result = ext.extract_from_text("No contact info here")
        assert result["phones"] == []
        assert result["emails"] == []

    def test_wechat_extractor_from_chat(self):
        from web.data_pipeline import WeChatContactExtractor

        ext = WeChatContactExtractor()
        msgs = ["电话 13812345678", "邮箱 a@b.com", "普通消息"]
        contacts = ext.extract_from_wechat_chat(msgs)
        assert len(contacts) == 2

    def test_data_transformer_to_json(self, tmp_path):
        from web.data_pipeline import DataTransformer

        output = str(tmp_path / "data.json")
        data = [{"name": "Alice", "age": 30}]
        result = DataTransformer.to_json(data, output)
        assert os.path.exists(result)
        with open(result, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded[0]["name"] == "Alice"

    def test_data_transformer_to_csv(self, tmp_path):
        from web.data_pipeline import DataTransformer

        output = str(tmp_path / "data.csv")
        data = [{"name": "Bob", "age": 25}]
        result = DataTransformer.to_csv(data, output)
        assert os.path.exists(result)

    def test_data_transformer_to_csv_empty(self, tmp_path):
        from web.data_pipeline import DataTransformer

        output = str(tmp_path / "empty.csv")
        result = DataTransformer.to_csv([], output)
        assert os.path.exists(result)

    def test_pipeline_wechat_to_json(self, tmp_path):
        from web.data_pipeline import CrossAppDataPipeline

        pipeline = CrossAppDataPipeline()
        output = str(tmp_path / "contacts.json")
        result = pipeline.run_pipeline(
            source_type="wechat_contact",
            source_data="张三 13812345678 zhangsan@test.com",
            target_format="json",
            output_path=output,
        )
        assert result["success"] is True
        assert result["record_count"] == 1

    def test_pipeline_unsupported_source(self, tmp_path):
        from web.data_pipeline import CrossAppDataPipeline

        pipeline = CrossAppDataPipeline()
        result = pipeline.run_pipeline(
            source_type="unknown",
            source_data="data",
            target_format="json",
            output_path=str(tmp_path / "out.json"),
        )
        assert result["success"] is False

    def test_pipeline_unsupported_format(self, tmp_path):
        from web.data_pipeline import CrossAppDataPipeline

        pipeline = CrossAppDataPipeline()
        result = pipeline.run_pipeline(
            source_type="wechat_contact",
            source_data="13812345678",
            target_format="xml",
            output_path=str(tmp_path / "out.xml"),
        )
        assert result["success"] is False

    def test_pipeline_wechat_chat_list_to_csv(self, tmp_path):
        from web.data_pipeline import CrossAppDataPipeline

        pipeline = CrossAppDataPipeline()
        output = str(tmp_path / "contacts.csv")
        msgs = ["电话 13812345678", "邮箱 a@b.com"]
        result = pipeline.run_pipeline(
            source_type="wechat_contact",
            source_data=msgs,
            target_format="csv",
            output_path=output,
        )
        assert result["success"] is True

    def test_pipeline_unsupported_data_format(self, tmp_path):
        from web.data_pipeline import CrossAppDataPipeline

        pipeline = CrossAppDataPipeline()
        result = pipeline.run_pipeline(
            source_type="wechat_contact",
            source_data=12345,
            target_format="json",
            output_path=str(tmp_path / "out.json"),
        )
        assert result["success"] is False
