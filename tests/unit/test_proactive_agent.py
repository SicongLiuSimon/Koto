"""Unit tests for ProactiveAgent.

Resets the singleton between tests and mocks ShadowWatcher + file I/O.
"""
from __future__ import annotations
from datetime import datetime, timedelta
import json
import pytest


# ---------------------------------------------------------------------------
# Fixture: isolated ProactiveAgent singleton + file I/O
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_proactive_agent(tmp_path, monkeypatch):
    from app.core.agent import proactive_agent as pa_module
    # Reset singleton
    pa_module.ProactiveAgent._instance = None
    # Redirect queue file to tmp_path
    queue_file = tmp_path / "proactive_queue.json"
    monkeypatch.setattr(pa_module, "_QUEUE_FILE", queue_file)
    yield
    pa_module.ProactiveAgent._instance = None


@pytest.fixture()
def agent():
    from app.core.agent.proactive_agent import ProactiveAgent
    return ProactiveAgent.get()


# ---------------------------------------------------------------------------
# Singleton pattern
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_returns_same_instance(self):
        from app.core.agent.proactive_agent import ProactiveAgent
        a = ProactiveAgent.get()
        b = ProactiveAgent.get()
        assert a is b

    def test_new_agent_has_empty_queue(self, agent):
        assert agent.pending() == []


# ---------------------------------------------------------------------------
# add_reminder()
# ---------------------------------------------------------------------------

class TestAddReminder:
    def test_add_reminder_shows_in_pending(self, agent):
        agent.add_reminder("Don't forget the meeting", priority="high")
        msgs = agent.pending()
        assert len(msgs) == 1
        assert msgs[0]["type"] == "reminder"
        assert "meeting" in msgs[0]["content"]

    def test_add_reminder_default_priority_is_high(self, agent):
        agent.add_reminder("check email")
        assert agent.pending()[0]["priority"] == "high"

    def test_add_low_priority_reminder(self, agent):
        agent.add_reminder("optional task", priority="low")
        assert agent.pending()[0]["priority"] == "low"

    def test_add_multiple_reminders(self, agent):
        agent.add_reminder("first")
        agent.add_reminder("second")
        assert len(agent.pending()) == 2


# ---------------------------------------------------------------------------
# pending() – sorting and filtering
# ---------------------------------------------------------------------------

class TestPending:
    def test_pending_sorted_by_priority(self, agent):
        agent.add_reminder("low task", priority="low")
        agent.add_reminder("medium task", priority="medium")
        agent.add_reminder("high task", priority="high")
        msgs = agent.pending()
        priorities = [m["priority"] for m in msgs]
        order = {"high": 0, "medium": 1, "low": 2}
        assert all(order[priorities[i]] <= order[priorities[i + 1]] for i in range(len(priorities) - 1))

    def test_expired_messages_excluded(self, agent):
        from app.core.agent.proactive_agent import _make_msg
        expired = _make_msg("reminder", "old reminder", priority="high", ttl_hours=0)
        # Back-date expires_at to the past
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        expired["expires_at"] = past
        agent._enqueue(expired)
        assert agent.pending() == []

    def test_dismissed_messages_excluded(self, agent):
        agent.add_reminder("will be dismissed")
        msg_id = agent.pending()[0]["id"]
        agent.dismiss(msg_id)
        assert agent.pending() == []

    def test_multiple_pending_all_returned(self, agent):
        for i in range(5):
            agent.add_reminder(f"task {i}")
        assert len(agent.pending()) == 5


# ---------------------------------------------------------------------------
# dismiss()
# ---------------------------------------------------------------------------

class TestDismiss:
    def test_dismiss_removes_message_from_pending(self, agent):
        agent.add_reminder("important reminder")
        msg_id = agent.pending()[0]["id"]
        agent.dismiss(msg_id)
        remaining = [m for m in agent.pending() if m["id"] == msg_id]
        assert remaining == []

    def test_dismiss_unknown_id_does_not_raise(self, agent):
        agent.dismiss("nonexistent-id")  # should not raise

    def test_dismiss_leaves_other_messages_intact(self, agent):
        agent.add_reminder("keep me")
        agent.add_reminder("dismiss me")
        msgs = agent.pending()
        to_dismiss = msgs[1]["id"]
        agent.dismiss(to_dismiss)
        remaining = agent.pending()
        assert len(remaining) == 1
        assert remaining[0]["content"] == "keep me"


# ---------------------------------------------------------------------------
# tick() – message generation
# ---------------------------------------------------------------------------

class TestTick:
    def test_tick_runs_without_error(self, agent, mocker):
        # Stub get_shadow_watcher to provide minimal observations
        mock_watcher = mocker.Mock()
        mock_watcher.enabled = True
        mock_watcher.get_observations.return_value = {
            "enabled": True,
            "last_seen": None,
            "streak": {"days": 0, "last_date": None},
            "open_tasks": [],
            "failed_tasks": [],
            "recent_topics_7d": {},
            "recent_topics_30d": {},
        }
        mock_watcher.get_open_tasks.return_value = []
        mock_watcher.get_failed_tasks.return_value = []
        mocker.patch(
            "app.core.monitoring.shadow_watcher.get_shadow_watcher",
            return_value=mock_watcher,
        )
        agent.tick(llm_fn=None)  # should not raise

    def test_tick_does_not_add_duplicate_type_too_soon(self, agent, mocker):
        """After a greeting is generated, a second tick within the cooldown window
        should not add another greeting."""
        mock_watcher = mocker.Mock()
        mock_watcher.enabled = True
        mock_watcher.get_observations.return_value = {
            "enabled": True,
            "last_seen": "2020-01-01T00:00:00",
            "streak": {"days": 10, "last_date": "2020-01-01"},
            "open_tasks": [],
            "failed_tasks": [],
            "recent_topics_7d": {},
            "recent_topics_30d": {},
        }
        mock_watcher.get_open_tasks.return_value = []
        mock_watcher.get_failed_tasks.return_value = []
        mocker.patch(
            "app.core.monitoring.shadow_watcher.get_shadow_watcher",
            return_value=mock_watcher,
        )
        agent.tick(llm_fn=None)
        count_after_first = len([m for m in agent.pending() if m["type"] == "greeting"])
        agent.tick(llm_fn=None)
        count_after_second = len([m for m in agent.pending() if m["type"] == "greeting"])
        assert count_after_second == count_after_first


# ---------------------------------------------------------------------------
# Queue message structure
# ---------------------------------------------------------------------------

class TestMessageStructure:
    def test_message_has_required_fields(self, agent):
        agent.add_reminder("structure test")
        msg = agent.pending()[0]
        for field in ("id", "type", "content", "priority", "created_at", "expires_at", "dismissed"):
            assert field in msg, f"Missing field: {field}"

    def test_message_id_is_string(self, agent):
        agent.add_reminder("test")
        assert isinstance(agent.pending()[0]["id"], str)

    def test_dismissed_defaults_to_false(self, agent):
        agent.add_reminder("test")
        assert agent.pending()[0]["dismissed"] is False


# ---------------------------------------------------------------------------
# Logging assertions
# ---------------------------------------------------------------------------

class TestProactiveAgentLogging:
    def test_no_error_logs_on_normal_add_reminder(self, agent, caplog):
        import logging
        with caplog.at_level(logging.ERROR, logger="app.core.agent.proactive_agent"):
            agent.add_reminder("all good")
        error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_logs == []
