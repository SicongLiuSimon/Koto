"""
Unit tests for web modules batch 5:
  - ProactiveTriggerSystem (proactive_trigger.py)
  - ParallelExecutor classes (parallel_executor.py)
  - ProcessedFileNetwork (processed_file_network.py)
  - PPTSynthesizer (ppt_synthesizer.py)
  - FileQualityChecker classes (file_quality_checker.py)
  - SearchEngine (search_engine.py)

Each test class covers constructors, core logic, and edge cases with mocked I/O.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, mock_open, PropertyMock

import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# ProactiveTriggerSystem
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestProactiveTrigger:
    """Tests for web.proactive_trigger.ProactiveTriggerSystem."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db_path = str(tmp_path / "triggers.db")

    def _make_system(self, **kwargs):
        from web.proactive_trigger import ProactiveTriggerSystem

        return ProactiveTriggerSystem(db_path=self.db_path, **kwargs)

    # --- init & database ---

    def test_init_creates_database_tables(self):
        """Init should create trigger_history, trigger_config, trigger_effectiveness tables."""
        self._make_system()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert "trigger_history" in tables
        assert "trigger_config" in tables
        assert "trigger_effectiveness" in tables
        assert "trigger_parameters" in tables

    def test_builtin_triggers_registered(self):
        """Init should register multiple built-in triggers."""
        system = self._make_system()
        assert len(system.triggers) >= 10
        assert "periodic_check_suggestions" in system.triggers
        assert "emergency_file_loss_risk" in system.triggers

    def test_register_trigger_custom(self):
        """register_trigger should add custom trigger to registry and db."""
        from web.proactive_trigger import TriggerCondition, TriggerType

        system = self._make_system()
        cond = TriggerCondition(
            trigger_id="custom_test",
            trigger_type=TriggerType.EVENT,
            condition_func=lambda uid: None,
            priority=5,
            cooldown_minutes=60,
            description="test trigger",
        )
        system.register_trigger(cond)
        assert "custom_test" in system.triggers

        # Verify it's persisted in database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT trigger_id FROM trigger_config WHERE trigger_id = 'custom_test'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row is not None

    def test_should_trigger_disabled(self):
        """Disabled trigger should not fire."""
        system = self._make_system()
        system.triggers["periodic_check_suggestions"].enabled = False
        assert system.should_trigger("periodic_check_suggestions") is False

    def test_should_trigger_cooldown(self):
        """Trigger within cooldown period should not fire."""
        system = self._make_system()
        trigger_id = "periodic_check_suggestions"
        system.last_trigger_times[trigger_id] = datetime.now()
        assert system.should_trigger(trigger_id) is False

    def test_should_trigger_unknown_id(self):
        """Unknown trigger_id returns False."""
        system = self._make_system()
        assert system.should_trigger("nonexistent_trigger") is False

    def test_should_trigger_past_cooldown(self):
        """Trigger past cooldown period should fire."""
        system = self._make_system()
        trigger_id = "periodic_check_suggestions"
        cooldown = system.triggers[trigger_id].cooldown_minutes
        system.last_trigger_times[trigger_id] = datetime.now() - timedelta(
            minutes=cooldown + 1
        )
        assert system.should_trigger(trigger_id) is True

    def test_list_triggers_sorted_by_priority(self):
        """list_triggers returns triggers sorted by descending priority."""
        system = self._make_system()
        triggers = system.list_triggers()
        assert len(triggers) > 0
        priorities = [t["priority"] for t in triggers]
        assert priorities == sorted(priorities, reverse=True)

    def test_update_trigger_config(self):
        """update_trigger_config modifies the in-memory and db config."""
        system = self._make_system()
        trigger_id = "periodic_morning_greeting"
        result = system.update_trigger_config(trigger_id, enabled=False, priority=1)
        assert result is True
        assert system.triggers[trigger_id].enabled is False
        assert system.triggers[trigger_id].priority == 1

    def test_update_trigger_config_nonexistent(self):
        """update_trigger_config returns False for unknown trigger_id."""
        system = self._make_system()
        assert system.update_trigger_config("no_such_trigger", enabled=True) is False

    def test_update_and_get_trigger_params(self):
        """update_trigger_params merges new params and persists them."""
        system = self._make_system()
        tid = "periodic_check_suggestions"
        original = system.get_trigger_params(tid)
        system.update_trigger_params(tid, {"custom_key": 42})
        updated = system.get_trigger_params(tid)
        assert updated["custom_key"] == 42
        # Original keys should still exist
        for k in original:
            assert k in updated

    def test_evaluate_interaction_need_no_triggers_fire(self):
        """When all triggers return None, evaluate_interaction_need returns None."""
        system = self._make_system()
        # Disable all triggers
        for t in system.triggers.values():
            t.enabled = False
        result = system.evaluate_interaction_need()
        assert result is None

    def test_determine_interaction_type_emergency(self):
        """Emergency trigger type should return ALERT."""
        from web.proactive_trigger import TriggerType, InteractionType

        system = self._make_system()
        itype = system._determine_interaction_type(TriggerType.EMERGENCY, 0.5, 0.5)
        assert itype == InteractionType.ALERT

    def test_determine_interaction_type_high_urgency(self):
        """High urgency (>=0.8) returns ALERT regardless of trigger type."""
        from web.proactive_trigger import TriggerType, InteractionType

        system = self._make_system()
        itype = system._determine_interaction_type(TriggerType.PERIODIC, 0.9, 0.5)
        assert itype == InteractionType.ALERT

    def test_determine_interaction_type_pattern(self):
        """Pattern trigger type with medium urgency returns QUESTION."""
        from web.proactive_trigger import TriggerType, InteractionType

        system = self._make_system()
        itype = system._determine_interaction_type(TriggerType.PATTERN, 0.4, 0.4)
        assert itype == InteractionType.QUESTION

    def test_calculate_disturbance_cost_late_night(self):
        """Disturbance cost should increase during late night hours."""
        from web.proactive_trigger import TriggerCondition, TriggerType

        system = self._make_system()
        trigger = TriggerCondition(
            trigger_id="test",
            trigger_type=TriggerType.PERIODIC,
            condition_func=lambda uid: None,
            priority=5,
            cooldown_minutes=60,
        )
        with patch("web.proactive_trigger.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 1, 2, 0, 0)  # 2am
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            cost = system._calculate_disturbance_cost("user1", trigger)
            assert cost >= 0.3  # late night penalty


# ═══════════════════════════════════════════════════════════════════════════════
# ParallelExecutor
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestParallelExecutor:
    """Tests for web.parallel_executor task queue, resource mgmt, and circuit breaker."""

    # --- Task dataclass ---

    def test_task_creation_defaults(self):
        from web.parallel_executor import Task, TaskType, Priority, TaskStatus

        t = Task(id="t1", session_id="s1", type=TaskType.CHAT, priority=Priority.NORMAL)
        assert t.status == TaskStatus.PENDING
        assert t.retry_count == 0
        assert t.max_retries == 3
        assert t.elapsed_time == 0

    def test_task_abort(self):
        from web.parallel_executor import Task, TaskType, Priority, TaskStatus

        t = Task(id="t1", session_id="s1", type=TaskType.CHAT, priority=Priority.NORMAL)
        t.abort()
        assert t.is_aborted is True
        assert t.status == TaskStatus.CANCELLED

    def test_task_to_dict(self):
        from web.parallel_executor import Task, TaskType, Priority

        t = Task(
            id="t1",
            session_id="s1",
            type=TaskType.CHAT,
            priority=Priority.NORMAL,
            user_input="hello world",
        )
        d = t.to_dict()
        assert d["id"] == "t1"
        assert d["type"] == "chat"
        assert d["priority"] == "NORMAL"
        assert d["status"] == "pending"

    def test_task_elapsed_time_running(self):
        from web.parallel_executor import Task, TaskType, Priority, TaskStatus

        t = Task(id="t1", session_id="s1", type=TaskType.CHAT, priority=Priority.NORMAL)
        t.started_at = datetime.now() - timedelta(seconds=5)
        t.status = TaskStatus.RUNNING
        assert t.elapsed_time >= 4.0

    # --- TaskQueueManager ---

    def test_queue_submit_and_get_next(self):
        from web.parallel_executor import TaskQueueManager, Task, TaskType, Priority

        mgr = TaskQueueManager()
        t = Task(id="t1", session_id="s1", type=TaskType.CHAT, priority=Priority.NORMAL)
        mgr.submit(t)
        result = mgr.get_next(timeout=0.1)
        assert result is not None
        assert result.id == "t1"

    def test_queue_priority_ordering(self):
        """CRITICAL tasks should be returned before HIGH/NORMAL."""
        from web.parallel_executor import TaskQueueManager, Task, TaskType, Priority

        mgr = TaskQueueManager()
        t_normal = Task(
            id="t_n", session_id="s1", type=TaskType.CHAT, priority=Priority.NORMAL
        )
        t_critical = Task(
            id="t_c", session_id="s1", type=TaskType.CHAT, priority=Priority.CRITICAL
        )
        mgr.submit(t_normal)
        mgr.submit(t_critical)
        first = mgr.get_next(timeout=0.1)
        assert first.id == "t_c"

    def test_queue_cancel_task(self):
        from web.parallel_executor import (
            TaskQueueManager,
            Task,
            TaskType,
            Priority,
            TaskStatus,
        )

        mgr = TaskQueueManager()
        t = Task(id="t1", session_id="s1", type=TaskType.CHAT, priority=Priority.NORMAL)
        mgr.submit(t)
        assert mgr.cancel("t1") is True
        assert t.status == TaskStatus.CANCELLED

    def test_queue_cancel_nonexistent(self):
        from web.parallel_executor import TaskQueueManager

        mgr = TaskQueueManager()
        assert mgr.cancel("nonexistent") is False

    def test_queue_full_raises(self):
        from web.parallel_executor import TaskQueueManager, Task, TaskType, Priority

        mgr = TaskQueueManager(max_queue_size=2)
        for i in range(2):
            mgr.submit(
                Task(
                    id=f"t{i}",
                    session_id="s1",
                    type=TaskType.CHAT,
                    priority=Priority.LOW,
                )
            )
        with pytest.raises(RuntimeError, match="full"):
            mgr.submit(
                Task(
                    id="t_overflow",
                    session_id="s1",
                    type=TaskType.CHAT,
                    priority=Priority.LOW,
                )
            )

    def test_queue_get_stats(self):
        from web.parallel_executor import TaskQueueManager, Task, TaskType, Priority

        mgr = TaskQueueManager()
        mgr.submit(
            Task(id="t1", session_id="s1", type=TaskType.CHAT, priority=Priority.HIGH)
        )
        stats = mgr.get_stats()
        assert stats["total_tasks"] == 1
        assert stats["high"] == 1

    def test_queue_get_session_tasks(self):
        from web.parallel_executor import TaskQueueManager, Task, TaskType, Priority

        mgr = TaskQueueManager()
        mgr.submit(
            Task(id="t1", session_id="s1", type=TaskType.CHAT, priority=Priority.NORMAL)
        )
        mgr.submit(
            Task(id="t2", session_id="s2", type=TaskType.CHAT, priority=Priority.NORMAL)
        )
        tasks = mgr.get_session_tasks("s1")
        assert len(tasks) == 1
        assert tasks[0].id == "t1"

    # --- ResourceManager ---

    def test_resource_manager_can_start_task(self):
        from web.parallel_executor import ResourceManager, Task, TaskType, Priority

        rm = ResourceManager()
        rm.get_memory_usage_mb = lambda: 500.0
        t = Task(id="t1", session_id="s1", type=TaskType.CHAT, priority=Priority.NORMAL)
        can_start, reason = rm.can_start_task(t)
        assert can_start is True
        assert reason == "OK"

    def test_resource_manager_release(self):
        from web.parallel_executor import ResourceManager, Task, TaskType, Priority

        rm = ResourceManager()
        rm.current_concurrent = 2
        t = Task(id="t1", session_id="s1", type=TaskType.CHAT, priority=Priority.NORMAL)
        rm.release(t)
        assert rm.current_concurrent == 1

    def test_resource_manager_max_concurrent_blocks(self):
        from web.parallel_executor import ResourceManager, Task, TaskType, Priority

        rm = ResourceManager()
        rm.get_memory_usage_mb = lambda: 100.0
        rm.current_concurrent = rm.max_concurrent_tasks
        t = Task(id="t1", session_id="s1", type=TaskType.CHAT, priority=Priority.NORMAL)
        can_start, reason = rm.can_start_task(t)
        assert can_start is False
        assert "Max concurrent" in reason

    def test_resource_manager_get_stats(self):
        from web.parallel_executor import ResourceManager

        rm = ResourceManager()
        stats = rm.get_stats()
        assert "concurrent_tasks" in stats
        assert "max_concurrent" in stats
        assert "memory_soft_limit_mb" in stats

    def test_resource_manager_refill_api_tokens(self):
        from web.parallel_executor import ResourceManager

        rm = ResourceManager()
        rm.api_call_tokens = 0.0
        rm.last_api_token_refill = time.time() - 2.0
        rm.refill_api_tokens()
        assert rm.api_call_tokens > 0

    # --- RetryPolicy ---

    def test_retry_policy_delay_increases(self):
        from web.parallel_executor import RetryPolicy

        rp = RetryPolicy(base_delay=1.0)
        d0 = rp.get_retry_delay(0)
        d1 = rp.get_retry_delay(1)
        d2 = rp.get_retry_delay(2)
        assert d1 > d0
        assert d2 > d1

    def test_retry_policy_fatal_errors(self):
        from web.parallel_executor import RetryPolicy, Task, TaskType, Priority

        rp = RetryPolicy()
        t = Task(id="t1", session_id="s1", type=TaskType.CHAT, priority=Priority.NORMAL)
        assert rp.should_retry(t, ValueError("bad")) is False
        assert rp.should_retry(t, RuntimeError("transient")) is True

    # --- CircuitBreaker ---

    def test_circuit_breaker_closed_by_default(self):
        from web.parallel_executor import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3)
        assert cb.can_execute() is True
        assert cb.state == "CLOSED"

    def test_circuit_breaker_opens_after_failures(self):
        from web.parallel_executor import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.can_execute() is False

    def test_circuit_breaker_half_open_after_timeout(self):
        from web.parallel_executor import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=2, timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "OPEN"
        time.sleep(0.15)
        assert cb.can_execute() is True
        assert cb.state == "HALF_OPEN"

    def test_circuit_breaker_recovers(self):
        from web.parallel_executor import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=2, timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        cb.can_execute()  # moves to HALF_OPEN
        cb.record_success()
        assert cb.state == "CLOSED"

    # --- TaskSnapshot ---

    def test_task_snapshot_roundtrip(self):
        from web.parallel_executor import TaskSnapshot, Task, TaskType, Priority

        t = Task(
            id="t1",
            session_id="s1",
            type=TaskType.CHAT,
            priority=Priority.NORMAL,
            user_input="test",
        )
        snap = TaskSnapshot.from_task(t)
        assert snap.task_id == "t1"
        j = snap.to_json()
        parsed = json.loads(j)
        assert parsed["task_id"] == "t1"
        assert parsed["type"] == "chat"

    # --- TaskMonitor ---

    def test_task_monitor_dashboard(self):
        from web.parallel_executor import (
            TaskMonitor,
            TaskQueueManager,
            ResourceManager,
            Task,
            TaskType,
            Priority,
            TaskStatus,
        )

        qm = TaskQueueManager()
        rm = ResourceManager()
        rm.get_memory_usage_mb = lambda: 100.0
        rm.get_cpu_usage_percent = lambda: 10.0
        monitor = TaskMonitor(qm, rm)
        t = Task(id="t1", session_id="s1", type=TaskType.CHAT, priority=Priority.NORMAL)
        t.started_at = datetime.now() - timedelta(seconds=2)
        t.completed_at = datetime.now()
        t.status = TaskStatus.COMPLETED
        monitor.record_task_complete(t)
        dash = monitor.get_dashboard()
        assert dash["completed_tasks"] == 1
        assert dash["success_rate"] > 0


# ═══════════════════════════════════════════════════════════════════════════════
# ProcessedFileNetwork
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestProcessedFileNetwork:
    """Tests for web.processed_file_network.ProcessedFileNetwork."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.tmp_path = tmp_path
        self.db_path = str(tmp_path / "file_network.db")
        self.workspace = str(tmp_path / "workspace")
        os.makedirs(self.workspace, exist_ok=True)

    def _make_network(self):
        from web.processed_file_network import ProcessedFileNetwork

        return ProcessedFileNetwork(db_path=self.db_path, workspace_dir=self.workspace)

    def _create_test_file(
        self, name="test.txt", content="Hello World file content here for testing"
    ):
        path = os.path.join(self.workspace, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_init_creates_tables(self):
        self._make_network()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert "file_records" in tables
        assert "processing_history" in tables
        assert "file_relations" in tables
        assert "text_snippets" in tables

    def test_register_file_success(self):
        net = self._make_network()
        path = self._create_test_file()
        result = net.register_file(path)
        assert result["success"] is True
        assert "file_id" in result

    def test_register_file_nonexistent(self):
        net = self._make_network()
        result = net.register_file("/nonexistent/file.txt")
        assert result["success"] is False
        assert "error" in result

    def test_register_file_idempotent(self):
        """Registering the same file twice should succeed (INSERT OR REPLACE)."""
        net = self._make_network()
        path = self._create_test_file()
        r1 = net.register_file(path)
        r2 = net.register_file(path)
        assert r1["file_id"] == r2["file_id"]

    def test_record_processing(self):
        net = self._make_network()
        path = self._create_test_file()
        result = net.record_processing(
            file_path=path,
            operation="annotate",
            changes_count=5,
            duration_seconds=1.5,
        )
        assert result["success"] is True
        assert "record_id" in result

    def test_create_relation(self):
        net = self._make_network()
        f1 = self._create_test_file("a.txt", "file a")
        f2 = self._create_test_file("b.txt", "file b")
        r1 = net.register_file(f1)
        r2 = net.register_file(f2)
        rel = net.create_relation(r1["file_id"], r2["file_id"], "related_to")
        assert rel["success"] is True

    def test_search_files_by_type(self):
        net = self._make_network()
        self._create_test_file("doc.txt", "some content")
        net.register_file(os.path.join(self.workspace, "doc.txt"))
        result = net.search_files(file_type="txt")
        assert result["success"] is True
        assert result["total_count"] >= 1

    def test_search_files_empty(self):
        net = self._make_network()
        result = net.search_files(file_type="pdf")
        assert result["success"] is True
        assert result["total_count"] == 0

    def test_get_file_network_graph(self):
        net = self._make_network()
        path = self._create_test_file()
        reg = net.register_file(path)
        result = net.get_file_network(reg["file_id"], depth=1)
        assert result["success"] is True
        assert result["node_count"] >= 1

    def test_get_file_network_nonexistent(self):
        net = self._make_network()
        result = net.get_file_network("nonexistent_id")
        assert result["success"] is False

    def test_get_statistics(self):
        net = self._make_network()
        path = self._create_test_file()
        net.register_file(path)
        stats = net.get_statistics()
        assert stats["success"] is True
        assert stats["statistics"]["total_files"] >= 1

    def test_open_file_nonexistent_id(self):
        net = self._make_network()
        result = net.open_file("no_such_id")
        assert result["success"] is False

    def test_dataclass_file_record_defaults(self):
        from web.processed_file_network import FileRecord

        fr = FileRecord(
            file_id="id1",
            path="/a/b",
            name="b",
            file_type="txt",
            size=100,
            created_at="2024-01-01",
            modified_at="2024-01-01",
            indexed_at="2024-01-01",
            content_hash="abc",
        )
        assert fr.tags == []

    def test_dataclass_file_relation_defaults(self):
        from web.processed_file_network import FileRelation

        rel = FileRelation(
            relation_id="r1",
            source_file_id="f1",
            target_file_id="f2",
            relation_type="derived_from",
            created_at="2024-01-01",
        )
        assert rel.metadata == {}


# ═══════════════════════════════════════════════════════════════════════════════
# PPTSynthesizer
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestPptSynthesizer:
    """Tests for web.ppt_synthesizer.PPTSynthesizer (non-async helpers and theme logic)."""

    def test_init_default_theme(self):
        from web.ppt_synthesizer import PPTSynthesizer

        synth = PPTSynthesizer()
        assert synth.theme == "business"
        assert synth.slide_count == 0

    def test_init_custom_theme(self):
        from web.ppt_synthesizer import PPTSynthesizer

        synth = PPTSynthesizer(theme="tech")
        assert synth.theme == "tech"

    def test_get_theme_colors_business(self):
        from web.ppt_synthesizer import PPTSynthesizer

        synth = PPTSynthesizer()
        colors = synth._get_theme_colors("business")
        assert "primary" in colors
        assert "accent" in colors
        assert "background" in colors
        assert "text" in colors
        assert len(colors["primary"]) == 3

    def test_get_theme_colors_tech(self):
        from web.ppt_synthesizer import PPTSynthesizer

        synth = PPTSynthesizer()
        colors = synth._get_theme_colors("tech")
        assert colors["primary"] == (0, 120, 215)

    def test_get_theme_colors_creative(self):
        from web.ppt_synthesizer import PPTSynthesizer

        synth = PPTSynthesizer()
        colors = synth._get_theme_colors("creative")
        assert colors["primary"] == (156, 39, 176)

    def test_get_theme_colors_unknown_defaults_to_business(self):
        from web.ppt_synthesizer import PPTSynthesizer

        synth = PPTSynthesizer()
        colors = synth._get_theme_colors("nonexistent")
        business = synth._get_theme_colors("business")
        assert colors == business

    def test_select_slide_layout_returns_blank(self):
        """_select_slide_layout always returns the blank layout (index 6)."""
        from web.ppt_synthesizer import PPTSynthesizer

        synth = PPTSynthesizer()
        mock_prs = MagicMock()
        mock_layout = MagicMock()
        mock_prs.slide_layouts.__getitem__ = MagicMock(return_value=mock_layout)
        blueprint = MagicMock()
        result = synth._select_slide_layout(mock_prs, blueprint)
        mock_prs.slide_layouts.__getitem__.assert_called_with(6)
        assert result == mock_layout

    def test_apply_beauty_rules_no_crash(self):
        """_apply_beauty_rules should not crash with minimal mocks."""
        from web.ppt_synthesizer import PPTSynthesizer

        synth = PPTSynthesizer()
        slide = MagicMock()
        blueprint = MagicMock()
        blueprint.layout_config = {}
        colors = synth._get_theme_colors("business")
        # Should not raise
        synth._apply_beauty_rules(slide, blueprint, colors)

    def test_beauty_optimizer_static_methods(self):
        """PPTBeautyOptimizer static methods should not crash with mock data."""
        from web.ppt_synthesizer import PPTBeautyOptimizer

        PPTBeautyOptimizer.add_visual_hierarchy(MagicMock(), {})
        PPTBeautyOptimizer.optimize_image_placement(MagicMock(), [], "balanced")

    def test_synthesize_from_blueprint_error_handling(self):
        """When synthesize_from_blueprint encounters an error, it returns error dict."""
        import asyncio
        from web.ppt_synthesizer import PPTSynthesizer

        synth = PPTSynthesizer()
        blueprint = MagicMock()
        blueprint.slides = [MagicMock()]
        blueprint.theme = "business"

        async def _run():
            # pptx is available but slides mock will cause attribute errors
            # triggering the except branch
            mock_prs = MagicMock()
            mock_prs.slide_layouts.__getitem__ = MagicMock(
                side_effect=RuntimeError("test error")
            )
            with patch("pptx.Presentation", return_value=mock_prs):
                return await synth.synthesize_from_blueprint(
                    blueprint, "/tmp/test.pptx"
                )

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result["success"] is False
        assert "error" in result

    def test_optimize_image_placement_no_images(self):
        """optimize_image_placement with empty images list should not crash."""
        from web.ppt_synthesizer import PPTBeautyOptimizer

        slide = MagicMock()
        PPTBeautyOptimizer.optimize_image_placement(slide, [], "balanced")
        slide.shapes.add_picture.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# FileQualityChecker
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestFileQualityChecker:
    """Tests for file_quality_checker: strip_markdown, ContentSanitizer, evaluators, gate."""

    # --- strip_markdown_for_export ---

    def test_strip_markdown_bold(self):
        from web.file_quality_checker import strip_markdown_for_export

        assert "hello" in strip_markdown_for_export("**hello**")
        assert "**" not in strip_markdown_for_export("**hello**")

    def test_strip_markdown_code_block(self):
        from web.file_quality_checker import strip_markdown_for_export

        text = "```python\nprint('hi')\n```"
        result = strip_markdown_for_export(text)
        assert "```" not in result
        assert "print" in result

    def test_strip_markdown_heading(self):
        from web.file_quality_checker import strip_markdown_for_export

        result = strip_markdown_for_export("## My Title")
        assert "##" not in result
        assert "My Title" in result

    def test_strip_markdown_link(self):
        from web.file_quality_checker import strip_markdown_for_export

        result = strip_markdown_for_export("[Click here](https://example.com)")
        assert "Click here" in result
        assert "https://example.com" not in result

    def test_strip_markdown_empty_input(self):
        from web.file_quality_checker import strip_markdown_for_export

        assert strip_markdown_for_export("") == ""
        assert strip_markdown_for_export(None) is None

    # --- strip_markdown_from_cell ---

    def test_strip_markdown_from_cell_bold(self):
        from web.file_quality_checker import strip_markdown_from_cell

        assert strip_markdown_from_cell("**test**") == "test"

    def test_strip_markdown_from_cell_empty(self):
        from web.file_quality_checker import strip_markdown_from_cell

        assert strip_markdown_from_cell("") == ""
        assert strip_markdown_from_cell(None) is None

    # --- detect_markdown_in_export ---

    def test_detect_markdown_in_export_finds_bold(self):
        from web.file_quality_checker import detect_markdown_in_export

        issues = detect_markdown_in_export("This is **bold** text")
        assert any("加粗" in i for i in issues)

    def test_detect_markdown_in_export_clean_text(self):
        from web.file_quality_checker import detect_markdown_in_export

        issues = detect_markdown_in_export("Plain text without markdown")
        assert len(issues) == 0

    # --- ContentSanitizer ---

    def test_sanitize_removes_ai_noise(self):
        from web.file_quality_checker import ContentSanitizer

        result = ContentSanitizer.sanitize_text("当然可以！这是内容")
        assert "当然可以" not in result
        assert "内容" in result

    def test_sanitize_removes_ai_dialogue(self):
        from web.file_quality_checker import ContentSanitizer

        result = ContentSanitizer.sanitize_text("数据分析报告 希望这对你有帮助")
        assert "希望这对你有帮助" not in result

    def test_sanitize_preserves_normal_text(self):
        from web.file_quality_checker import ContentSanitizer

        text = "这是一段正常的技术文档内容"
        assert ContentSanitizer.sanitize_text(text) == text

    def test_sanitize_ppt_outline(self):
        from web.file_quality_checker import ContentSanitizer

        outline = [
            {"title": "**第一章**", "points": ["当然可以！要点一", "要点二"]},
            {"title": "第二章", "content": ["内容A"]},
        ]
        cleaned, fixes = ContentSanitizer.sanitize_ppt_outline(outline)
        assert len(fixes) > 0
        # Bold markers should be removed from title
        assert "**" not in cleaned[0]["title"]

    def test_sanitize_document_text(self):
        from web.file_quality_checker import ContentSanitizer

        text = "当然可以！以下是报告内容：\n第一段正文\n希望这对你有帮助"
        cleaned, fixes = ContentSanitizer.sanitize_document_text(text)
        assert len(fixes) > 0

    # --- FileQualityEvaluator ---

    def test_evaluate_ppt_outline_good(self):
        from web.file_quality_checker import FileQualityEvaluator

        outline = [
            {
                "title": "引言",
                "type": "detail",
                "points": ["要点A长度足够了", "要点B长度足够了", "要点C长度足够了"],
            },
            {
                "title": "方法论",
                "type": "detail",
                "points": [
                    "研究方法一详细描述",
                    "研究方法二详细描述",
                    "研究方法三详细描述",
                ],
            },
            {
                "title": "结果与分析",
                "type": "detail",
                "points": [
                    "结果一的详细内容描述",
                    "结果二的详细内容描述",
                    "结果三的详细内容描述",
                ],
            },
            {
                "title": "结论总结",
                "type": "detail",
                "points": [
                    "总结要点一详细内容",
                    "总结要点二详细内容",
                    "总结要点三详细内容",
                ],
            },
        ]
        result = FileQualityEvaluator.evaluate_ppt_outline(outline)
        assert result["pass"] is True
        assert result["score"] >= 60

    def test_evaluate_ppt_outline_empty(self):
        from web.file_quality_checker import FileQualityEvaluator

        result = FileQualityEvaluator.evaluate_ppt_outline([])
        assert result["score"] < 100
        assert "过少" in str(result["issues"])

    def test_evaluate_document_text_good(self):
        from web.file_quality_checker import FileQualityEvaluator

        text = (
            "# 报告标题\n\n"
            + "这是一段很长的正文内容。" * 50
            + "\n\n## 小节\n\n更多内容在这里。"
        )
        result = FileQualityEvaluator.evaluate_document_text(text)
        assert result["pass"] is True

    def test_evaluate_document_text_empty(self):
        from web.file_quality_checker import FileQualityEvaluator

        result = FileQualityEvaluator.evaluate_document_text("")
        assert result["score"] == 0
        assert result["pass"] is False

    # --- FileQualityGate ---

    def test_gate_check_ppt_outline_proceed(self):
        from web.file_quality_checker import FileQualityGate

        outline = [
            {
                "title": "标题页面",
                "type": "detail",
                "points": ["详细要点一内容", "详细要点二内容", "详细要点三内容"],
            },
            {
                "title": "内容页面",
                "type": "detail",
                "points": ["详细要点一内容", "详细要点二内容", "详细要点三内容"],
            },
            {
                "title": "分析结果",
                "type": "detail",
                "points": ["详细要点一内容", "详细要点二内容", "详细要点三内容"],
            },
            {
                "title": "总结页面",
                "type": "detail",
                "points": ["详细要点一内容", "详细要点二内容", "详细要点三内容"],
            },
        ]
        result = FileQualityGate.check_and_fix_ppt_outline(outline)
        assert result["action"] in ("proceed", "warn")
        assert "outline" in result
        assert "quality" in result

    def test_gate_check_document_empty(self):
        from web.file_quality_checker import FileQualityGate

        result = FileQualityGate.check_and_fix_document("")
        assert result["action"] == "regenerate"

    def test_gate_check_and_fix_for_export_empty(self):
        from web.file_quality_checker import FileQualityGate

        result = FileQualityGate.check_and_fix_for_export("")
        assert result["stripped_md"] is False
        assert result["quality"]["score"] == 0

    def test_gate_check_and_fix_for_export_excel(self):
        from web.file_quality_checker import FileQualityGate

        text = "# 标题\n\n" + "正文内容很长的段落。" * 50 + "\n\n**加粗文字**"
        result = FileQualityGate.check_and_fix_for_export(text, target_format="excel")
        assert "**" not in result["text"]
        assert "#" not in result["text"]


# ═══════════════════════════════════════════════════════════════════════════════
# SearchEngine
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSearchEngine:
    """Tests for web.search_engine.SearchEngine."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.tmp_path = tmp_path
        self.workspace = str(tmp_path / "workspace")
        self.chats = str(tmp_path / "chats")
        os.makedirs(self.workspace, exist_ok=True)
        os.makedirs(self.chats, exist_ok=True)

    def _make_engine(self):
        from web.search_engine import SearchEngine

        engine = SearchEngine()
        engine.workspace_root = self.workspace
        engine.chats_root = self.chats
        engine.project_root = str(self.tmp_path)
        return engine

    def test_init_sets_paths(self):
        from web.search_engine import SearchEngine

        engine = SearchEngine()
        assert engine.workspace_root is not None
        assert engine.chats_root is not None

    def test_search_files_by_filename(self):
        engine = self._make_engine()
        # Create a file
        with open(os.path.join(self.workspace, "report.txt"), "w") as f:
            f.write("some content")
        results = engine.search_files("report")
        assert len(results) >= 1
        assert results[0]["type"] == "filename"

    def test_search_files_by_content(self):
        engine = self._make_engine()
        with open(os.path.join(self.workspace, "data.txt"), "w") as f:
            f.write("line1\nthe quick brown fox\nline3")
        results = engine.search_files("quick brown")
        assert len(results) >= 1
        assert results[0]["type"] == "content"
        assert results[0]["line"] == 2

    def test_search_files_no_match(self):
        engine = self._make_engine()
        results = engine.search_files("zzzznonexistentzzzz")
        assert len(results) == 0

    def test_search_chats(self):
        engine = self._make_engine()
        chat_data = {
            "messages": [
                {
                    "content": "Hello world",
                    "role": "user",
                    "timestamp": "2024-01-01T10:00:00",
                },
                {
                    "content": "Hi there",
                    "role": "assistant",
                    "timestamp": "2024-01-01T10:00:01",
                },
            ]
        }
        with open(os.path.join(self.chats, "chat1.json"), "w") as f:
            json.dump(chat_data, f)
        results = engine.search_chats("Hello")
        assert len(results) >= 1
        assert results[0]["type"] == "chat"
        assert results[0]["role"] == "user"

    def test_search_chats_no_chats_dir(self):
        engine = self._make_engine()
        engine.chats_root = "/nonexistent/path"
        results = engine.search_chats("test")
        assert results == []

    def test_search_notes_with_index(self):
        engine = self._make_engine()
        notes_dir = os.path.join(self.workspace, "notes")
        os.makedirs(notes_dir, exist_ok=True)
        index = {
            "note1": {
                "title": "Python Tips",
                "content": "Use list comprehensions for efficiency",
                "tags": ["python", "tips"],
                "category": "programming",
                "created_at": "2024-01-01T10:00:00",
            }
        }
        with open(os.path.join(notes_dir, "notes_index.json"), "w") as f:
            json.dump(index, f)
        results = engine.search_notes("Python")
        assert len(results) >= 1
        assert results[0]["type"] == "note"

    def test_search_notes_no_dir(self):
        engine = self._make_engine()
        results = engine.search_notes("anything")
        # Notes dir doesn't contain notes_index.json by default (no notes subdir)
        assert isinstance(results, list)

    def test_search_clipboard(self):
        engine = self._make_engine()
        clip_dir = os.path.join(self.workspace, "clipboard")
        os.makedirs(clip_dir, exist_ok=True)
        history = [
            {
                "content": "clipboard item one",
                "type": "text",
                "timestamp": "2024-06-01T10:00:00",
            },
            {
                "content": "another clipboard item",
                "type": "text",
                "timestamp": "2024-06-01T11:00:00",
            },
        ]
        with open(os.path.join(clip_dir, "history.json"), "w") as f:
            json.dump(history, f)
        results = engine.search_clipboard("clipboard item")
        assert len(results) >= 1

    def test_search_all_aggregates(self):
        engine = self._make_engine()
        results = engine.search_all("test")
        assert "files" in results
        assert "chats" in results
        assert "notes" in results
        assert "clipboard" in results

    def test_extract_match_context_found(self):
        engine = self._make_engine()
        ctx = engine._extract_match_context(
            "The quick brown fox jumps over the lazy dog", "fox", 20
        )
        assert "fox" in ctx

    def test_extract_match_context_not_found(self):
        engine = self._make_engine()
        ctx = engine._extract_match_context("Hello world", "zzz", 20)
        # Falls back to first N chars
        assert "Hello" in ctx

    def test_search_by_date_range_empty(self):
        engine = self._make_engine()
        results = engine.search_by_date_range("2024-01-01", "2024-01-31")
        assert isinstance(results, dict)

    def test_search_by_date_range_invalid_date(self):
        engine = self._make_engine()
        results = engine.search_by_date_range("not-a-date", "also-bad")
        assert isinstance(results, dict)

    def test_filter_by_date_iso(self):
        engine = self._make_engine()
        items = [
            {"timestamp": "2024-06-15T10:00:00"},
            {"timestamp": "2024-07-01T10:00:00"},
        ]
        start = datetime(2024, 6, 1)
        end = datetime(2024, 6, 30)
        filtered = engine._filter_by_date(items, start, end)
        assert len(filtered) == 1

    def test_get_search_engine_singleton(self):
        import web.search_engine as mod

        old = mod._search_engine
        mod._search_engine = None
        try:
            e1 = mod.get_search_engine()
            e2 = mod.get_search_engine()
            assert e1 is e2
        finally:
            mod._search_engine = old
