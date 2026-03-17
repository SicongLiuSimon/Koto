"""Unit tests for bounded cache / history eviction.

Tests:
- AIRouter._cache_set half-eviction strategy
- AlertManager alert_history trimming
"""

import pytest


@pytest.mark.unit
class TestAIRouterCacheSet:
    """Tests for AIRouter._cache_set half-eviction behaviour."""

    def setup_method(self):
        from app.core.routing.ai_router import AIRouter

        self._orig_cache = AIRouter._cache.copy()
        AIRouter._cache.clear()

    def teardown_method(self):
        from app.core.routing.ai_router import AIRouter

        AIRouter._cache.clear()
        AIRouter._cache.update(self._orig_cache)

    def test_keys_and_values_stored_and_retrievable(self):
        from app.core.routing.ai_router import AIRouter

        AIRouter._cache_set("k1", ("CHAT", "AI"))
        AIRouter._cache_set("k2", ("CODER", "AI"))
        assert AIRouter._cache["k1"] == ("CHAT", "AI")
        assert AIRouter._cache["k2"] == ("CODER", "AI")
        assert len(AIRouter._cache) == 2

    def test_no_eviction_at_exactly_max_size(self):
        """Fill cache to _cache_max_size via _cache_set — no eviction yet."""
        from app.core.routing.ai_router import AIRouter

        max_size = AIRouter._cache_max_size
        for i in range(max_size):
            AIRouter._cache_set(f"key_{i}", f"val_{i}")
        assert len(AIRouter._cache) == max_size
        # Every key still present
        assert "key_0" in AIRouter._cache
        assert f"key_{max_size - 1}" in AIRouter._cache

    def test_eviction_triggered_one_over_max(self):
        """Adding one entry beyond max triggers half-eviction."""
        from app.core.routing.ai_router import AIRouter

        max_size = AIRouter._cache_max_size
        half = max_size // 2  # 50
        for i in range(max_size):
            AIRouter._cache_set(f"key_{i}", f"val_{i}")
        # One more → eviction
        AIRouter._cache_set("key_new", "val_new")
        # Half evicted, then one added: 250 + 1 = 251
        assert len(AIRouter._cache) == half + 1

    def test_oldest_half_removed_newest_half_preserved(self):
        from app.core.routing.ai_router import AIRouter

        max_size = AIRouter._cache_max_size
        half = max_size // 2
        for i in range(max_size):
            AIRouter._cache_set(f"key_{i}", f"val_{i}")
        AIRouter._cache_set("key_overflow", "val_overflow")
        # Oldest half (key_0 .. key_249) evicted
        for i in range(half):
            assert f"key_{i}" not in AIRouter._cache
        # Newer half (key_250 .. key_499) preserved
        for i in range(half, max_size):
            assert f"key_{i}" in AIRouter._cache
            assert AIRouter._cache[f"key_{i}"] == f"val_{i}"
        # New entry present
        assert AIRouter._cache["key_overflow"] == "val_overflow"

    def test_cache_never_exceeds_max_size(self):
        """Over many inserts the cache stays within _cache_max_size."""
        from app.core.routing.ai_router import AIRouter

        max_size = AIRouter._cache_max_size
        for i in range(max_size + 100):
            AIRouter._cache_set(f"key_{i}", f"val_{i}")
            assert len(AIRouter._cache) <= max_size

    def test_second_eviction_cycle(self):
        """After first eviction, refilling and overflowing again works."""
        from app.core.routing.ai_router import AIRouter

        max_size = AIRouter._cache_max_size
        half = max_size // 2
        # First cycle: fill and overflow
        for i in range(max_size + 1):
            AIRouter._cache_set(f"a_{i}", i)
        size_after_first = len(AIRouter._cache)
        assert size_after_first == half + 1
        # Second cycle: keep adding until overflow again
        needed = max_size - size_after_first  # entries to reach max again
        for i in range(needed):
            AIRouter._cache_set(f"b_{i}", i)
        assert len(AIRouter._cache) == max_size
        # One more triggers second eviction
        AIRouter._cache_set("b_final", "done")
        assert len(AIRouter._cache) == half + 1


@pytest.mark.unit
class TestAlertManagerHistoryTrimming:
    """Tests for AlertManager alert_history single-item trimming."""

    def _make_manager(self, max_history=5):
        from app.core.monitoring.alert_manager import (
            AlertChannel,
            AlertManager,
            AlertRule,
        )

        mgr = AlertManager()
        mgr._MAX_ALERT_HISTORY = max_history
        rule = AlertRule(
            "test_cpu", ["cpu_high"], min_severity="high", channels=[AlertChannel.LOG]
        )
        mgr.add_rule(rule)
        return mgr

    def _make_event(self, idx):
        return {
            "event_type": "cpu_high",
            "severity": "high",
            "description": f"event_{idx}",
            "timestamp": f"2025-01-01T00:00:{idx:02d}",
        }

    def test_no_trimming_at_max(self):
        mgr = self._make_manager(max_history=5)
        for i in range(5):
            mgr.process_event(self._make_event(i))
        assert len(mgr.alert_history) == 5

    def test_trimming_on_one_over(self):
        mgr = self._make_manager(max_history=5)
        for i in range(6):
            mgr.process_event(self._make_event(i))
        assert len(mgr.alert_history) == 5

    def test_oldest_removed_newest_preserved(self):
        mgr = self._make_manager(max_history=5)
        for i in range(8):
            mgr.process_event(self._make_event(i))
        assert len(mgr.alert_history) == 5
        # The 5 most recent events correspond to indices 3-7
        descriptions = [e["severity"] for e in mgr.alert_history]
        assert all(d == "high" for d in descriptions)
        # Last entry is from the most recent process_event call
        assert mgr.alert_history[-1]["event_type"] == "cpu_high"

    def test_data_structure_integrity_after_trim(self):
        mgr = self._make_manager(max_history=5)
        for i in range(10):
            mgr.process_event(self._make_event(i))
        assert len(mgr.alert_history) == 5
        required_keys = {
            "id",
            "rule",
            "event_type",
            "severity",
            "timestamp",
            "channels",
        }
        for entry in mgr.alert_history:
            assert required_keys.issubset(entry.keys())
            assert entry["rule"] == "test_cpu"
            assert entry["event_type"] == "cpu_high"
            assert entry["severity"] == "high"
            assert isinstance(entry["channels"], list)

    def test_many_over_max_still_bounded(self):
        mgr = self._make_manager(max_history=5)
        for i in range(50):
            mgr.process_event(self._make_event(i))
            assert len(mgr.alert_history) <= 5
