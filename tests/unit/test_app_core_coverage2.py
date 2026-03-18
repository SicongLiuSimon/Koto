# -*- coding: utf-8 -*-
"""
Comprehensive unit tests for low-coverage app/core modules.

Targets:
  1. app.core.routing.__init__          (lazy imports)
  2. app.core.goal.goal_job_handler      (goal check handling)
  3. app.core.monitoring.event_database   (SQLite event storage)
  4. app.core.learning.lora_pipeline      (LoRA training pipeline)
  5. app.core.analytics.trend_analyzer    (trend analysis)
  6. app.core.scripts.script_generator    (script generation)
  7. app.core.learning.distill_manager    (distillation manager)
  8. app.core.ops.remediation_policy      (remediation rules)
  9. app.core.services.search_service     (search service)
 10. app.core.services.rag_service        (RAG service)
 11. app.api.distill_routes               (distill API routes)
"""

import json
import os
import queue
import sqlite3
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

try:
    import google.genai._api_client  # noqa: F401

    HAS_GENAI = True
except (ImportError, ModuleNotFoundError):
    HAS_GENAI = False

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Routing __init__ (lazy imports)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestRoutingInit:
    """Tests for app.core.routing.__init__ lazy attribute loading."""

    def test_smart_dispatcher_direct_import(self):
        from app.core.routing import SmartDispatcher

        assert SmartDispatcher is not None

    @patch.dict("sys.modules", {"app.core.routing.local_model_router": MagicMock()})
    def test_local_model_router_lazy_import(self):
        import app.core.routing as routing_mod

        result = routing_mod.__getattr__("LocalModelRouter")
        assert result is not None

    @patch.dict("sys.modules", {"app.core.routing.local_model_router": MagicMock()})
    def test_router_decision_lazy_import(self):
        import app.core.routing as routing_mod

        result = routing_mod.__getattr__("RouterDecision")
        assert result is not None

    @patch.dict("sys.modules", {"app.core.routing.ai_router": MagicMock()})
    def test_ai_router_lazy_import(self):
        import app.core.routing as routing_mod

        result = routing_mod.__getattr__("AIRouter")
        assert result is not None

    @patch.dict("sys.modules", {"app.core.routing.task_decomposer": MagicMock()})
    def test_task_decomposer_lazy_import(self):
        import app.core.routing as routing_mod

        result = routing_mod.__getattr__("TaskDecomposer")
        assert result is not None

    @patch.dict("sys.modules", {"app.core.routing.local_planner": MagicMock()})
    def test_local_planner_lazy_import(self):
        import app.core.routing as routing_mod

        result = routing_mod.__getattr__("LocalPlanner")
        assert result is not None

    @patch.dict("sys.modules", {"app.core.routing.plan_executor": MagicMock()})
    def test_plan_executor_lazy_import(self):
        import app.core.routing as routing_mod

        result = routing_mod.__getattr__("PlanExecutor")
        assert result is not None

    @patch.dict("sys.modules", {"app.core.routing.plan_executor": MagicMock()})
    def test_build_handlers_lazy_import(self):
        import app.core.routing as routing_mod

        result = routing_mod.__getattr__("build_handlers_from_orchestrator")
        assert result is not None

    @patch.dict("sys.modules", {"app.core.routing.tool_router": MagicMock()})
    def test_tool_router_lazy_import(self):
        import app.core.routing as routing_mod

        result = routing_mod.__getattr__("ToolRouter")
        assert result is not None

    @patch.dict("sys.modules", {"app.core.routing.tool_router": MagicMock()})
    def test_get_tool_router_lazy_import(self):
        import app.core.routing as routing_mod

        result = routing_mod.__getattr__("get_tool_router")
        assert result is not None

    def test_unknown_attribute_raises(self):
        import app.core.routing as routing_mod

        with pytest.raises(AttributeError, match="has no attribute"):
            routing_mod.__getattr__("NonExistentClass")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. GoalJobHandler
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestGoalJobHandler:
    """Tests for app.core.goal.goal_job_handler."""

    def test_register_goal_handler(self):
        from app.core.goal.goal_job_handler import register_goal_handler

        runner = MagicMock()
        register_goal_handler(runner)
        runner.register_handler.assert_called_once_with(
            "goal_check",
            pytest.importorskip("app.core.goal.goal_job_handler")._handle_goal_check,
        )

    def test_handle_missing_goal_id(self):
        from app.core.goal.goal_job_handler import _handle_goal_check

        ctx = MagicMock()
        ctx.payload = {}
        result = _handle_goal_check(ctx)
        assert "缺少 goal_id" in result

    @patch("app.core.goal.goal_manager.get_goal_manager")
    def test_handle_goal_not_found(self, mock_get_gm):
        from app.core.goal.goal_job_handler import _handle_goal_check

        mock_gm = MagicMock()
        mock_gm.get.return_value = None
        mock_get_gm.return_value = mock_gm
        ctx = MagicMock()
        ctx.payload = {"goal_id": "abc12345"}
        result = _handle_goal_check(ctx)
        assert "未找到目标" in result

    @patch("app.core.goal.goal_manager.get_goal_manager")
    def test_handle_goal_inactive_status(self, mock_get_gm):
        from app.core.goal.goal_job_handler import _handle_goal_check
        from app.core.goal.goal_manager import GoalStatus

        mock_goal = MagicMock()
        mock_goal.title = "Test Goal"
        # Use an actual non-ACTIVE GoalStatus member
        non_active = [s for s in GoalStatus if s != GoalStatus.ACTIVE]
        mock_goal.status = non_active[0] if non_active else MagicMock()

        mock_gm = MagicMock()
        mock_gm.get.return_value = mock_goal
        mock_get_gm.return_value = mock_gm

        ctx = MagicMock()
        ctx.payload = {"goal_id": "abc12345"}
        result = _handle_goal_check(ctx)
        assert "跳过执行" in result

    @patch("app.core.goal.goal_manager.get_goal_manager")
    def test_handle_goal_agent_exception(self, mock_get_gm):
        from app.core.goal.goal_job_handler import _handle_goal_check
        from app.core.goal.goal_manager import GoalStatus

        mock_goal = MagicMock()
        mock_goal.status = GoalStatus.ACTIVE
        mock_goal.title = "Test Goal"
        mock_goal.user_goal = "Track something"
        mock_goal.last_result = None
        mock_goal.session_id = "sess-1"
        mock_goal.get_context.return_value = {"progress_summary": "some progress"}

        mock_run = MagicMock()
        mock_run.run_id = "run-1"

        mock_gm = MagicMock()
        mock_gm.get.return_value = mock_goal
        mock_gm.start_run.return_value = mock_run
        mock_get_gm.return_value = mock_gm

        ctx = MagicMock()
        ctx.payload = {"goal_id": "abc12345"}
        ctx.task_id = "task-1"
        ctx.is_cancelled.return_value = False

        # Patch the agent imports to raise an exception
        with patch.dict(
            "sys.modules",
            {
                "app.core.llm.gemini": MagicMock(
                    get_gemini_client=MagicMock(side_effect=Exception("LLM error"))
                )
            },
        ):
            with patch(
                "app.core.agent.unified_agent.UnifiedAgent",
                side_effect=Exception("LLM error"),
            ):
                result = _handle_goal_check(ctx)
                assert result is not None
                mock_gm.finish_run.assert_called_once()

    @patch("app.core.goal.goal_manager.get_goal_manager")
    def test_handle_goal_completed_status_tag(self, mock_get_gm):
        """When agent returns STATUS:COMPLETED, goal should be marked complete."""
        from app.core.goal.goal_job_handler import _handle_goal_check
        from app.core.goal.goal_manager import GoalStatus

        mock_goal = MagicMock()
        mock_goal.status = GoalStatus.ACTIVE
        mock_goal.title = "Test Goal"
        mock_goal.user_goal = "Track something"
        mock_goal.last_result = None
        mock_goal.session_id = "sess-1"
        mock_goal.get_context.return_value = {}

        mock_run = MagicMock()
        mock_run.run_id = "run-1"

        mock_gm = MagicMock()
        mock_gm.get.return_value = mock_goal
        mock_gm.start_run.return_value = mock_run
        mock_get_gm.return_value = mock_gm

        ctx = MagicMock()
        ctx.payload = {"goal_id": "abc12345"}
        ctx.task_id = "task-1"
        ctx.is_cancelled.return_value = False

        # Mock UnifiedAgent to return STATUS:COMPLETED
        mock_step = MagicMock()
        mock_step.step_type = MagicMock()
        mock_step.step_type.value = "ANSWER"
        mock_step.content = "Goal done\nSTATUS:COMPLETED"

        mock_agent = MagicMock()
        mock_agent.run.return_value = [mock_step]

        mock_gemini_mod = MagicMock()
        mock_agent_mod = MagicMock()
        mock_agent_mod.UnifiedAgent.return_value = mock_agent
        mock_registry_mod = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "app.core.llm.gemini": mock_gemini_mod,
                "app.core.agent.unified_agent": mock_agent_mod,
                "app.core.agent.tool_registry": mock_registry_mod,
            },
        ):
            result = _handle_goal_check(ctx)
            assert result is not None
            mock_gm.complete.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. EventDatabase
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestEventDatabase:
    """Tests for app.core.monitoring.event_database.EventDatabase."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path):
        """Create an EventDatabase with a temp DB path."""
        from app.core.monitoring.event_database import EventDatabase

        db = EventDatabase.__new__(EventDatabase)
        db.DB_PATH = tmp_path / "test_events.db"
        db.lock = threading.Lock()
        db._local = threading.local()
        db._ensure_db_exists()
        self.db = db

    def test_save_event_returns_positive_id(self):
        event = {
            "timestamp": "2025-01-01T10:00:00",
            "event_type": "cpu_high",
            "severity": "high",
            "metric_name": "cpu",
            "metric_value": 95.0,
            "threshold": 80.0,
            "description": "CPU too high",
        }
        eid = self.db.save_event(event)
        assert eid > 0

    def test_get_events_returns_saved_events(self):
        event = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "event_type": "memory_high",
            "severity": "medium",
            "description": "Memory usage elevated",
        }
        self.db.save_event(event)
        events = self.db.get_events(hours_back=1)
        assert len(events) >= 1
        assert events[0]["event_type"] == "memory_high"

    def test_get_events_filter_by_type(self):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.db.save_event(
            {"timestamp": now, "event_type": "cpu_high", "severity": "high"}
        )
        self.db.save_event(
            {"timestamp": now, "event_type": "disk_full", "severity": "low"}
        )
        events = self.db.get_events(event_type="cpu_high", hours_back=1)
        assert all(e["event_type"] == "cpu_high" for e in events)

    def test_get_events_filter_by_severity(self):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.db.save_event(
            {"timestamp": now, "event_type": "cpu_high", "severity": "high"}
        )
        self.db.save_event({"timestamp": now, "event_type": "mem", "severity": "low"})
        events = self.db.get_events(severity="high", hours_back=1)
        assert all(e["severity"] == "high" for e in events)

    def test_get_stats_empty(self):
        stats = self.db.get_stats(days_back=7)
        assert stats["total_events"] == 0

    def test_save_and_get_stats(self):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.db.save_event(
            {"timestamp": now, "event_type": "cpu_high", "severity": "high"}
        )
        self.db.save_event(
            {"timestamp": now, "event_type": "mem", "severity": "medium"}
        )
        stats = self.db.get_stats(days_back=1)
        assert stats["total_events"] == 2

    def test_save_remediation_action(self):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        eid = self.db.save_event(
            {"timestamp": now, "event_type": "test", "severity": "low"}
        )
        aid = self.db.save_remediation_action(
            eid, "restart_service", result={"ok": True}
        )
        assert aid > 0

    def test_update_remediation_status(self):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        eid = self.db.save_event(
            {"timestamp": now, "event_type": "test", "severity": "low"}
        )
        aid = self.db.save_remediation_action(eid, "restart_service")
        result = self.db.update_remediation_status(
            aid, "completed", result={"success": True}
        )
        assert result is True

    def test_clear_old_events(self):
        old_ts = "2020-01-01T00:00:00"
        self.db.save_event(
            {"timestamp": old_ts, "event_type": "old", "severity": "low"}
        )
        deleted = self.db.clear_old_events(days_old=1)
        assert deleted >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 4. LoRA Pipeline
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestLoRAPipeline:
    """Tests for app.core.learning.lora_pipeline."""

    def test_training_config_defaults(self):
        from app.core.learning.lora_pipeline import TrainingConfig

        cfg = TrainingConfig()
        assert cfg.base_model == "Qwen/Qwen3-8B"
        assert cfg.lora_r == 16
        assert cfg.num_epochs == 3

    def test_training_config_to_dict(self):
        from app.core.learning.lora_pipeline import TrainingConfig

        cfg = TrainingConfig()
        d = cfg.to_dict()
        assert isinstance(d, dict)
        assert "base_model" in d

    def test_for_hardware_high_vram(self):
        from app.core.learning.lora_pipeline import TrainingConfig

        cfg = TrainingConfig.for_hardware(vram_gb=24, ram_gb=64)
        assert "8B" in cfg.base_model
        assert cfg.fp16 is True
        assert cfg.use_4bit is False

    def test_for_hardware_mid_vram(self):
        from app.core.learning.lora_pipeline import TrainingConfig

        cfg = TrainingConfig.for_hardware(vram_gb=12, ram_gb=32)
        assert "4B" in cfg.base_model

    def test_for_hardware_low_vram(self):
        from app.core.learning.lora_pipeline import TrainingConfig

        cfg = TrainingConfig.for_hardware(vram_gb=6, ram_gb=16)
        assert "1.7B" in cfg.base_model

    def test_for_hardware_very_low_vram(self):
        from app.core.learning.lora_pipeline import TrainingConfig

        cfg = TrainingConfig.for_hardware(vram_gb=4, ram_gb=8)
        assert "0.6B" in cfg.base_model
        assert cfg.use_4bit is True

    def test_for_hardware_cpu_only(self):
        from app.core.learning.lora_pipeline import TrainingConfig

        cfg = TrainingConfig.for_hardware(vram_gb=0, ram_gb=8)
        assert "0.6B" in cfg.base_model
        assert cfg.gradient_accumulation_steps == 8

    def test_adapter_meta_roundtrip(self):
        from app.core.learning.lora_pipeline import AdapterMeta

        meta = AdapterMeta(
            skill_id="test",
            adapter_path="/tmp/adapter",
            base_model="Qwen/Qwen3-8B",
            trained_at="2025-01-01",
            num_samples=100,
            num_epochs=3,
        )
        d = meta.to_dict()
        restored = AdapterMeta.from_dict(d)
        assert restored.skill_id == "test"
        assert restored.base_model == "Qwen/Qwen3-8B"

    def test_pipeline_init(self):
        from app.core.learning.lora_pipeline import LoRAPipeline, TrainingConfig

        cfg = TrainingConfig()
        pipeline = LoRAPipeline(config=cfg)
        assert pipeline.config.base_model == "Qwen/Qwen3-8B"

    def test_check_prerequisites_missing_packages(self):
        from app.core.learning.lora_pipeline import LoRAPipeline

        pipeline = LoRAPipeline()
        ok, missing = pipeline.check_prerequisites()
        # Some deps may be missing in test env; just check return types
        assert isinstance(ok, bool)
        assert isinstance(missing, list)

    def test_prepare_dataset_no_trace_file(self, tmp_path):
        from app.core.learning.lora_pipeline import LoRAPipeline

        pipeline = LoRAPipeline()
        with patch("app.core.learning.lora_pipeline._SHADOW_DIR", str(tmp_path)):
            result = pipeline.prepare_dataset("nonexistent_skill")
            assert result is None

    def test_prepare_dataset_too_few_samples(self, tmp_path):
        from app.core.learning.lora_pipeline import LoRAPipeline

        trace_file = tmp_path / "test_skill.jsonl"
        trace_file.write_text('{"user_input": "hi", "ai_response": "hello"}\n')
        pipeline = LoRAPipeline()
        with patch("app.core.learning.lora_pipeline._SHADOW_DIR", str(tmp_path)):
            result = pipeline.prepare_dataset("test_skill", min_samples=10)
            assert result is None

    def test_prepare_dataset_qwen3_format(self, tmp_path):
        from app.core.learning.lora_pipeline import LoRAPipeline

        trace_file = tmp_path / "test_skill.jsonl"
        lines = [
            json.dumps({"user_input": f"question {i}", "ai_response": f"answer {i}"})
            for i in range(10)
        ]
        trace_file.write_text("\n".join(lines))
        pipeline = LoRAPipeline()
        ds_dir = tmp_path / "datasets"
        ds_dir.mkdir()
        with patch("app.core.learning.lora_pipeline._SHADOW_DIR", str(tmp_path)):
            with patch("app.core.learning.lora_pipeline._DATASET_DIR", str(ds_dir)):
                result = pipeline.prepare_dataset("test_skill", min_samples=5)
                assert result is not None
                with open(result, encoding="utf-8") as f:
                    data = json.load(f)
                assert len(data) == 10
                assert "messages" in data[0]

    def test_prepare_dataset_alpaca_format(self, tmp_path):
        from app.core.learning.lora_pipeline import LoRAPipeline

        trace_file = tmp_path / "alpha_skill.jsonl"
        lines = [
            json.dumps({"user_input": f"q{i}", "ai_response": f"a{i}"})
            for i in range(5)
        ]
        trace_file.write_text("\n".join(lines))
        pipeline = LoRAPipeline()
        ds_dir = tmp_path / "datasets"
        ds_dir.mkdir()
        with patch("app.core.learning.lora_pipeline._SHADOW_DIR", str(tmp_path)):
            with patch("app.core.learning.lora_pipeline._DATASET_DIR", str(ds_dir)):
                result = pipeline.prepare_dataset(
                    "alpha_skill", min_samples=3, output_format="alpaca"
                )
                assert result is not None
                with open(result, encoding="utf-8") as f:
                    data = json.load(f)
                assert "instruction" in data[0]

    def test_train_already_active(self):
        from app.core.learning.lora_pipeline import LoRAPipeline

        pipeline = LoRAPipeline()
        pipeline._active_trainings["busy_skill"] = True
        result = pipeline.train("busy_skill")
        assert result["success"] is False
        assert "正在训练中" in result["error"]

    def test_register_as_adapter(self, tmp_path):
        from app.core.learning.lora_pipeline import LoRAPipeline

        pipeline = LoRAPipeline()
        with patch("app.core.learning.lora_pipeline._ADAPTER_DIR", str(tmp_path)):
            path = pipeline.register_as_adapter(
                "my_skill", "/tmp/adapter", num_samples=50
            )
            assert path.endswith(".json")
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
            assert meta["skill_id"] == "my_skill"

    def test_list_adapters_empty(self, tmp_path):
        from app.core.learning.lora_pipeline import LoRAPipeline

        pipeline = LoRAPipeline()
        with patch("app.core.learning.lora_pipeline._ADAPTER_DIR", str(tmp_path)):
            result = pipeline.list_adapters()
            assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# 5. TrendAnalyzer
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestTrendAnalyzer:
    """Tests for app.core.analytics.trend_analyzer.TrendAnalyzer."""

    @pytest.fixture
    def analyzer(self):
        from app.core.analytics.trend_analyzer import TrendAnalyzer

        return TrendAnalyzer()

    def test_analyze_empty_events(self, analyzer):
        result = analyzer.analyze_event_trends([])
        assert "error" in result

    def test_analyze_event_trends_basic(self, analyzer):
        events = [
            {"event_type": "cpu_high", "severity": "high"},
            {"event_type": "cpu_high", "severity": "high"},
            {"event_type": "cpu_high", "severity": "medium"},
            {"event_type": "disk_full", "severity": "low"},
        ]
        result = analyzer.analyze_event_trends(events, hours_back=24)
        assert result["total_events"] == 4
        assert "cpu_high" in result["event_types"]
        assert result["avg_events_per_hour"] > 0
        assert len(result["top_trends"]) >= 1

    def test_predict_issues_empty(self, analyzer):
        result = analyzer.predict_issues([])
        assert "warnings" in result or result.get("predictions") == []

    def test_predict_issues_high_frequency(self, analyzer):
        events = [{"event_type": "cpu_high"}] * 12
        result = analyzer.predict_issues(events)
        assert result["total_risk_count"] >= 1
        preds = result["predictions"]
        assert any(p["risk_level"] == "high" for p in preds)

    def test_predict_issues_with_metrics(self, analyzer):
        events = [{"event_type": "mem"}] * 3
        metrics = {"cpu_usage": 90.0, "memory_usage": 92.0}
        result = analyzer.predict_issues(events, metrics=metrics)
        preds = result["predictions"]
        issues = [p["issue"] for p in preds]
        assert any("CPU" in i for i in issues)
        assert any("Memory" in i for i in issues)

    def test_get_historical_comparison_no_data(self, analyzer):
        result = analyzer.get_historical_comparison({"cpu_usage": 50}, {})
        assert "message" in result

    def test_get_historical_comparison_with_data(self, analyzer):
        current = {"cpu_usage": 80, "memory_usage": 70, "disk_usage": 60}
        historical = {"avg_cpu": 50, "avg_memory": 60, "avg_disk": 55}
        result = analyzer.get_historical_comparison(current, historical)
        assert "cpu" in result["comparisons"]
        assert result["comparisons"]["cpu"]["status"] == "above"

    def test_anomaly_score_empty(self, analyzer):
        assert analyzer.get_anomaly_score([]) == 0.0

    def test_anomaly_score_levels(self, analyzer):
        assert analyzer.get_anomaly_score([{"e": 1}] * 1, hours_back=24) == 0.0
        assert analyzer.get_anomaly_score([{"e": 1}] * 30, hours_back=24) == 0.1
        assert analyzer.get_anomaly_score([{"e": 1}] * 80, hours_back=24) == 0.3
        assert analyzer.get_anomaly_score([{"e": 1}] * 200, hours_back=24) == 0.6
        assert analyzer.get_anomaly_score([{"e": 1}] * 300, hours_back=24) == 0.9


# ═══════════════════════════════════════════════════════════════════════════════
# 6. ScriptGenerator
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestScriptGenerator:
    """Tests for app.core.scripts.script_generator.ScriptGenerator."""

    def test_kill_process_ps_with_pid(self):
        from app.core.scripts.script_generator import ScriptGenerator, ScriptType

        gen = ScriptGenerator(script_type=ScriptType.POWERSHELL)
        script = gen.generate_kill_process_script("notepad.exe", pid=1234)
        assert "1234" in script
        assert "Stop-Process" in script

    def test_kill_process_ps_no_pid(self):
        from app.core.scripts.script_generator import ScriptGenerator, ScriptType

        gen = ScriptGenerator(script_type=ScriptType.POWERSHELL)
        script = gen.generate_kill_process_script("notepad.exe")
        assert "notepad" in script

    def test_kill_process_bash_with_pid(self):
        from app.core.scripts.script_generator import ScriptGenerator, ScriptType

        gen = ScriptGenerator(script_type=ScriptType.BASH)
        script = gen.generate_kill_process_script("python", pid=5678)
        assert "5678" in script
        assert "kill" in script

    def test_kill_process_bash_no_pid(self):
        from app.core.scripts.script_generator import ScriptGenerator, ScriptType

        gen = ScriptGenerator(script_type=ScriptType.BASH)
        script = gen.generate_kill_process_script("python")
        assert "pkill" in script

    def test_clear_disk_space_ps(self):
        from app.core.scripts.script_generator import ScriptGenerator, ScriptType

        gen = ScriptGenerator(script_type=ScriptType.POWERSHELL)
        script = gen.generate_clear_disk_space_script(min_gb=10)
        assert "10" in script
        assert "Recycle Bin" in script

    def test_clear_disk_space_bash(self):
        from app.core.scripts.script_generator import ScriptGenerator, ScriptType

        gen = ScriptGenerator(script_type=ScriptType.BASH)
        script = gen.generate_clear_disk_space_script()
        assert "apt-get" in script

    def test_restart_service_ps(self):
        from app.core.scripts.script_generator import ScriptGenerator, ScriptType

        gen = ScriptGenerator(script_type=ScriptType.POWERSHELL)
        script = gen.generate_restart_service_script("wuauserv")
        assert "wuauserv" in script

    def test_restart_service_bash(self):
        from app.core.scripts.script_generator import ScriptGenerator, ScriptType

        gen = ScriptGenerator(script_type=ScriptType.BASH)
        script = gen.generate_restart_service_script("nginx")
        assert "systemctl restart nginx" in script

    def test_memory_cleanup_ps(self):
        from app.core.scripts.script_generator import ScriptGenerator, ScriptType

        gen = ScriptGenerator(script_type=ScriptType.POWERSHELL)
        script = gen.generate_memory_cleanup_script()
        assert "GC" in script

    def test_memory_cleanup_bash(self):
        from app.core.scripts.script_generator import ScriptGenerator, ScriptType

        gen = ScriptGenerator(script_type=ScriptType.BASH)
        script = gen.generate_memory_cleanup_script()
        assert "drop_caches" in script

    def test_generate_fix_script_cpu_high(self):
        from app.core.scripts.script_generator import ScriptGenerator, ScriptType

        gen = ScriptGenerator(script_type=ScriptType.POWERSHELL)
        result = gen.generate_fix_script("cpu_high", process_name="chrome.exe")
        assert result["status"] == "success"
        assert result["filename"].endswith(".ps1")
        assert result["issue_type"] == "cpu_high"

    def test_generate_fix_script_unknown_type(self):
        from app.core.scripts.script_generator import ScriptGenerator

        gen = ScriptGenerator()
        result = gen.generate_fix_script("unknown_issue")
        assert result["status"] == "error"

    def test_requires_admin(self):
        from app.core.scripts.script_generator import ScriptGenerator

        gen = ScriptGenerator()
        assert gen._requires_admin("disk_full") is True
        assert gen._requires_admin("cpu_high") is False

    def test_disk_health_ps(self):
        from app.core.scripts.script_generator import ScriptGenerator, ScriptType

        gen = ScriptGenerator(script_type=ScriptType.POWERSHELL)
        script = gen.generate_check_disk_health_script()
        assert "disk" in script.lower() or "Disk" in script

    def test_disk_health_bash(self):
        from app.core.scripts.script_generator import ScriptGenerator, ScriptType

        gen = ScriptGenerator(script_type=ScriptType.BASH)
        script = gen.generate_check_disk_health_script()
        assert "df -h" in script


# ═══════════════════════════════════════════════════════════════════════════════
# 7. DistillManager
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestDistillManager:
    """Tests for app.core.learning.distill_manager."""

    def test_training_job_to_dict(self):
        from app.core.learning.distill_manager import TrainingJob

        job = TrainingJob(job_id="j1", skill_id="test_skill")
        d = job.to_dict()
        assert d["job_id"] == "j1"
        assert d["skill_id"] == "test_skill"
        assert d["status"] == "queued"

    def test_training_job_add_log(self):
        from app.core.learning.distill_manager import TrainingJob

        job = TrainingJob(job_id="j1", skill_id="test_skill")
        job.add_log("Starting training")
        assert len(job.logs) == 1
        assert "Starting training" in job.logs[0]

    def test_training_job_log_cap(self):
        from app.core.learning.distill_manager import TrainingJob

        job = TrainingJob(job_id="j1", skill_id="test_skill")
        for i in range(600):
            job.add_log(f"log {i}")
        assert len(job.logs) == 500

    def test_job_status_constants(self):
        from app.core.learning.distill_manager import JobStatus

        assert JobStatus.QUEUED == "queued"
        assert JobStatus.RUNNING == "running"
        assert JobStatus.DONE == "done"
        assert JobStatus.FAILED == "failed"
        assert JobStatus.CANCELLED == "cancelled"

    @patch("app.core.learning.distill_manager.DistillManager._start_worker")
    def test_submit_returns_job_id(self, mock_worker):
        from app.core.learning.distill_manager import DistillManager

        mgr = DistillManager.__new__(DistillManager)
        mgr._jobs = {}
        mgr._queues = {}
        mgr._task_queue = queue.Queue()
        job_id = mgr.submit("email_writer")
        assert isinstance(job_id, str)
        assert len(job_id) == 8

    @patch("app.core.learning.distill_manager.DistillManager._start_worker")
    def test_submit_duplicate_returns_existing(self, mock_worker):
        from app.core.learning.distill_manager import DistillManager

        mgr = DistillManager.__new__(DistillManager)
        mgr._jobs = {}
        mgr._queues = {}
        mgr._task_queue = queue.Queue()
        id1 = mgr.submit("same_skill")
        id2 = mgr.submit("same_skill")
        assert id1 == id2

    @patch("app.core.learning.distill_manager.DistillManager._start_worker")
    def test_get_job(self, mock_worker):
        from app.core.learning.distill_manager import DistillManager

        mgr = DistillManager.__new__(DistillManager)
        mgr._jobs = {}
        mgr._queues = {}
        mgr._task_queue = queue.Queue()
        job_id = mgr.submit("test_skill")
        job = mgr.get_job(job_id)
        assert job is not None
        assert job.skill_id == "test_skill"

    @patch("app.core.learning.distill_manager.DistillManager._start_worker")
    def test_list_jobs(self, mock_worker):
        from app.core.learning.distill_manager import DistillManager

        mgr = DistillManager.__new__(DistillManager)
        mgr._jobs = {}
        mgr._queues = {}
        mgr._task_queue = queue.Queue()
        mgr.submit("skill_a")
        mgr.submit("skill_b")
        jobs = mgr.list_jobs()
        assert len(jobs) == 2

    @patch("app.core.learning.distill_manager.DistillManager._start_worker")
    def test_list_jobs_filter_by_skill(self, mock_worker):
        from app.core.learning.distill_manager import DistillManager

        mgr = DistillManager.__new__(DistillManager)
        mgr._jobs = {}
        mgr._queues = {}
        mgr._task_queue = queue.Queue()
        mgr.submit("skill_a")
        mgr.submit("skill_b")
        jobs = mgr.list_jobs(skill_id="skill_a")
        assert len(jobs) == 1

    @patch("app.core.learning.distill_manager.DistillManager._start_worker")
    def test_cancel_queued_job(self, mock_worker):
        from app.core.learning.distill_manager import DistillManager

        mgr = DistillManager.__new__(DistillManager)
        mgr._jobs = {}
        mgr._queues = {}
        mgr._task_queue = queue.Queue()
        job_id = mgr.submit("cancel_me")
        assert mgr.cancel(job_id) is True
        assert mgr.get_job(job_id).status == "cancelled"

    @patch("app.core.learning.distill_manager.DistillManager._start_worker")
    def test_cancel_nonexistent_job(self, mock_worker):
        from app.core.learning.distill_manager import DistillManager

        mgr = DistillManager.__new__(DistillManager)
        mgr._jobs = {}
        mgr._queues = {}
        mgr._task_queue = queue.Queue()
        assert mgr.cancel("nonexistent") is False

    @patch("app.core.learning.distill_manager.DistillManager._start_worker")
    def test_push_event(self, mock_worker):
        from app.core.learning.distill_manager import DistillManager

        mgr = DistillManager.__new__(DistillManager)
        mgr._jobs = {}
        mgr._queues = {}
        mgr._task_queue = queue.Queue()
        job_id = mgr.submit("test_skill")
        mgr._push_event(job_id, "progress", {"pct": 50})
        q = mgr._queues[job_id]
        evt = q.get_nowait()
        assert evt["event"] == "progress"
        assert evt["pct"] == 50


# ═══════════════════════════════════════════════════════════════════════════════
# 8. RemediationPolicy
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestRemediationPolicy:
    """Tests for app.core.ops.remediation_policy."""

    @pytest.fixture
    def policy(self):
        from app.core.ops.remediation_policy import RemediationPolicy

        return RemediationPolicy()

    def test_init_has_builtin_rules(self, policy):
        rules = policy.list_rules()
        names = [r["name"] for r in rules]
        assert "gc_on_memory_pressure" in names
        assert "disable_failed_skill" in names

    def test_add_rule(self, policy):
        from app.core.ops.remediation_policy import RemediationRule

        policy.add_rule(
            RemediationRule(
                name="test_rule",
                trigger_event="test_event",
                action=lambda e, c: None,
            )
        )
        names = [r["name"] for r in policy.list_rules()]
        assert "test_rule" in names

    def test_remove_rule(self, policy):
        from app.core.ops.remediation_policy import RemediationRule

        policy.add_rule(
            RemediationRule(
                name="to_remove", trigger_event="x", action=lambda e, c: None
            )
        )
        policy.remove_rule("to_remove")
        names = [r["name"] for r in policy.list_rules()]
        assert "to_remove" not in names

    def test_enable_disable_rule(self, policy):
        policy.enable_rule("gc_on_memory_pressure", enabled=False)
        rules = {r["name"]: r for r in policy.list_rules()}
        assert rules["gc_on_memory_pressure"]["enabled"] is False
        policy.enable_rule("gc_on_memory_pressure", enabled=True)
        rules = {r["name"]: r for r in policy.list_rules()}
        assert rules["gc_on_memory_pressure"]["enabled"] is True

    def test_should_fire_respects_cooldown(self, policy):
        from app.core.ops.remediation_policy import RemediationRule

        rule = RemediationRule(
            name="test_cd",
            trigger_event="evt",
            action=lambda e, c: None,
            cooldown_seconds=9999,
        )
        rule.last_fired = time.monotonic()
        assert policy._should_fire(rule, "evt", time.monotonic()) is False

    def test_should_fire_min_occurrences(self, policy):
        from app.core.ops.remediation_policy import RemediationRule

        rule = RemediationRule(
            name="test_min",
            trigger_event="evt",
            action=lambda e, c: None,
            min_occurrences=5,
            window_seconds=60,
        )
        # Not enough events
        assert policy._should_fire(rule, "evt", time.monotonic()) is False

    def test_handle_fires_matching_rule(self, policy):
        from app.core.ops.remediation_policy import RemediationRule

        fired = []
        policy.add_rule(
            RemediationRule(
                name="test_fire",
                trigger_event="my_event",
                action=lambda e, c: fired.append(True),
                cooldown_seconds=0,
            )
        )
        event = MagicMock()
        event.event_type = "my_event"
        policy.handle(event)
        # Give the thread time to execute
        time.sleep(0.3)
        assert len(fired) >= 1

    @patch("app.core.ops.remediation_policy.gc.collect", return_value=10)
    def test_action_gc(self, mock_gc):
        from app.core.ops.remediation_policy import _action_gc

        _action_gc(MagicMock(), {})
        mock_gc.assert_called_once()

    def test_action_log_model_fallback(self):
        from app.core.ops.remediation_policy import _action_log_model_fallback

        event = MagicMock()
        event.detail = {"from": "gemini", "to": "local", "reason": "timeout"}
        _action_log_model_fallback(event, {})  # Should not raise

    def test_action_warn_queue_backlog(self):
        from app.core.ops.remediation_policy import _action_warn_queue_backlog

        _action_warn_queue_backlog(MagicMock(), {})  # Should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# 9. SearchService
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSearchService:
    """Tests for app.core.services.search_service.SearchService."""

    def test_init_no_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("app.core.services.search_service.HAS_GENAI_V2", False):
                from app.core.services.search_service import SearchService

                svc = SearchService(api_key=None)
                assert svc.client is None

    def test_search_no_genai(self):
        with patch("app.core.services.search_service.HAS_GENAI_V2", False):
            from app.core.services.search_service import SearchService

            svc = SearchService.__new__(SearchService)
            svc.api_key = None
            svc.client = None
            # Manually set the module-level flag for the duration of the call
            import app.core.services.search_service as ss_mod

            original = ss_mod.HAS_GENAI_V2
            ss_mod.HAS_GENAI_V2 = False
            try:
                result = svc.search("test query")
                assert result["success"] is False
                assert "not installed" in result.get("error", "")
            finally:
                ss_mod.HAS_GENAI_V2 = original

    def test_search_no_client(self):
        import app.core.services.search_service as ss_mod
        from app.core.services.search_service import SearchService

        original = ss_mod.HAS_GENAI_V2
        ss_mod.HAS_GENAI_V2 = True
        try:
            svc = SearchService.__new__(SearchService)
            svc.api_key = None
            svc.client = None
            result = svc.search("query")
            assert result["success"] is False
            assert "not initialized" in result.get("error", "")
        finally:
            ss_mod.HAS_GENAI_V2 = original

    @pytest.mark.skipif(not HAS_GENAI, reason="google.genai not properly installed")
    def test_search_success(self):
        import app.core.services.search_service as ss_mod
        from app.core.services.search_service import SearchService

        original = ss_mod.HAS_GENAI_V2
        ss_mod.HAS_GENAI_V2 = True
        try:
            svc = SearchService.__new__(SearchService)
            svc.api_key = "fake-key"
            mock_response = MagicMock()
            mock_response.text = "Search result text"
            mock_response.candidates = []
            svc.client = MagicMock()
            svc.client.models.generate_content.return_value = mock_response
            result = svc.search("test query")
            assert result["success"] is True
            assert result["data"] == "Search result text"
        finally:
            ss_mod.HAS_GENAI_V2 = original

    def test_search_no_response_text(self):
        import app.core.services.search_service as ss_mod
        from app.core.services.search_service import SearchService

        original = ss_mod.HAS_GENAI_V2
        ss_mod.HAS_GENAI_V2 = True
        try:
            svc = SearchService.__new__(SearchService)
            svc.api_key = "fake-key"
            mock_response = MagicMock()
            mock_response.text = ""
            svc.client = MagicMock()
            svc.client.models.generate_content.return_value = mock_response
            result = svc.search("query")
            assert result["success"] is False
        finally:
            ss_mod.HAS_GENAI_V2 = original

    @pytest.mark.skipif(not HAS_GENAI, reason="google.genai not properly installed")
    def test_search_exception(self):
        import app.core.services.search_service as ss_mod
        from app.core.services.search_service import SearchService

        original = ss_mod.HAS_GENAI_V2
        ss_mod.HAS_GENAI_V2 = True
        try:
            svc = SearchService.__new__(SearchService)
            svc.api_key = "fake-key"
            svc.client = MagicMock()
            svc.client.models.generate_content.side_effect = Exception("API error")
            result = svc.search("query")
            assert result["success"] is False
            assert "API error" in result["error"]
        finally:
            ss_mod.HAS_GENAI_V2 = original


# ═══════════════════════════════════════════════════════════════════════════════
# 10. RAGService
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestRAGService:
    """Tests for app.core.services.rag_service.RAGService."""

    def test_init_creates_dir(self, tmp_path):
        from app.core.services.rag_service import RAGService

        idx_dir = tmp_path / "rag_idx"
        svc = RAGService(index_dir=str(idx_dir), auto_load=False)
        assert idx_dir.exists()

    def test_stats_no_index(self, tmp_path):
        from app.core.services.rag_service import RAGService

        svc = RAGService(index_dir=str(tmp_path / "empty_rag"), auto_load=False)
        stats = svc.stats()
        assert stats["initialized"] is False
        assert stats["doc_count"] == 0

    def test_clear_empty(self, tmp_path):
        from app.core.services.rag_service import RAGService

        svc = RAGService(index_dir=str(tmp_path / "rag"), auto_load=False)
        assert svc.clear() is True
        assert svc._doc_count == 0

    def test_retrieve_empty_index(self, tmp_path):
        from app.core.services.rag_service import RAGService

        svc = RAGService(index_dir=str(tmp_path / "rag"), auto_load=False)
        results = svc.retrieve("hello")
        assert results == []

    def test_save_no_vectorstore(self, tmp_path):
        from app.core.services.rag_service import RAGService

        svc = RAGService(index_dir=str(tmp_path / "rag"), auto_load=False)
        assert svc.save() is True

    def test_load_no_index_file(self, tmp_path):
        from app.core.services.rag_service import RAGService

        svc = RAGService(index_dir=str(tmp_path / "rag"), auto_load=False)
        assert svc.load() is False

    def test_split_text(self, tmp_path):
        from app.core.services.rag_service import RAGService

        svc = RAGService(index_dir=str(tmp_path / "rag"), auto_load=False)
        text = "Hello world. " * 200
        chunks = svc._split_text(text, source="test")
        assert len(chunks) >= 1
        assert chunks[0].metadata["source"] == "test"

    def test_hybrid_retrieve_empty(self, tmp_path):
        from app.core.services.rag_service import RAGService

        svc = RAGService(index_dir=str(tmp_path / "rag"), auto_load=False)
        results = svc.hybrid_retrieve("query")
        assert results == []

    def test_tokenize_function(self):
        from app.core.services.rag_service import _tokenize

        tokens = _tokenize("hello world test")
        assert len(tokens) > 0

    def test_rag_answer_no_chunks(self, tmp_path):
        from app.core.services.rag_service import RAGService

        svc = RAGService(index_dir=str(tmp_path / "rag"), auto_load=False)
        result = svc.rag_answer("what is koto?")
        assert result["context_used"] is False
        assert result["answer"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Distill Routes (Flask Blueprint)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestDistillRoutes:
    """Tests for app.api.distill_routes Flask blueprint."""

    @pytest.fixture
    def app(self):
        from flask import Flask

        from app.api.distill_routes import distill_bp

        app = Flask(__name__)
        app.register_blueprint(distill_bp, url_prefix="/api/distill")
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @patch("app.api.distill_routes._get_manager")
    def test_submit_missing_skill_id(self, mock_mgr, client):
        resp = client.post("/api/distill/submit", json={})
        assert resp.status_code == 400
        assert "skill_id" in resp.get_json()["error"]

    @patch("app.api.distill_routes._get_manager")
    def test_submit_success(self, mock_mgr, client):
        mock_mgr.return_value.submit.return_value = "abc12345"
        resp = client.post("/api/distill/submit", json={"skill_id": "email_writer"})
        assert resp.status_code == 202
        data = resp.get_json()
        assert data["job_id"] == "abc12345"

    @patch("app.api.distill_routes._get_manager")
    def test_submit_exception(self, mock_mgr, client):
        mock_mgr.return_value.submit.side_effect = RuntimeError("boom")
        resp = client.post("/api/distill/submit", json={"skill_id": "test"})
        assert resp.status_code == 500

    @patch("app.api.distill_routes._get_manager")
    def test_list_jobs(self, mock_mgr, client):
        mock_mgr.return_value.list_jobs.return_value = [{"job_id": "j1"}]
        resp = client.get("/api/distill/jobs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1

    @patch("app.api.distill_routes._get_manager")
    def test_get_job_found(self, mock_mgr, client):
        mock_job = MagicMock()
        mock_job.to_dict.return_value = {"job_id": "j1", "status": "running"}
        mock_mgr.return_value.get_job.return_value = mock_job
        resp = client.get("/api/distill/jobs/j1")
        assert resp.status_code == 200

    @patch("app.api.distill_routes._get_manager")
    def test_get_job_not_found(self, mock_mgr, client):
        mock_mgr.return_value.get_job.return_value = None
        resp = client.get("/api/distill/jobs/nonexistent")
        assert resp.status_code == 404

    @patch("app.api.distill_routes._get_manager")
    def test_cancel_job_success(self, mock_mgr, client):
        mock_mgr.return_value.cancel.return_value = True
        resp = client.post("/api/distill/jobs/j1/cancel")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    @patch("app.api.distill_routes._get_manager")
    def test_cancel_job_not_found(self, mock_mgr, client):
        mock_mgr.return_value.cancel.return_value = False
        mock_mgr.return_value.get_job.return_value = None
        resp = client.post("/api/distill/jobs/j1/cancel")
        assert resp.status_code == 404

    @patch("app.api.distill_routes._get_manager")
    def test_cancel_job_conflict(self, mock_mgr, client):
        mock_mgr.return_value.cancel.return_value = False
        mock_job = MagicMock()
        mock_job.status = "running"
        mock_mgr.return_value.get_job.return_value = mock_job
        resp = client.post("/api/distill/jobs/j1/cancel")
        assert resp.status_code == 409

    @patch("app.api.distill_routes._get_manager")
    def test_list_adapters(self, mock_mgr, client):
        with patch("glob.glob", return_value=[]):
            resp = client.get("/api/distill/adapters")
            assert resp.status_code == 200
            assert "adapters" in resp.get_json()
