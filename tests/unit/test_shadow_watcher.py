"""Unit tests for ShadowWatcher.

All tests reset the singleton between test classes and mock file I/O.
"""
from __future__ import annotations
import threading
import time
import pytest


# ---------------------------------------------------------------------------
# Fixture: fresh ShadowWatcher singleton per test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_shadow_watcher(tmp_path, monkeypatch):
    """Reset the ShadowWatcher singleton and redirect file I/O to tmp_path."""
    from app.core.monitoring import shadow_watcher as sw_module
    sw_module.ShadowWatcher._instance = None

    obs_file = tmp_path / "shadow_observations.json"
    monkeypatch.setattr(sw_module, "_OBS_FILE", obs_file)
    yield
    sw_module.ShadowWatcher._instance = None


# ---------------------------------------------------------------------------
# Singleton pattern
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_returns_same_instance(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        a = ShadowWatcher.get()
        b = ShadowWatcher.get()
        assert a is b

    def test_new_instance_has_default_observations(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        obs = w.get_observations()
        assert "topics" in obs
        assert "open_tasks" in obs
        assert "failed_tasks" in obs
        assert obs["enabled"] is True


# ---------------------------------------------------------------------------
# get_observations() shape
# ---------------------------------------------------------------------------

class TestGetObservations:
    def test_returns_dict_with_required_keys(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        obs = ShadowWatcher.get().get_observations()
        for key in ("enabled", "topics", "open_tasks", "failed_tasks",
                    "last_seen", "streak", "recent_topics_7d", "recent_topics_30d"):
            assert key in obs, f"Missing key: {key}"

    def test_recent_topics_are_dicts(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        obs = ShadowWatcher.get().get_observations()
        assert isinstance(obs["recent_topics_7d"], dict)
        assert isinstance(obs["recent_topics_30d"], dict)


# ---------------------------------------------------------------------------
# set_enabled()
# ---------------------------------------------------------------------------

class TestSetEnabled:
    def test_disable_prevents_processing(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        w.set_enabled(False)
        assert w.get_observations()["enabled"] is False

    def test_enable_restores_processing(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        w.set_enabled(False)
        w.set_enabled(True)
        assert w.get_observations()["enabled"] is True

    def test_observe_is_noop_when_disabled(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        w.set_enabled(False)
        ShadowWatcher.observe("write code for me", "Here is the code.", "s1")
        time.sleep(0.05)
        assert w.get_observations()["total_observations"] == 0


# ---------------------------------------------------------------------------
# get_open_tasks() and get_failed_tasks()
# ---------------------------------------------------------------------------

class TestTaskLists:
    def test_open_tasks_initially_empty(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        assert w.get_open_tasks() == []

    def test_failed_tasks_initially_empty(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        assert w.get_failed_tasks() == []

    def test_completed_task_excluded_from_open_tasks(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        with w._obs_lock:
            w._obs["open_tasks"].append(
                {"id": "t1", "text": "done task", "done": True, "session": "s1"}
            )
        assert len(w.get_open_tasks()) == 0

    def test_resolved_failure_excluded_from_failed_tasks(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        with w._obs_lock:
            w._obs["failed_tasks"].append(
                {"id": "f1", "text": "was bad", "resolved": True, "session": "s1"}
            )
        assert len(w.get_failed_tasks()) == 0

    def test_pending_task_included_in_open_tasks(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        with w._obs_lock:
            w._obs["open_tasks"].append(
                {"id": "t2", "text": "pending task", "done": False, "session": "s1"}
            )
        tasks = w.get_open_tasks()
        assert len(tasks) == 1
        assert tasks[0]["id"] == "t2"


# ---------------------------------------------------------------------------
# observe() – async processing (with sync fallback via _process)
# ---------------------------------------------------------------------------

class TestObserve:
    def test_observe_increments_total_on_sync_process(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        w._process("I need to debug this code.", "Here is the fix.", "s1")
        assert w.get_observations()["total_observations"] >= 1

    def test_observe_updates_last_seen(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        w._process("Hello", "Hi there!", "s1")
        assert w.get_observations()["last_seen"] is not None

    def test_failed_ai_response_registered(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        w._process(
            "What is the price of gold today?",
            "I don't have access to the internet, sorry.",
            "s1",
        )
        failed = w.get_failed_tasks()
        assert len(failed) >= 1

    def test_observe_with_disabled_watcher_does_not_process(self):
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        w.set_enabled(False)
        ShadowWatcher.observe("test msg", "test reply", "s1")
        time.sleep(0.1)
        assert w.get_observations()["total_observations"] == 0


# ---------------------------------------------------------------------------
# Logging assertions (negative cases)
# ---------------------------------------------------------------------------

class TestShadowWatcherLogging:
    def test_set_enabled_false_emits_info_log(self, caplog):
        import logging
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        with caplog.at_level(logging.INFO, logger="app.core.monitoring.shadow_watcher"):
            ShadowWatcher.get().set_enabled(False)
        assert any("已禁用" in r.message or "disabled" in r.message.lower() for r in caplog.records)

    def test_no_logs_when_observe_on_disabled_watcher(self, caplog):
        import logging
        from app.core.monitoring.shadow_watcher import ShadowWatcher
        w = ShadowWatcher.get()
        w.set_enabled(False)
        with caplog.at_level(logging.DEBUG, logger="app.core.monitoring.shadow_watcher"):
            ShadowWatcher.observe("msg", "reply", "s1")
            time.sleep(0.05)
        # No processing-related debug logs should appear (watcher disabled)
        processing_logs = [r for r in caplog.records if "process" in r.message.lower()]
        assert processing_logs == []
