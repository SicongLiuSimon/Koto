"""
Unit tests for web modules batch 7:
  PPTQualityChecker, PPTResourceManager, PPTContentPlanner, PPTLayoutPlanner,
  PPTImageMatcher, PPTMasterOrchestrator, PPTGenerationPipeline, PPTGenerationTaskHandler,
  task_scheduler (Task, TaskScheduler), task_dispatcher (TaskExecutor, TaskScheduler as DispatcherScheduler),
  WorkflowManager, Workflow, WorkflowExecutor, ReminderManager, SmartFeedback,
  SuggestionEngine, SuggestionAnnotator, TemplateLibrary.

Covers constructors, key public methods, and error handling with mocked I/O.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, PropertyMock, mock_open, patch

import pytest


def run_async(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════════════════
# PPT Quality Checker
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestPPTQualityChecker:
    """Tests for web.ppt_quality.PPTQualityChecker."""

    def _make_checker(self):
        from web.ppt_quality import PPTQualityChecker

        return PPTQualityChecker()

    def test_init(self):
        checker = self._make_checker()
        assert checker is not None

    def test_evaluate_file_not_found(self):
        checker = self._make_checker()
        result = checker.evaluate("/nonexistent/path.pptx")
        assert result["success"] is False
        assert result["score"] == 0
        assert "not found" in result["error"].lower()

    @patch("web.ppt_quality.os.path.exists", return_value=True)
    def test_evaluate_missing_pptx_lib(self, mock_exists):
        checker = self._make_checker()
        with patch.dict("sys.modules", {"pptx": None}):
            # Force import error by patching builtins
            original_import = (
                __builtins__.__import__
                if hasattr(__builtins__, "__import__")
                else __import__
            )

            def mock_import(name, *args, **kwargs):
                if name == "pptx" or (isinstance(name, str) and "pptx" in name):
                    raise ImportError("no pptx")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                result = checker.evaluate("fake.pptx")
                assert result["success"] is False
                assert "python-pptx" in result["error"]

    def test_score_perfect(self):
        checker = self._make_checker()
        metrics = {
            "slide_count": 8,
            "image_slides": 2,
            "missing_title_slides": 0,
            "avg_bullets_per_slide": 4,
            "max_bullet_length": 80,
            "max_text_chars_per_slide": 400,
        }
        score, issues, suggestions = checker._score(metrics)
        assert score == 100
        assert len(issues) == 0
        assert "Quality looks good" in suggestions[0]

    def test_score_too_few_slides(self):
        checker = self._make_checker()
        metrics = {
            "slide_count": 3,
            "image_slides": 0,
            "missing_title_slides": 0,
            "avg_bullets_per_slide": 4,
            "max_bullet_length": 80,
            "max_text_chars_per_slide": 400,
        }
        score, issues, suggestions = checker._score(metrics)
        assert score < 100
        assert "Too few slides" in issues

    def test_score_too_many_slides(self):
        checker = self._make_checker()
        metrics = {
            "slide_count": 15,
            "image_slides": 5,
            "missing_title_slides": 0,
            "avg_bullets_per_slide": 4,
            "max_bullet_length": 80,
            "max_text_chars_per_slide": 400,
        }
        score, issues, suggestions = checker._score(metrics)
        assert score < 100
        assert "Too many slides" in issues

    def test_score_missing_titles(self):
        checker = self._make_checker()
        metrics = {
            "slide_count": 8,
            "image_slides": 2,
            "missing_title_slides": 3,
            "avg_bullets_per_slide": 4,
            "max_bullet_length": 80,
            "max_text_chars_per_slide": 400,
        }
        score, issues, _ = checker._score(metrics)
        assert score < 100
        assert "missing titles" in issues[0].lower()

    def test_score_floor_at_zero(self):
        checker = self._make_checker()
        metrics = {
            "slide_count": 3,
            "image_slides": 0,
            "missing_title_slides": 10,
            "avg_bullets_per_slide": 0,
            "max_bullet_length": 200,
            "max_text_chars_per_slide": 1000,
        }
        score, _, _ = checker._score(metrics)
        assert score >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# PPT Master — dataclasses and enums
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestPPTMasterDataclasses:
    """Tests for SlideBlueprint, PPTBlueprint, and enums."""

    def test_slide_type_enum(self):
        from web.ppt_master import SlideType

        assert SlideType.TITLE.value == "title"
        assert SlideType.CONTENT_IMAGE.value == "content_image"

    def test_content_density_enum(self):
        from web.ppt_master import ContentDensity

        assert ContentDensity.LIGHT.value == "light"
        assert ContentDensity.DENSE.value == "dense"

    def test_slide_blueprint_to_dict(self):
        from web.ppt_master import ContentDensity, SlideBlueprint, SlideType

        sb = SlideBlueprint(
            slide_index=0,
            slide_type=SlideType.TITLE,
            title="Test",
            content=["A", "B"],
        )
        d = sb.to_dict()
        assert d["slide_index"] == 0
        assert d["slide_type"] == "title"
        assert d["title"] == "Test"
        assert d["content"] == ["A", "B"]
        assert d["density"] == "medium"

    def test_ppt_blueprint_to_dict(self):
        from web.ppt_master import PPTBlueprint

        bp = PPTBlueprint(title="My PPT", subtitle="sub")
        d = bp.to_dict()
        assert d["title"] == "My PPT"
        assert d["subtitle"] == "sub"
        assert d["slides"] == []

    def test_ppt_blueprint_add_log(self):
        from web.ppt_master import PPTBlueprint

        bp = PPTBlueprint(title="T", subtitle="S")
        bp.add_log("test message")
        assert len(bp.generation_log) == 1
        assert "test message" in bp.generation_log[0]


# ═══════════════════════════════════════════════════════════════════════════════
# PPT Master — ResourceManager
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestPPTResourceManager:
    """Tests for web.ppt_master.PPTResourceManager."""

    def _make_rm(self):
        from web.ppt_master import PPTResourceManager

        return PPTResourceManager()

    def test_init(self):
        rm = self._make_rm()
        assert rm.search_results == {}
        assert rm.images == {}

    def test_add_search_results(self):
        rm = self._make_rm()
        results = [{"title": "T1", "url": "http://x", "content": "abc"}]
        rm.add_search_results("ai", results)
        assert "ai" in rm.search_results
        assert len(rm.references) == 1

    def test_add_images(self):
        rm = self._make_rm()
        rm.add_images("tech", ["/img1.png", "/img2.png"])
        assert rm.images["tech"] == ["/img1.png", "/img2.png"]
        assert len(rm.generated_images) == 2

    def test_get_best_images(self):
        rm = self._make_rm()
        rm.add_images("tech", ["/a.png", "/b.png", "/c.png"])
        assert len(rm.get_best_images("tech", 2)) == 2

    def test_get_best_images_missing_keyword(self):
        rm = self._make_rm()
        assert rm.get_best_images("nonexistent") == []

    def test_get_summary_for_blueprint(self):
        rm = self._make_rm()
        rm.add_search_results("k", [{"title": "", "url": "", "content": "x"}])
        rm.add_images("k", ["/img.png"])
        summary = rm.get_summary_for_blueprint()
        assert summary["search_keywords_count"] == 1
        assert summary["generated_images_count"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# PPT Master — LayoutPlanner
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestPPTLayoutPlanner:
    """Tests for web.ppt_master.PPTLayoutPlanner."""

    def _make_planner(self):
        from web.ppt_master import PPTLayoutPlanner

        return PPTLayoutPlanner()

    def test_init(self):
        planner = self._make_planner()
        assert "title_heavy" in planner.layout_rules
        assert "balanced" in planner.layout_rules

    def test_plan_layout_title(self):
        from web.ppt_master import PPTLayoutPlanner, SlideBlueprint, SlideType

        planner = PPTLayoutPlanner()
        sb = SlideBlueprint(slide_index=0, slide_type=SlideType.TITLE, title="T")
        config = planner.plan_layout(sb)
        assert config["title_size"] == 54

    def test_plan_layout_comparison(self):
        from web.ppt_master import PPTLayoutPlanner, SlideBlueprint, SlideType

        planner = PPTLayoutPlanner()
        sb = SlideBlueprint(slide_index=1, slide_type=SlideType.COMPARISON, title="C")
        config = planner.plan_layout(sb)
        assert "left_width" in config

    def test_plan_layout_dense_content(self):
        from web.ppt_master import (
            ContentDensity,
            PPTLayoutPlanner,
            SlideBlueprint,
            SlideType,
        )

        planner = PPTLayoutPlanner()
        sb = SlideBlueprint(
            slide_index=2,
            slide_type=SlideType.CONTENT,
            title="D",
            density=ContentDensity.DENSE,
        )
        config = planner.plan_layout(sb, content_count=6)
        assert config.get("line_spacing") == 1.3

    def test_optimize_slide_count(self):
        planner = self._make_planner()
        assert planner.optimize_slide_count(3) == 5
        assert planner.optimize_slide_count(20) == 15
        assert planner.optimize_slide_count(10) == 10

    def test_choose_bullet_style(self):
        from web.ppt_master import PPTLayoutPlanner, SlideType

        planner = PPTLayoutPlanner()
        assert planner._choose_bullet_style(SlideType.TITLE) == "none"
        assert planner._choose_bullet_style(SlideType.CONTENT) == "circle"
        assert planner._choose_bullet_style(SlideType.SUMMARY) == "checkmark"


# ═══════════════════════════════════════════════════════════════════════════════
# PPT Master — ContentPlanner (default plan)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestPPTContentPlanner:
    """Tests for web.ppt_master.PPTContentPlanner default plan generation."""

    def _make_planner(self):
        from web.ppt_master import PPTContentPlanner

        return PPTContentPlanner(ai_client=None)

    def test_init_no_client(self):
        planner = self._make_planner()
        assert planner.ai_client is None
        assert planner.model_name == "gemini-2.5-flash"

    def test_generate_default_plan(self):
        planner = self._make_planner()
        plan = planner._generate_default_plan("关于AI的演示")
        assert "outline" in plan
        assert len(plan["outline"]) > 0
        assert plan["theme_recommendation"] == "business"

    def test_generate_default_plan_has_sections(self):
        planner = self._make_planner()
        plan = planner._generate_default_plan("关于量子计算的报告")
        sections = [s["section_title"] for s in plan["outline"]]
        assert "概述" in sections
        assert "总结" in sections

    def test_plan_content_structure_no_client(self):
        planner = self._make_planner()
        result = run_async(planner.plan_content_structure("关于Python的PPT"))
        assert "outline" in result

    def test_expand_slide_content_no_client(self):
        planner = self._make_planner()
        result = run_async(planner.expand_slide_content("Test", ["a", "b"]))
        assert result == ["a", "b"]


# ═══════════════════════════════════════════════════════════════════════════════
# PPT Master — ImageMatcher
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestPPTImageMatcher:
    """Tests for web.ppt_master.PPTImageMatcher."""

    def test_init(self):
        from web.ppt_master import PPTImageMatcher

        m = PPTImageMatcher(ai_client=None)
        assert m.image_cache == {}

    def test_generate_prompts_for_content_image(self):
        from web.ppt_master import PPTImageMatcher, SlideBlueprint, SlideType

        m = PPTImageMatcher()
        prompts = m._generate_image_prompts_for_slide(
            SlideBlueprint(
                slide_index=0, slide_type=SlideType.CONTENT_IMAGE, title="AI Tech"
            ),
            "modern",
        )
        assert len(prompts) == 1
        assert "AI Tech" in prompts[0]

    def test_generate_prompts_for_comparison(self):
        from web.ppt_master import PPTImageMatcher, SlideBlueprint, SlideType

        m = PPTImageMatcher()
        prompts = m._generate_image_prompts_for_slide(
            SlideBlueprint(
                slide_index=0, slide_type=SlideType.COMPARISON, title="A vs B"
            ),
            "flat",
        )
        assert len(prompts) == 2

    def test_generate_prompts_for_content_returns_empty(self):
        from web.ppt_master import PPTImageMatcher, SlideBlueprint, SlideType

        m = PPTImageMatcher()
        prompts = m._generate_image_prompts_for_slide(
            SlideBlueprint(
                slide_index=0, slide_type=SlideType.CONTENT, title="Text only"
            ),
            "corporate",
        )
        assert prompts == []

    def test_generate_image_prompts_updates_slides(self):
        from web.ppt_master import PPTImageMatcher, SlideBlueprint, SlideType

        m = PPTImageMatcher()
        slides = [
            SlideBlueprint(
                slide_index=0, slide_type=SlideType.CONTENT_IMAGE, title="Slide1"
            ),
            SlideBlueprint(slide_index=1, slide_type=SlideType.CONTENT, title="Slide2"),
        ]
        result = run_async(m.generate_image_prompts(slides, "pro"))
        assert len(result[0].image_prompts) > 0
        assert len(result[1].image_prompts) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# PPT Pipeline
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestPPTGenerationPipeline:
    """Tests for web.ppt_pipeline.PPTGenerationPipeline."""

    @patch("web.ppt_pipeline.PPTSynthesizer")
    @patch("web.ppt_pipeline.PPTMasterOrchestrator")
    def test_init(self, mock_orch, mock_synth):
        from web.ppt_pipeline import PPTGenerationPipeline

        pipeline = PPTGenerationPipeline(ai_client=None, workspace_dir="/tmp")
        assert pipeline.workspace_dir == "/tmp"
        assert pipeline.log == []

    @patch("web.ppt_pipeline.PPTSynthesizer")
    @patch("web.ppt_pipeline.PPTMasterOrchestrator")
    def test_log(self, mock_orch, mock_synth):
        from web.ppt_pipeline import PPTGenerationPipeline

        pipeline = PPTGenerationPipeline()
        pipeline._log("test")
        assert "test" in pipeline.log

    @patch("web.ppt_pipeline.PPTSynthesizer")
    @patch("web.ppt_pipeline.PPTMasterOrchestrator")
    def test_get_logs(self, mock_orch, mock_synth):
        from web.ppt_pipeline import PPTGenerationPipeline

        pipeline = PPTGenerationPipeline()
        pipeline._log("a")
        pipeline._log("b")
        assert len(pipeline.get_logs()) == 2

    @patch("web.ppt_pipeline.PPTSynthesizer")
    @patch("web.ppt_pipeline.PPTMasterOrchestrator")
    def test_prepare_image_map_empty(self, mock_orch, mock_synth):
        from web.ppt_master import PPTBlueprint, SlideBlueprint, SlideType
        from web.ppt_pipeline import PPTGenerationPipeline

        pipeline = PPTGenerationPipeline()
        bp = PPTBlueprint(title="T", subtitle="S")
        bp.slides = [
            SlideBlueprint(slide_index=0, slide_type=SlideType.CONTENT, title="X")
        ]
        result = pipeline._prepare_image_map(bp)
        assert result == {}

    @patch("web.ppt_pipeline.PPTSynthesizer")
    @patch("web.ppt_pipeline.PPTMasterOrchestrator")
    def test_prepare_image_map_with_images(self, mock_orch, mock_synth):
        from web.ppt_master import PPTBlueprint, SlideBlueprint, SlideType
        from web.ppt_pipeline import PPTGenerationPipeline

        pipeline = PPTGenerationPipeline()
        bp = PPTBlueprint(title="T", subtitle="S")
        s = SlideBlueprint(slide_index=0, slide_type=SlideType.CONTENT_IMAGE, title="X")
        s.image_paths = ["/img.png"]
        bp.slides = [s]
        result = pipeline._prepare_image_map(bp)
        assert 0 in result

    @patch("web.ppt_pipeline.PPTSynthesizer")
    @patch("web.ppt_pipeline.PPTMasterOrchestrator")
    def test_finalize_result(self, mock_orch, mock_synth):
        from web.ppt_master import PPTBlueprint, SlideBlueprint, SlideType
        from web.ppt_pipeline import PPTGenerationPipeline

        pipeline = PPTGenerationPipeline()
        bp = PPTBlueprint(title="T", subtitle="S")
        bp.slides = [
            SlideBlueprint(
                slide_index=0, slide_type=SlideType.TITLE, title="T", content=["a"]
            )
        ]
        # Mock the orchestrator's resource_manager
        pipeline.orchestrator = MagicMock()
        pipeline.orchestrator.resource_manager.get_summary_for_blueprint.return_value = (
            {}
        )
        result = pipeline._finalize_result(
            {"file_size": 100, "slide_count": 1},
            bp,
            {"quality_score": 85, "checks": {}, "recommendations": []},
            "/out.pptx",
        )
        assert result["success"] is True
        assert result["slide_count"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Task Scheduler (task_scheduler.py)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestTaskSchedulerModule:
    """Tests for web.task_scheduler (Task, TaskScheduler)."""

    def _make_task(self, action=None, **kwargs):
        from web.task_scheduler import Task, TaskPriority

        if action is None:
            action = MagicMock(return_value="done")
        return Task(task_id="t1", name="TestTask", action=action, **kwargs)

    @patch("web.task_scheduler.os.makedirs")
    @patch("web.task_scheduler.os.path.exists", return_value=False)
    def test_task_init(self, mock_exists, mock_makedirs):
        task = self._make_task()
        assert task.task_id == "t1"
        assert task.name == "TestTask"
        from web.task_scheduler import TaskStatus

        assert task.status == TaskStatus.PENDING

    @patch("web.task_scheduler.os.makedirs")
    @patch("web.task_scheduler.os.path.exists", return_value=False)
    def test_task_execute_success(self, mock_exists, mock_makedirs):
        action = MagicMock(return_value="result")
        task = self._make_task(action=action)
        result = task.execute()
        assert result is True
        from web.task_scheduler import TaskStatus

        assert task.status == TaskStatus.COMPLETED
        assert task.result == "result"

    @patch("web.task_scheduler.os.makedirs")
    @patch("web.task_scheduler.os.path.exists", return_value=False)
    def test_task_execute_failure(self, mock_exists, mock_makedirs):
        action = MagicMock(side_effect=RuntimeError("boom"))
        task = self._make_task(action=action)
        result = task.execute()
        assert result is False
        from web.task_scheduler import TaskStatus

        assert task.status == TaskStatus.FAILED
        assert "boom" in task.error

    @patch("web.task_scheduler.os.makedirs")
    @patch("web.task_scheduler.os.path.exists", return_value=False)
    def test_task_cancel(self, mock_exists, mock_makedirs):
        task = self._make_task()
        task.mark_cancelled()
        assert task.is_cancelled() is True
        from web.task_scheduler import TaskStatus

        assert task.status == TaskStatus.CANCELLED

    @patch("web.task_scheduler.os.makedirs")
    @patch("web.task_scheduler.os.path.exists", return_value=False)
    def test_task_to_dict(self, mock_exists, mock_makedirs):
        task = self._make_task()
        d = task.to_dict()
        assert d["task_id"] == "t1"
        assert d["name"] == "TestTask"
        assert d["priority"] == "NORMAL"

    @patch("web.task_scheduler.os.makedirs")
    @patch("web.task_scheduler.os.path.exists", return_value=False)
    def test_scheduler_init(self, mock_exists, mock_makedirs):
        from web.task_scheduler import TaskScheduler

        sched = TaskScheduler()
        assert sched.running is False
        assert sched.tasks == {}

    @patch("web.task_scheduler.json.dump")
    @patch("builtins.open", mock_open())
    @patch("web.task_scheduler.os.makedirs")
    @patch("web.task_scheduler.os.path.exists", return_value=False)
    def test_scheduler_add_task(self, mock_exists, mock_makedirs, mock_dump):
        from web.task_scheduler import Task, TaskPriority, TaskScheduler

        sched = TaskScheduler()
        action = MagicMock()
        task = Task(task_id="t1", name="T1", action=action, priority=TaskPriority.HIGH)
        tid = sched.add_task(task)
        assert tid == "t1"
        assert "t1" in sched.tasks
        assert len(sched.task_queue) == 1

    @patch("web.task_scheduler.json.dump")
    @patch("builtins.open", mock_open())
    @patch("web.task_scheduler.os.makedirs")
    @patch("web.task_scheduler.os.path.exists", return_value=False)
    def test_scheduler_cancel_task(self, mock_exists, mock_makedirs, mock_dump):
        from web.task_scheduler import Task, TaskScheduler

        sched = TaskScheduler()
        task = Task(task_id="t1", name="T1", action=MagicMock())
        sched.add_task(task)
        assert sched.cancel_task("t1") is True
        assert sched.cancel_task("nonexistent") is False
        assert len(sched.task_queue) == 0

    @patch("web.task_scheduler.json.dump")
    @patch("builtins.open", mock_open())
    @patch("web.task_scheduler.os.makedirs")
    @patch("web.task_scheduler.os.path.exists", return_value=False)
    def test_scheduler_get_task(self, mock_exists, mock_makedirs, mock_dump):
        from web.task_scheduler import Task, TaskScheduler

        sched = TaskScheduler()
        task = Task(task_id="t1", name="T1", action=MagicMock())
        sched.add_task(task)
        assert sched.get_task("t1") is task
        assert sched.get_task("missing") is None

    @patch("web.task_scheduler.json.dump")
    @patch("builtins.open", mock_open())
    @patch("web.task_scheduler.os.makedirs")
    @patch("web.task_scheduler.os.path.exists", return_value=False)
    def test_scheduler_list_tasks(self, mock_exists, mock_makedirs, mock_dump):
        from web.task_scheduler import Task, TaskScheduler, TaskStatus

        sched = TaskScheduler()
        t1 = Task(task_id="t1", name="T1", action=MagicMock())
        t2 = Task(task_id="t2", name="T2", action=MagicMock())
        sched.add_task(t1)
        sched.add_task(t2)
        all_tasks = sched.list_tasks()
        assert len(all_tasks) == 2
        pending = sched.list_tasks(status=TaskStatus.PENDING)
        assert len(pending) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Task Dispatcher (task_dispatcher.py)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestTaskDispatcher:
    """Tests for web.task_dispatcher (TaskExecutor, TaskScheduler)."""

    @patch("web.task_dispatcher.get_task_monitor")
    @patch("web.task_dispatcher.get_resource_manager")
    @patch("web.task_dispatcher.get_queue_manager")
    def test_dispatcher_scheduler_init(self, mock_qm, mock_rm, mock_mon):
        from web.task_dispatcher import TaskScheduler as DispatcherScheduler

        sched = DispatcherScheduler(max_workers=3)
        assert sched.max_workers == 3
        assert sched.running is False

    @patch("web.task_dispatcher.get_task_monitor")
    @patch("web.task_dispatcher.get_resource_manager")
    @patch("web.task_dispatcher.get_queue_manager")
    def test_dispatcher_scheduler_register_executor(self, mock_qm, mock_rm, mock_mon):
        from web.parallel_executor import TaskType
        from web.task_dispatcher import TaskScheduler as DispatcherScheduler

        sched = DispatcherScheduler()
        fn = MagicMock()
        sched.register_executor(TaskType.CHAT, fn)
        assert TaskType.CHAT in sched.executors

    @patch("web.task_dispatcher.get_task_monitor")
    @patch("web.task_dispatcher.get_resource_manager")
    @patch("web.task_dispatcher.get_queue_manager")
    def test_dispatcher_scheduler_get_stats(self, mock_qm, mock_rm, mock_mon):
        from web.task_dispatcher import TaskScheduler as DispatcherScheduler

        sched = DispatcherScheduler(max_workers=4)
        stats = sched.get_stats()
        assert stats["running"] is False
        assert stats["max_workers"] == 4

    @patch("web.task_dispatcher.get_task_monitor")
    @patch("web.task_dispatcher.get_resource_manager")
    @patch("web.task_dispatcher.get_queue_manager")
    def test_dispatcher_scheduler_start_stop(self, mock_qm, mock_rm, mock_mon):
        from web.task_dispatcher import TaskScheduler as DispatcherScheduler

        mock_qm.return_value.get_next.return_value = None
        sched = DispatcherScheduler()
        sched.start()
        assert sched.running is True
        sched.stop()
        assert sched.running is False

    @patch("web.task_dispatcher.get_task_monitor")
    @patch("web.task_dispatcher.get_resource_manager")
    @patch("web.task_dispatcher.get_queue_manager")
    def test_task_executor_circuit_breaker_open(self, mock_qm, mock_rm, mock_mon):
        from web.parallel_executor import Priority, Task, TaskType
        from web.task_dispatcher import TaskExecutor

        task = Task(
            id="test-1",
            session_id="s1",
            type=TaskType.CHAT,
            priority=Priority.NORMAL,
        )
        execute_fn = MagicMock()
        executor = TaskExecutor(task, execute_fn)
        executor.circuit_breaker.can_execute = MagicMock(return_value=False)
        result = executor.execute()
        assert result is False
        assert "Circuit breaker" in task.error


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow Manager
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestWorkflowManager:
    """Tests for web.workflow_manager (Workflow, WorkflowManager, WorkflowExecutor)."""

    def test_workflow_init(self):
        from web.workflow_manager import Workflow

        wf = Workflow("Test Flow", "A test workflow")
        assert wf.name == "Test Flow"
        assert wf.id == "Test_Flow"
        assert wf.steps == []
        assert wf.execution_count == 0

    def test_workflow_add_step(self):
        from web.workflow_manager import Workflow

        wf = Workflow("WF")
        step = wf.add_step("step1", "agent", {"request": "do something"})
        assert step["name"] == "step1"
        assert step["type"] == "agent"
        assert len(wf.steps) == 1

    def test_workflow_set_variable(self):
        from web.workflow_manager import Workflow

        wf = Workflow("WF")
        wf.set_variable("topic", default_value="AI", description="The topic")
        assert "topic" in wf.variables
        assert wf.variables["topic"]["default"] == "AI"
        assert wf.variables["topic"]["required"] is False

    def test_workflow_to_dict_from_dict(self):
        from web.workflow_manager import Workflow

        wf = Workflow("WF", "desc")
        wf.add_step("s1", "tool")
        wf.tags = ["test"]
        d = wf.to_dict()
        wf2 = Workflow.from_dict(d)
        assert wf2.name == "WF"
        assert len(wf2.steps) == 1
        assert wf2.tags == ["test"]

    @patch("web.workflow_manager.os.makedirs")
    @patch("web.workflow_manager.Path.glob", return_value=[])
    def test_workflow_manager_init(self, mock_glob, mock_makedirs):
        from web.workflow_manager import WorkflowManager

        mgr = WorkflowManager(storage_dir="/tmp/wf")
        assert mgr.workflows == {}

    @patch("web.workflow_manager.os.makedirs")
    @patch("web.workflow_manager.Path.glob", return_value=[])
    def test_workflow_manager_create_and_list(self, mock_glob, mock_makedirs):
        from web.workflow_manager import WorkflowManager

        with patch("builtins.open", mock_open()):
            with patch("web.workflow_manager.json.dump"):
                mgr = WorkflowManager(storage_dir="/tmp/wf")
                wf = mgr.create_workflow("NewWF", "desc")
                assert "NewWF" in wf.name
                workflows = mgr.list_workflows()
                assert len(workflows) == 1

    @patch("web.workflow_manager.os.makedirs")
    @patch("web.workflow_manager.Path.glob", return_value=[])
    def test_workflow_manager_delete(self, mock_glob, mock_makedirs):
        from web.workflow_manager import WorkflowManager

        with patch("builtins.open", mock_open()):
            with patch("web.workflow_manager.json.dump"):
                with patch("web.workflow_manager.os.path.exists", return_value=False):
                    mgr = WorkflowManager(storage_dir="/tmp/wf")
                    wf = mgr.create_workflow("ToDelete")
                    assert mgr.delete_workflow(wf.id) is True
                    assert wf.id not in mgr.workflows

    @patch("web.workflow_manager.os.makedirs")
    @patch("web.workflow_manager.Path.glob", return_value=[])
    def test_workflow_manager_get_statistics(self, mock_glob, mock_makedirs):
        from web.workflow_manager import WorkflowManager

        with patch("builtins.open", mock_open()):
            with patch("web.workflow_manager.json.dump"):
                mgr = WorkflowManager(storage_dir="/tmp/wf")
                mgr.create_workflow("WF1")
                stats = mgr.get_statistics()
                assert stats["total_workflows"] == 1

    def test_workflow_executor_init(self):
        from web.workflow_manager import WorkflowExecutor

        ex = WorkflowExecutor()
        assert ex.execution_history == []

    def test_workflow_executor_execute_tool_step(self):
        from web.workflow_manager import Workflow, WorkflowExecutor

        ex = WorkflowExecutor()
        wf = Workflow("WF")
        wf.add_step("s1", "tool", {"tool": "search", "args": {}})
        result = ex.execute(wf)
        assert result["status"] == "completed"
        assert result["steps_completed"] == 1

    def test_workflow_executor_execute_unknown_step(self):
        from web.workflow_manager import Workflow, WorkflowExecutor

        ex = WorkflowExecutor()
        wf = Workflow("WF")
        wf.add_step("s1", "unknown_type")
        result = ex.execute(wf)
        assert result["steps_failed"] == 1

    def test_workflow_executor_get_execution_history(self):
        from web.workflow_manager import Workflow, WorkflowExecutor

        ex = WorkflowExecutor()
        wf = Workflow("WF")
        wf.add_step("s1", "tool", {"tool": "t"})
        ex.execute(wf)
        hist = ex.get_execution_history()
        assert len(hist) == 1

    def test_workflow_executor_callbacks(self):
        from web.workflow_manager import Workflow, WorkflowExecutor

        ex = WorkflowExecutor()
        wf = Workflow("WF")
        wf.add_step("s1", "conditional", {"condition": "x > 1"})
        on_start = MagicMock()
        on_done = MagicMock()
        result = ex.execute(
            wf, callbacks={"on_step_start": on_start, "on_step_done": on_done}
        )
        on_start.assert_called_once()
        on_done.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# Reminder Manager
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestReminderManager:
    """Tests for web.reminder_manager.ReminderManager."""

    @patch("web.reminder_manager.show_toast")
    @patch("web.reminder_manager.os.makedirs")
    @patch("web.reminder_manager.os.path.exists", return_value=False)
    def test_init(self, mock_exists, mock_makedirs, mock_toast):
        from web.reminder_manager import ReminderManager

        mgr = ReminderManager()
        assert mgr.reminders == {}
        assert mgr.timers == {}

    @patch("web.reminder_manager.show_toast")
    @patch("web.reminder_manager.os.makedirs")
    @patch("web.reminder_manager.os.path.exists", return_value=False)
    def test_add_reminder(self, mock_exists, mock_makedirs, mock_toast):
        from web.reminder_manager import ReminderManager

        mgr = ReminderManager()
        future = datetime.now() + timedelta(hours=1)
        with patch.object(mgr, "_save"):
            rid = mgr.add_reminder("Test", "Message", future)
            assert rid.startswith("reminder_")
            assert rid in mgr.reminders
            assert mgr.reminders[rid]["status"] == "scheduled"

    @patch("web.reminder_manager.show_toast")
    @patch("web.reminder_manager.os.makedirs")
    @patch("web.reminder_manager.os.path.exists", return_value=False)
    def test_add_reminder_in(self, mock_exists, mock_makedirs, mock_toast):
        from web.reminder_manager import ReminderManager

        mgr = ReminderManager()
        with patch.object(mgr, "_save"):
            rid = mgr.add_reminder_in("Test", "Msg", 3600)
            assert rid in mgr.reminders

    @patch("web.reminder_manager.show_toast")
    @patch("web.reminder_manager.os.makedirs")
    @patch("web.reminder_manager.os.path.exists", return_value=False)
    def test_cancel_reminder(self, mock_exists, mock_makedirs, mock_toast):
        from web.reminder_manager import ReminderManager

        mgr = ReminderManager()
        with patch.object(mgr, "_save"):
            rid = mgr.add_reminder("T", "M", datetime.now() + timedelta(hours=1))
            assert mgr.cancel_reminder(rid) is True
            assert mgr.reminders[rid]["status"] == "cancelled"
            assert mgr.cancel_reminder("nonexistent") is False

    @patch("web.reminder_manager.show_toast")
    @patch("web.reminder_manager.os.makedirs")
    @patch("web.reminder_manager.os.path.exists", return_value=False)
    def test_list_reminders(self, mock_exists, mock_makedirs, mock_toast):
        from web.reminder_manager import ReminderManager

        mgr = ReminderManager()
        with patch.object(mgr, "_save"):
            mgr.add_reminder("A", "M", datetime.now() + timedelta(hours=1))
            mgr.add_reminder("B", "M", datetime.now() + timedelta(hours=2))
            assert len(mgr.list_reminders()) == 2

    @patch("web.reminder_manager.show_toast")
    @patch("web.reminder_manager.os.makedirs")
    @patch("web.reminder_manager.os.path.exists", return_value=False)
    def test_clear_expired(self, mock_exists, mock_makedirs, mock_toast):
        from web.reminder_manager import ReminderManager

        mgr = ReminderManager()
        mgr.reminders = {
            "r1": {"status": "sent"},
            "r2": {"status": "expired"},
            "r3": {"status": "scheduled"},
        }
        with patch.object(mgr, "_save"):
            cleared = mgr.clear_expired()
            assert cleared == 2
            assert "r3" in mgr.reminders

    @patch("web.reminder_manager.show_toast")
    @patch("web.reminder_manager.os.makedirs")
    @patch("web.reminder_manager.os.path.exists", return_value=False)
    def test_restore_pending_marks_expired(
        self, mock_exists, mock_makedirs, mock_toast
    ):
        from web.reminder_manager import ReminderManager

        mgr = ReminderManager()
        old_time = (datetime.now() - timedelta(hours=2)).isoformat()
        mgr.reminders = {"r1": {"status": "pending", "time": old_time}}
        with patch.object(mgr, "_save"):
            mgr._restore_pending()
            assert mgr.reminders["r1"]["status"] == "expired"


# ═══════════════════════════════════════════════════════════════════════════════
# Smart Feedback
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSmartFeedback:
    """Tests for web.smart_feedback.SmartFeedback."""

    def _make_fb(self, **kwargs):
        from web.smart_feedback import SmartFeedback

        defaults = {
            "user_request": "帮我做一个关于AI的PPT",
            "task_type": "PPT",
            "emit": MagicMock(),
            "total_steps": 5,
        }
        defaults.update(kwargs)
        return SmartFeedback(**defaults)

    def test_init(self):
        fb = self._make_fb()
        assert fb.task_type == "PPT"
        assert fb.current_step == 0
        assert fb.total_steps == 5

    def test_extract_topic(self):
        fb = self._make_fb(user_request="帮我做一个关于量子计算的PPT")
        assert "量子计算" in fb._topic

    def test_start(self):
        fb = self._make_fb()
        msg, detail = fb.start()
        assert "开始" in msg or "制作" in msg
        fb.emit.assert_called_once()

    def test_step_with_total(self):
        fb = self._make_fb(total_steps=3)
        msg, _ = fb.step("Processing data")
        assert "[1/3]" in msg
        assert fb.current_step == 1

    def test_substep(self):
        fb = self._make_fb()
        msg, _ = fb.substep("Sub operation")
        assert "→" in msg

    def test_info(self):
        fb = self._make_fb()
        msg, _ = fb.info("Information message")
        assert msg == "Information message"

    def test_warn(self):
        fb = self._make_fb()
        msg, _ = fb.warn("Warning!")
        assert "⚠️" in msg

    def test_done(self):
        fb = self._make_fb()
        msg, _ = fb.done("All complete")
        assert "✅" in msg
        assert "耗时" in msg

    def test_error(self):
        fb = self._make_fb()
        msg, _ = fb.error("Something broke")
        assert "❌" in msg

    def test_quality_report_excellent(self):
        fb = self._make_fb()
        msg, _ = fb.quality_report(90)
        assert "✅" in msg

    def test_quality_report_poor(self):
        fb = self._make_fb()
        msg, _ = fb.quality_report(50, issues=["issue1"], fixes=["fix1"])
        assert "⚠️" in msg

    def test_ppt_planning(self):
        fb = self._make_fb()
        msg, _ = fb.ppt_planning("some context")
        assert "规划" in msg

    def test_ppt_outline_ready(self):
        fb = self._make_fb()
        msg, _ = fb.ppt_outline_ready(10, title="Test PPT")
        assert "10" in msg

    def test_ppt_enriching(self):
        fb = self._make_fb()
        msg, _ = fb.ppt_enriching(5)
        assert "5" in msg

    def test_for_ppt_factory(self):
        from web.smart_feedback import SmartFeedback

        fb = SmartFeedback.for_ppt("request", MagicMock())
        assert fb.task_type == "PPT"
        assert fb.total_steps == 7

    def test_for_document_factory(self):
        from web.smart_feedback import SmartFeedback

        fb = SmartFeedback.for_document("request", MagicMock(), "EXCEL")
        assert fb.task_type == "EXCEL"

    def test_heartbeat_start_stop(self):
        fb = self._make_fb()
        fb.start_heartbeat(interval=100)
        assert fb._heartbeat_active is True
        fb.stop_heartbeat()
        assert fb._heartbeat_active is False


# ═══════════════════════════════════════════════════════════════════════════════
# Suggestion Engine
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSuggestionEngine:
    """Tests for web.suggestion_engine.SuggestionEngine."""

    @pytest.fixture(autouse=True)
    def _patch_imports(self):
        """Ensure suggestion_engine can be imported by mocking its dependencies."""
        mock_bm_mod = MagicMock()
        mock_kg_mod = MagicMock()
        # Set up the EVENT_FILE_CREATE and EVENT_FILE_OPEN constants
        mock_bm_mod.BehaviorMonitor.EVENT_FILE_CREATE = "file_create"
        mock_bm_mod.BehaviorMonitor.EVENT_FILE_OPEN = "file_open"

        patches = {}
        modules_to_mock = [
            "web.behavior_monitor",
            "behavior_monitor",
            "web.knowledge_graph",
            "knowledge_graph",
            "concept_extractor",
        ]
        import sys

        for mod_name in modules_to_mock:
            if mod_name in ("web.behavior_monitor", "behavior_monitor"):
                patches[mod_name] = mock_bm_mod
            elif mod_name in ("web.knowledge_graph", "knowledge_graph"):
                patches[mod_name] = mock_kg_mod
            else:
                patches[mod_name] = MagicMock()

        # Remove cached module if it failed earlier
        for key in list(sys.modules.keys()):
            if "suggestion_engine" in key:
                del sys.modules[key]

        with patch.dict("sys.modules", patches):
            # Re-import with mocked deps
            import importlib

            if "web.suggestion_engine" in sys.modules:
                importlib.reload(sys.modules["web.suggestion_engine"])
            yield

    def _make_engine(self, db_path=":memory:"):
        from web.suggestion_engine import SuggestionEngine

        mock_bm = MagicMock()
        mock_kg = MagicMock()
        # Set constants on the mock
        mock_bm.EVENT_FILE_CREATE = "file_create"
        mock_bm.EVENT_FILE_OPEN = "file_open"
        engine = SuggestionEngine(
            behavior_monitor=mock_bm,
            knowledge_graph=mock_kg,
            db_path=db_path,
        )
        return engine, mock_bm, mock_kg

    def test_init(self, tmp_path):
        db = str(tmp_path / "test.db")
        engine, _, _ = self._make_engine(db_path=db)
        assert len(engine.rules) == 8

    def test_save_and_get_pending(self, tmp_path):
        db = str(tmp_path / "test.db")
        engine, _, _ = self._make_engine(db_path=db)
        suggestion = {
            "type": "organize",
            "title": "Test",
            "description": "Desc",
            "priority": "medium",
            "context": {},
            "action_items": [],
        }
        sid = engine._save_suggestion(suggestion)
        assert sid > 0
        pending = engine.get_pending_suggestions()
        assert len(pending) == 1
        assert pending[0]["title"] == "Test"

    def test_dismiss_suggestion(self, tmp_path):
        db = str(tmp_path / "test.db")
        engine, _, _ = self._make_engine(db_path=db)
        sid = engine._save_suggestion(
            {
                "type": "backup",
                "title": "T",
                "description": "D",
                "priority": "low",
                "context": {},
                "action_items": [],
            }
        )
        engine.dismiss_suggestion(sid, "not useful")
        pending = engine.get_pending_suggestions()
        assert len(pending) == 0

    def test_apply_suggestion(self, tmp_path):
        db = str(tmp_path / "test.db")
        engine, _, _ = self._make_engine(db_path=db)
        sid = engine._save_suggestion(
            {
                "type": "cleanup",
                "title": "T",
                "description": "D",
                "priority": "high",
                "context": {},
                "action_items": [],
            }
        )
        engine.apply_suggestion(sid)
        pending = engine.get_pending_suggestions()
        assert len(pending) == 0

    def test_get_statistics(self, tmp_path):
        db = str(tmp_path / "test.db")
        engine, _, _ = self._make_engine(db_path=db)
        engine._save_suggestion(
            {
                "type": "optimize",
                "title": "T",
                "description": "D",
                "priority": "low",
                "context": {},
                "action_items": [],
            }
        )
        stats = engine.get_statistics()
        assert stats["total_suggestions"] == 1
        assert stats["pending_suggestions"] == 1

    def test_rule_repeated_file_pattern_no_events(self, tmp_path):
        db = str(tmp_path / "test.db")
        engine, mock_bm, _ = self._make_engine(db_path=db)
        mock_bm.get_recent_events.return_value = []
        result = engine._rule_repeated_file_pattern()
        assert result == []

    def test_rule_repeated_file_pattern_with_events(self, tmp_path):
        db = str(tmp_path / "test.db")
        engine, mock_bm, _ = self._make_engine(db_path=db)
        events = [{"file_path": f"/doc/file{i}.docx"} for i in range(6)]
        mock_bm.get_recent_events.return_value = events
        result = engine._rule_repeated_file_pattern()
        assert len(result) >= 1
        assert result[0]["type"] == "workflow"

    def test_log_rule_execution(self, tmp_path):
        db = str(tmp_path / "test.db")
        engine, _, _ = self._make_engine(db_path=db)
        engine._log_rule_execution("test_rule", True, 3)
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT * FROM rule_history").fetchall()
        conn.close()
        assert len(rows) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Suggestion Annotator
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSuggestionAnnotator:
    """Tests for web.suggestion_annotator.SuggestionAnnotator."""

    def _make_annotator(self, batch_size=3):
        from web.suggestion_annotator import SuggestionAnnotator

        return SuggestionAnnotator(batch_size=batch_size)

    def test_init(self):
        ann = self._make_annotator()
        assert ann.batch_size == 3

    def test_apply_rules_empty_text(self):
        ann = self._make_annotator()
        result = ann._apply_rules("", 0, 0)
        assert result == []

    def test_apply_rules_detects_keyi_pattern(self):
        ann = self._make_annotator()
        result = ann._apply_rules("可以进行优化设计", 0, 0)
        assert any(s["类型"] == "删除冗余词" for s in result)

    def test_apply_rules_detects_jinxing(self):
        ann = self._make_annotator()
        result = ann._apply_rules("进行分析处理", 0, 0)
        found = [s for s in result if "进行" in s["原文"]]
        assert len(found) >= 1

    def test_apply_rules_detects_bei(self):
        ann = self._make_annotator()
        result = ann._apply_rules("被优化的代码", 0, 0)
        found = [s for s in result if s["类型"] == "被动→主动"]
        assert len(found) >= 1

    def test_deduplicate_suggestions(self):
        ann = self._make_annotator()
        suggestions = [
            {"原文": "A", "修改": "B", "id": "1"},
            {"原文": "A", "修改": "B", "id": "2"},
            {"原文": "C", "修改": "D", "id": "3"},
        ]
        result = ann._deduplicate_suggestions(suggestions)
        assert len(result) == 2

    def test_sse_event(self):
        ann = self._make_annotator()
        event = ann._sse_event("progress", {"stage": "reading", "progress": 10})
        assert "event: progress" in event
        assert "reading" in event

    def test_apply_user_choices_success(self):
        ann = self._make_annotator()
        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        with patch("docx.Document", return_value=mock_doc, create=True):
            result = ann.apply_user_choices(
                "test.docx", [{"id": "s_0_0", "接受": True}]
            )
            assert result["success"] is True

    def test_apply_user_choices_error(self):
        ann = self._make_annotator()
        # Force an error by using a nonexistent path without docx available
        with patch.dict("sys.modules", {"docx": None}):
            result = ann.apply_user_choices("nonexistent.docx", [])
            assert result["success"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# Template Library
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestTemplateLibrary:
    """Tests for web.template_library.TemplateLibrary."""

    @patch("web.template_library.os.makedirs")
    def _make_library(self, mock_makedirs, workspace_dir="/tmp/ws"):
        from web.template_library import TemplateLibrary

        return TemplateLibrary(workspace_dir=workspace_dir)

    def test_init(self):
        lib = self._make_library()
        assert lib.workspace_dir == "/tmp/ws"

    def test_list_templates(self):
        lib = self._make_library()
        templates = lib.list_templates()
        assert len(templates) >= 8
        ids = [t["id"] for t in templates]
        assert "business_report" in ids
        assert "resume_modern" in ids

    def test_get_template_exists(self):
        lib = self._make_library()
        t = lib.get_template("business_report")
        assert t is not None
        assert t["name"] == "商业报告"

    def test_get_template_not_found(self):
        lib = self._make_library()
        assert lib.get_template("nonexistent") is None

    def test_generate_from_template_not_found(self):
        lib = self._make_library()
        result = lib.generate_from_template("nonexistent", {})
        assert result["success"] is False

    def test_generate_from_template_unsupported_type(self):
        lib = self._make_library()
        # Temporarily add a bad template
        lib.TEMPLATES["bad"] = {
            "name": "Bad",
            "type": "xyz",
            "description": "",
            "variables": [],
        }
        result = lib.generate_from_template("bad", {})
        assert result["success"] is False
        del lib.TEMPLATES["bad"]

    @patch("web.template_library.os.makedirs")
    def test_build_business_report(self, mock_makedirs):
        lib = self._make_library()
        content = lib._build_business_report(
            {
                "title": "Test Report",
                "author": "Tester",
                "company": "Corp",
                "executive_summary": "Summary",
                "main_content": "Content",
                "conclusion": "Done",
            }
        )
        assert "Test Report" in content
        assert "Tester" in content

    @patch("web.template_library.os.makedirs")
    def test_build_resume(self, mock_makedirs):
        lib = self._make_library()
        content = lib._build_resume({"name": "John", "email": "j@x.com"})
        assert "John" in content

    @patch("web.template_library.os.makedirs")
    def test_build_product_ppt_outline(self, mock_makedirs):
        lib = self._make_library()
        outline = lib._build_product_ppt_outline(
            {
                "product_name": "Widget",
                "tagline": "Better widgets",
                "features": "Fast\nReliable",
            }
        )
        assert len(outline) == 6
        assert outline[0]["title"] == "产品概览"
