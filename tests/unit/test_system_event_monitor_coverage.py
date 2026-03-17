"""
Comprehensive tests for app.core.monitoring.system_event_monitor module.

Covers SystemEvent dataclass, SystemEventMonitor class lifecycle/methods,
internal _check_system_metrics/_record_event, and get_system_event_monitor singleton.
"""

import threading
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.core.monitoring.system_event_monitor import (
    SystemEvent,
    SystemEventMonitor,
    get_system_event_monitor,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(**overrides) -> SystemEvent:
    """Create a SystemEvent with sensible defaults, overridden as needed."""
    defaults = dict(
        timestamp="2025-01-01T00:00:00",
        event_type="cpu_high",
        severity="medium",
        metric_name="cpu_percent",
        metric_value=90.0,
        threshold=85.0,
        description="CPU usage high: 90.0%",
    )
    defaults.update(overrides)
    return SystemEvent(**defaults)


def _make_monitor(**kwargs) -> SystemEventMonitor:
    """Create a monitor that will never actually spin up a real loop."""
    return SystemEventMonitor(**kwargs)


# ---------------------------------------------------------------------------
# 1. SystemEvent dataclass
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSystemEvent:
    """Tests for the SystemEvent dataclass."""

    def test_to_dict_serialization(self):
        """to_dict returns a dict with all fields."""
        event = _make_event()
        d = event.to_dict()

        assert isinstance(d, dict)
        assert d["timestamp"] == "2025-01-01T00:00:00"
        assert d["event_type"] == "cpu_high"
        assert d["severity"] == "medium"
        assert d["metric_name"] == "cpu_percent"
        assert d["metric_value"] == 90.0
        assert d["threshold"] == 85.0
        assert d["description"] == "CPU usage high: 90.0%"

    def test_to_dict_keys(self):
        """to_dict contains exactly the expected keys."""
        expected_keys = {
            "timestamp",
            "event_type",
            "severity",
            "metric_name",
            "metric_value",
            "threshold",
            "description",
        }
        assert _make_event().to_dict().keys() == expected_keys

    def test_to_dict_round_trip_values(self):
        """Field values survive a to_dict round-trip."""
        event = _make_event(metric_value=99.9, threshold=80.0)
        d = event.to_dict()
        assert d["metric_value"] == 99.9
        assert d["threshold"] == 80.0


# ---------------------------------------------------------------------------
# 2–10. SystemEventMonitor public API
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSystemEventMonitorInit:
    """Tests for __init__ defaults and parameterisation."""

    def test_defaults(self):
        monitor = _make_monitor()
        assert monitor.check_interval == 30
        assert monitor.running is False
        assert monitor.thread is None
        assert monitor.events == []
        assert monitor.event_callbacks == []

    def test_custom_interval(self):
        monitor = _make_monitor(check_interval=10)
        assert monitor.check_interval == 10


@pytest.mark.unit
class TestSystemEventMonitorLifecycle:
    """Tests for start / stop / is_running."""

    @patch("app.core.monitoring.system_event_monitor.threading.Thread")
    def test_start_sets_running(self, mock_thread_cls):
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        mock_thread_cls.return_value = mock_thread

        monitor = _make_monitor()
        monitor.start()

        assert monitor.running is True
        mock_thread.start.assert_called_once()

    @patch("app.core.monitoring.system_event_monitor.threading.Thread")
    def test_start_idempotent(self, mock_thread_cls):
        """Calling start twice does not create a second thread."""
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        mock_thread_cls.return_value = mock_thread

        monitor = _make_monitor()
        monitor.start()
        monitor.start()  # second call — should be no-op

        assert mock_thread_cls.call_count == 1

    @patch("app.core.monitoring.system_event_monitor.threading.Thread")
    def test_stop_clears_running(self, mock_thread_cls):
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        mock_thread_cls.return_value = mock_thread

        monitor = _make_monitor()
        monitor.start()
        monitor.stop()

        assert monitor.running is False
        mock_thread.join.assert_called_once_with(timeout=1)

    def test_stop_when_not_running_is_noop(self):
        monitor = _make_monitor()
        monitor.stop()  # should not raise
        assert monitor.running is False

    @patch("app.core.monitoring.system_event_monitor.threading.Thread")
    def test_is_running_true(self, mock_thread_cls):
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        mock_thread_cls.return_value = mock_thread

        monitor = _make_monitor()
        monitor.start()
        assert monitor.is_running() is True

    def test_is_running_false_initially(self):
        monitor = _make_monitor()
        assert monitor.is_running() is False

    @patch("app.core.monitoring.system_event_monitor.threading.Thread")
    def test_is_running_false_after_stop(self, mock_thread_cls):
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        mock_thread_cls.return_value = mock_thread

        monitor = _make_monitor()
        monitor.start()
        monitor.stop()
        assert monitor.is_running() is False


@pytest.mark.unit
class TestRegisterCallback:
    """Tests for register_callback."""

    def test_stores_callback(self):
        monitor = _make_monitor()
        cb = Mock()
        monitor.register_callback(cb)
        assert cb in monitor.event_callbacks

    def test_multiple_callbacks(self):
        monitor = _make_monitor()
        cb1, cb2 = Mock(), Mock()
        monitor.register_callback(cb1)
        monitor.register_callback(cb2)
        assert len(monitor.event_callbacks) == 2


@pytest.mark.unit
class TestGetEvents:
    """Tests for get_events."""

    def test_empty(self):
        monitor = _make_monitor()
        assert monitor.get_events() == []

    def test_returns_dicts(self):
        monitor = _make_monitor()
        monitor.events = [_make_event()]
        result = monitor.get_events()
        assert len(result) == 1
        assert isinstance(result[0], dict)

    def test_most_recent_first(self):
        monitor = _make_monitor()
        e1 = _make_event(timestamp="2025-01-01T00:00:00", event_type="cpu_high")
        e2 = _make_event(timestamp="2025-01-02T00:00:00", event_type="memory_high")
        monitor.events = [e1, e2]

        result = monitor.get_events()
        assert result[0]["event_type"] == "memory_high"
        assert result[1]["event_type"] == "cpu_high"

    def test_limit(self):
        monitor = _make_monitor()
        monitor.events = [
            _make_event(timestamp=f"2025-01-{i+1:02d}T00:00:00") for i in range(10)
        ]
        result = monitor.get_events(limit=3)
        assert len(result) == 3

    def test_event_type_filter(self):
        monitor = _make_monitor()
        monitor.events = [
            _make_event(event_type="cpu_high"),
            _make_event(event_type="memory_high"),
            _make_event(event_type="cpu_high"),
        ]
        result = monitor.get_events(event_type="memory_high")
        assert len(result) == 1
        assert result[0]["event_type"] == "memory_high"

    def test_event_type_filter_no_match(self):
        monitor = _make_monitor()
        monitor.events = [_make_event(event_type="cpu_high")]
        assert monitor.get_events(event_type="disk_high") == []


@pytest.mark.unit
class TestGetSummary:
    """Tests for get_summary."""

    def test_empty_summary(self):
        monitor = _make_monitor()
        s = monitor.get_summary()
        assert s["total_events"] == 0
        assert s["by_type"] == {}
        assert s["by_severity"] == {}
        assert s["status"] == "healthy"

    def test_summary_with_events(self):
        monitor = _make_monitor()
        monitor.events = [
            _make_event(event_type="cpu_high", severity="medium"),
            _make_event(event_type="memory_high", severity="high"),
        ]
        s = monitor.get_summary()
        assert s["total_events"] == 2
        assert s["by_type"]["cpu_high"] == 1
        assert s["by_type"]["memory_high"] == 1
        assert s["by_severity"]["medium"] == 1
        assert s["by_severity"]["high"] == 1
        assert s["status"] == "critical"
        assert s["last_event"] is not None

    def test_summary_status_warning(self):
        """Status is 'warning' when >2 medium events but no high."""
        monitor = _make_monitor()
        monitor.events = [_make_event(severity="medium") for _ in range(3)]
        assert monitor.get_summary()["status"] == "warning"

    def test_summary_status_healthy(self):
        """Status stays 'healthy' with a few low-severity events."""
        monitor = _make_monitor()
        monitor.events = [_make_event(severity="low") for _ in range(2)]
        assert monitor.get_summary()["status"] == "healthy"


@pytest.mark.unit
class TestClearEvents:
    """Tests for clear_events."""

    def test_returns_count(self):
        monitor = _make_monitor()
        monitor.events = [_make_event() for _ in range(5)]
        assert monitor.clear_events() == 5

    def test_events_empty_after_clear(self):
        monitor = _make_monitor()
        monitor.events = [_make_event()]
        monitor.clear_events()
        assert monitor.events == []

    def test_clear_empty_returns_zero(self):
        monitor = _make_monitor()
        assert monitor.clear_events() == 0


# ---------------------------------------------------------------------------
# 11–14. Internal _check_system_metrics (psutil mocked)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckSystemMetrics:
    """Tests for _check_system_metrics with mocked psutil."""

    @patch("psutil.process_iter", return_value=[])
    @patch("psutil.disk_usage")
    @patch("psutil.virtual_memory")
    @patch("psutil.cpu_percent", return_value=95.0)
    def test_high_cpu_spike(self, mock_cpu, mock_mem, mock_disk, mock_procs):
        """CPU spike (>threshold AND >20% jump) records cpu_spike event."""
        mock_mem.return_value = MagicMock(
            percent=50.0, used=4 * 1024**3, total=16 * 1024**3
        )
        mock_disk.return_value = MagicMock(percent=50.0, used=50 * 1024**3)

        monitor = _make_monitor()
        monitor._last_cpu = 0.0  # large jump
        monitor._check_system_metrics()

        assert len(monitor.events) >= 1
        types = [e.event_type for e in monitor.events]
        assert "cpu_spike" in types

    @patch("psutil.process_iter", return_value=[])
    @patch("psutil.disk_usage")
    @patch("psutil.virtual_memory")
    @patch("psutil.cpu_percent", return_value=90.0)
    def test_high_cpu_no_spike(self, mock_cpu, mock_mem, mock_disk, mock_procs):
        """CPU above threshold but no spike (small jump) records cpu_high."""
        mock_mem.return_value = MagicMock(
            percent=50.0, used=4 * 1024**3, total=16 * 1024**3
        )
        mock_disk.return_value = MagicMock(percent=50.0, used=50 * 1024**3)

        monitor = _make_monitor()
        monitor._last_cpu = 85.0  # only 5% jump, not >20
        monitor._check_system_metrics()

        types = [e.event_type for e in monitor.events]
        assert "cpu_high" in types
        assert "cpu_spike" not in types

    @patch("psutil.process_iter", return_value=[])
    @patch("psutil.disk_usage")
    @patch("psutil.virtual_memory")
    @patch("psutil.cpu_percent", return_value=50.0)
    def test_high_memory(self, mock_cpu, mock_mem, mock_disk, mock_procs):
        """Memory above 90% records memory_high event."""
        mock_mem.return_value = MagicMock(
            percent=95.0, used=15 * 1024**3, total=16 * 1024**3
        )
        mock_disk.return_value = MagicMock(percent=50.0, used=50 * 1024**3)

        monitor = _make_monitor()
        monitor._check_system_metrics()

        types = [e.event_type for e in monitor.events]
        assert "memory_high" in types

    @patch("psutil.process_iter", return_value=[])
    @patch("psutil.disk_usage")
    @patch("psutil.virtual_memory")
    @patch("psutil.cpu_percent", return_value=50.0)
    def test_high_disk(self, mock_cpu, mock_mem, mock_disk, mock_procs):
        """Disk above 85% records disk_high event."""
        mock_mem.return_value = MagicMock(
            percent=50.0, used=4 * 1024**3, total=16 * 1024**3
        )
        mock_disk.return_value = MagicMock(percent=95.0, used=200 * 1024**3)

        monitor = _make_monitor()
        monitor._check_system_metrics()

        types = [e.event_type for e in monitor.events]
        assert "disk_high" in types

    @patch("psutil.process_iter", return_value=[])
    @patch("psutil.disk_usage")
    @patch("psutil.virtual_memory")
    @patch("psutil.cpu_percent", return_value=50.0)
    def test_normal_no_events(self, mock_cpu, mock_mem, mock_disk, mock_procs):
        """All metrics normal → no events recorded."""
        mock_mem.return_value = MagicMock(
            percent=50.0, used=4 * 1024**3, total=16 * 1024**3
        )
        mock_disk.return_value = MagicMock(percent=50.0, used=50 * 1024**3)

        monitor = _make_monitor()
        monitor._check_system_metrics()
        assert monitor.events == []

    @patch("psutil.process_iter")
    @patch("psutil.disk_usage")
    @patch("psutil.virtual_memory")
    @patch("psutil.cpu_percent", return_value=50.0)
    def test_high_memory_process(self, mock_cpu, mock_mem, mock_disk, mock_procs):
        """A process using >1 GB records process_memory_high."""
        mock_mem.return_value = MagicMock(
            percent=50.0, used=4 * 1024**3, total=16 * 1024**3
        )
        mock_disk.return_value = MagicMock(percent=50.0, used=50 * 1024**3)

        proc = MagicMock()
        proc.memory_info.return_value = MagicMock(rss=2000 * 1024**2)  # 2000 MB
        proc.name.return_value = "heavy_app"
        mock_procs.return_value = [proc]

        monitor = _make_monitor()
        monitor._check_system_metrics()

        types = [e.event_type for e in monitor.events]
        assert "process_memory_high" in types


# ---------------------------------------------------------------------------
# 15. _record_event triggers callbacks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecordEvent:
    """Tests for _record_event callback triggering and event storage."""

    def test_triggers_callbacks(self):
        monitor = _make_monitor()
        cb = Mock()
        monitor.register_callback(cb)

        monitor._record_event(
            event_type="cpu_high",
            severity="medium",
            metric_name="cpu_percent",
            metric_value=90.0,
            threshold=85.0,
            description="test",
        )

        cb.assert_called_once()
        arg = cb.call_args[0][0]
        assert isinstance(arg, SystemEvent)
        assert arg.event_type == "cpu_high"

    def test_callback_exception_does_not_propagate(self):
        """A failing callback must not prevent event recording."""
        monitor = _make_monitor()
        bad_cb = Mock(side_effect=RuntimeError("boom"))
        good_cb = Mock()
        monitor.register_callback(bad_cb)
        monitor.register_callback(good_cb)

        monitor._record_event(
            event_type="cpu_high",
            severity="medium",
            metric_name="cpu_percent",
            metric_value=90.0,
            threshold=85.0,
            description="test",
        )

        # Event was still recorded
        assert len(monitor.events) == 1
        # Good callback was still invoked
        good_cb.assert_called_once()

    def test_event_cap_at_100(self):
        """Events list is capped at 100 entries."""
        monitor = _make_monitor()
        for i in range(105):
            monitor._record_event(
                event_type="cpu_high",
                severity="medium",
                metric_name="cpu_percent",
                metric_value=float(i),
                threshold=85.0,
                description=f"event {i}",
            )
        assert len(monitor.events) == 100
        # Oldest events should have been trimmed; last event value is 104
        assert monitor.events[-1].metric_value == 104.0


# ---------------------------------------------------------------------------
# 16–17. Singleton factory get_system_event_monitor
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSystemEventMonitor:
    """Tests for the get_system_event_monitor singleton factory."""

    def setup_method(self):
        """Reset the module-level singleton before each test."""
        import app.core.monitoring.system_event_monitor as mod

        mod._monitor_instance = None

    def teardown_method(self):
        """Ensure singleton is cleared after each test."""
        import app.core.monitoring.system_event_monitor as mod

        if mod._monitor_instance is not None:
            mod._monitor_instance.running = False
        mod._monitor_instance = None

    def test_returns_instance(self):
        m = get_system_event_monitor(check_interval=5)
        assert isinstance(m, SystemEventMonitor)
        assert m.check_interval == 5

    def test_singleton_same_object(self):
        m1 = get_system_event_monitor(check_interval=5)
        m2 = get_system_event_monitor(check_interval=99)
        assert m1 is m2
        # Interval should remain from first call
        assert m1.check_interval == 5

    def test_reset_between_tests(self):
        """After setup_method resets the singleton, a new instance is created."""
        import app.core.monitoring.system_event_monitor as mod

        assert mod._monitor_instance is None
        m = get_system_event_monitor(check_interval=7)
        assert m.check_interval == 7


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMonitorLoopMocked:
    """Verify _monitor_loop calls _check_system_metrics and respects running flag."""

    @patch("app.core.monitoring.system_event_monitor.time.sleep")
    def test_monitor_loop_calls_check(self, mock_sleep):
        """_monitor_loop invokes _check_system_metrics then sleeps."""
        monitor = _make_monitor(check_interval=1)
        monitor._check_system_metrics = Mock()

        # Let the loop run once then stop
        def stop_after_sleep(_interval):
            monitor.running = False

        mock_sleep.side_effect = stop_after_sleep
        monitor.running = True
        monitor._monitor_loop()

        monitor._check_system_metrics.assert_called_once()
        mock_sleep.assert_called_once_with(1)

    @patch("app.core.monitoring.system_event_monitor.time.sleep")
    def test_monitor_loop_handles_check_exception(self, mock_sleep):
        """Exceptions in _check_system_metrics are caught; loop sleeps and continues."""
        monitor = _make_monitor(check_interval=2)
        monitor._check_system_metrics = Mock(side_effect=RuntimeError("oops"))

        def stop_after_sleep(_interval):
            monitor.running = False

        mock_sleep.side_effect = stop_after_sleep
        monitor.running = True
        monitor._monitor_loop()

        # Sleep is still called (in the except branch)
        mock_sleep.assert_called_once_with(2)

    def test_monitor_loop_exits_when_not_running(self):
        """Loop exits immediately when running is False."""
        monitor = _make_monitor()
        monitor.running = False
        monitor._check_system_metrics = Mock()
        monitor._monitor_loop()  # should return immediately
        monitor._check_system_metrics.assert_not_called()
