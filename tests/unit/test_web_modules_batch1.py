"""
Unit tests for web modules: SettingsManager, MemoryManager, token_tracker,
SystemInfoCollector, WorkFileLibrary.

Covers constructors, getters/setters, and core logic with mocked I/O.
"""
from __future__ import annotations

import copy
import json
import os
import sqlite3
import time
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, mock_open

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# SettingsManager
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSettingsManager:
    """Tests for web.settings.SettingsManager (singleton)."""

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        """Reset the singleton between tests so each test is independent."""
        import web.settings as mod

        old_instance = mod.SettingsManager._instance
        old_settings = mod.SettingsManager._settings
        # Deep-copy DEFAULT_SETTINGS so tests that mutate nested dicts
        # (via .set()) don't corrupt the module-level constant.
        saved_defaults = copy.deepcopy(mod.DEFAULT_SETTINGS)
        mod.SettingsManager._instance = None
        mod.SettingsManager._settings = None
        mod.SettingsManager._dirty = False
        mod.SettingsManager._flush_timer = None
        yield
        # Restore after test
        mod.DEFAULT_SETTINGS.clear()
        mod.DEFAULT_SETTINGS.update(saved_defaults)
        mod.SettingsManager._instance = old_instance
        mod.SettingsManager._settings = old_settings

    # -- construction / loading ------------------------------------------------

    def test_init_loads_defaults_when_no_file(self):
        """When no settings file exists, defaults are used."""
        with patch("web.settings.os.path.exists", return_value=False), \
             patch("web.settings.os.makedirs"), \
             patch("builtins.open", mock_open()):
            from web.settings import SettingsManager
            mgr = SettingsManager()
            assert mgr._settings is not None
            assert "storage" in mgr._settings
            assert "appearance" in mgr._settings

    def test_init_loads_from_file(self, tmp_path):
        """When a settings file exists, it is loaded and merged with defaults."""
        saved = {"appearance": {"theme": "light"}, "custom_key": "val"}
        settings_file = tmp_path / "user_settings.json"
        settings_file.write_text(json.dumps(saved), encoding="utf-8")

        with patch("web.settings.SETTINGS_FILE", str(settings_file)):
            from web.settings import SettingsManager
            mgr = SettingsManager()
            # User override is preserved
            assert mgr.get("appearance", "theme") == "light"
            # Defaults for keys not in file are merged in
            assert mgr.get("appearance", "language") is not None

    def test_init_falls_back_on_corrupt_file(self, tmp_path):
        """Corrupt JSON should fall back to defaults."""
        settings_file = tmp_path / "user_settings.json"
        settings_file.write_text("{bad json", encoding="utf-8")

        with patch("web.settings.SETTINGS_FILE", str(settings_file)):
            from web.settings import SettingsManager
            mgr = SettingsManager()
            assert "storage" in mgr._settings

    # -- get / set / update ----------------------------------------------------

    def test_get_category(self):
        with patch("web.settings.os.path.exists", return_value=False), \
             patch("web.settings.os.makedirs"), \
             patch("builtins.open", mock_open()):
            from web.settings import SettingsManager
            mgr = SettingsManager()
            ai_settings = mgr.get("ai")
            assert isinstance(ai_settings, dict)
            assert "default_model" in ai_settings

    def test_get_key(self):
        with patch("web.settings.os.path.exists", return_value=False), \
             patch("web.settings.os.makedirs"), \
             patch("builtins.open", mock_open()):
            from web.settings import SettingsManager
            mgr = SettingsManager()
            assert mgr.get("appearance", "theme") == "dark"

    def test_get_missing_returns_none(self):
        with patch("web.settings.os.path.exists", return_value=False), \
             patch("web.settings.os.makedirs"), \
             patch("builtins.open", mock_open()):
            from web.settings import SettingsManager
            mgr = SettingsManager()
            assert mgr.get("nonexistent") is None
            assert mgr.get("appearance", "nonexistent") is None

    def test_set_value(self):
        with patch("web.settings.os.path.exists", return_value=False), \
             patch("web.settings.os.makedirs"), \
             patch("builtins.open", mock_open()), \
             patch("web.settings.threading.Timer") as MockTimer:
            MockTimer.return_value = MagicMock()
            from web.settings import SettingsManager
            mgr = SettingsManager()
            result = mgr.set("appearance", "theme", "light")
            assert result is True
            assert mgr.get("appearance", "theme") == "light"
            assert mgr._dirty is True

    def test_update_values(self):
        with patch("web.settings.os.path.exists", return_value=False), \
             patch("web.settings.os.makedirs"), \
             patch("builtins.open", mock_open()), \
             patch("web.settings.threading.Timer") as MockTimer:
            MockTimer.return_value = MagicMock()
            from web.settings import SettingsManager
            mgr = SettingsManager()
            result = mgr.update("appearance", {"theme": "auto", "font_size": "large"})
            assert result is True
            assert mgr.get("appearance", "theme") == "auto"
            assert mgr.get("appearance", "font_size") == "large"

    # -- reset / flush ---------------------------------------------------------

    def test_reset_category(self):
        with patch("web.settings.os.path.exists", return_value=False), \
             patch("web.settings.os.makedirs"), \
             patch("builtins.open", mock_open()):
            from web.settings import SettingsManager, DEFAULT_SETTINGS
            mgr = SettingsManager()
            mgr._settings["appearance"]["theme"] = "light"
            mgr.reset("appearance")
            assert mgr.get("appearance", "theme") == DEFAULT_SETTINGS["appearance"]["theme"]

    def test_reset_all(self):
        with patch("web.settings.os.path.exists", return_value=False), \
             patch("web.settings.os.makedirs"), \
             patch("builtins.open", mock_open()):
            from web.settings import SettingsManager, DEFAULT_SETTINGS
            mgr = SettingsManager()
            mgr._settings["ai"]["default_model"] = "custom"
            mgr.reset()
            assert mgr.get("ai", "default_model") == DEFAULT_SETTINGS["ai"]["default_model"]

    def test_flush_writes_when_dirty(self):
        with patch("web.settings.os.path.exists", return_value=False), \
             patch("web.settings.os.makedirs") as mk, \
             patch("builtins.open", mock_open()) as mo:
            from web.settings import SettingsManager
            mgr = SettingsManager()
            mgr._dirty = True
            result = mgr.flush()
            assert result is True
            assert mgr._dirty is False

    def test_flush_noop_when_clean(self):
        with patch("web.settings.os.path.exists", return_value=False), \
             patch("web.settings.os.makedirs"), \
             patch("builtins.open", mock_open()):
            from web.settings import SettingsManager
            mgr = SettingsManager()
            mgr._dirty = False
            result = mgr.flush()
            assert result is True

    # -- convenience properties ------------------------------------------------

    def test_property_accessors(self):
        with patch("web.settings.os.path.exists", return_value=False), \
             patch("web.settings.os.makedirs"), \
             patch("builtins.open", mock_open()):
            from web.settings import SettingsManager
            mgr = SettingsManager()
            assert mgr.workspace_dir is not None
            assert mgr.documents_dir is not None
            assert mgr.images_dir is not None
            assert mgr.chats_dir is not None
            assert mgr.theme == "dark"


# ═══════════════════════════════════════════════════════════════════════════════
# MemoryManager
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestMemoryManager:
    """Tests for web.memory_manager.MemoryManager."""

    @pytest.fixture()
    def manager(self, tmp_path):
        """Create a MemoryManager backed by a temp file."""
        from web.memory_manager import MemoryManager
        mem_path = str(tmp_path / "memory.json")
        return MemoryManager(memory_path=mem_path)

    # -- init / loading --------------------------------------------------------

    def test_init_creates_empty_list(self, manager):
        assert manager.memories == []

    def test_load_existing_file(self, tmp_path):
        mem_file = tmp_path / "memory.json"
        data = [{"id": 1, "content": "hello", "category": "fact",
                 "source": "user", "created_at": "2024-01-01 00:00:00", "use_count": 0}]
        mem_file.write_text(json.dumps(data), encoding="utf-8")
        from web.memory_manager import MemoryManager
        mgr = MemoryManager(memory_path=str(mem_file))
        assert len(mgr.memories) == 1
        assert mgr.memories[0]["content"] == "hello"

    def test_load_corrupt_file(self, tmp_path):
        mem_file = tmp_path / "memory.json"
        mem_file.write_text("{bad", encoding="utf-8")
        from web.memory_manager import MemoryManager
        mgr = MemoryManager(memory_path=str(mem_file))
        assert mgr.memories == []

    # -- add / delete ----------------------------------------------------------

    def test_add_memory(self, manager):
        item = manager.add_memory("likes cats", category="user_preference")
        assert item["content"] == "likes cats"
        assert item["category"] == "user_preference"
        assert item["source"] == "user"
        assert item["use_count"] == 0
        assert len(manager.memories) == 1

    def test_add_memory_persists(self, manager):
        manager.add_memory("fact one")
        raw = json.loads(Path(manager.memory_path).read_text(encoding="utf-8"))
        assert len(raw) == 1

    def test_delete_memory(self, manager):
        item = manager.add_memory("delete me")
        assert manager.delete_memory(item["id"]) is True
        assert len(manager.memories) == 0

    def test_delete_nonexistent(self, manager):
        assert manager.delete_memory(99999) is False

    # -- listing / searching ---------------------------------------------------

    def test_get_all_memories_sorted(self, manager):
        manager.add_memory("first")
        # created_at uses strftime with second precision, so wait >1s
        time.sleep(1.1)
        manager.add_memory("second")
        result = manager.get_all_memories()
        # newest first
        assert result[0]["content"] == "second"

    def test_list_memories_alias(self, manager):
        manager.add_memory("test")
        assert manager.list_memories() == manager.get_all_memories()

    def test_search_empty_query(self, manager):
        manager.add_memory("hello world")
        assert manager.search_memories("") == []

    def test_search_exact_phrase(self, manager):
        manager.add_memory("I love Python programming")
        manager.add_memory("Java is fine too")
        results = manager.search_memories("Python programming")
        assert len(results) >= 1
        assert results[0]["content"] == "I love Python programming"

    def test_search_keyword_match(self, manager):
        manager.add_memory("dark mode preferred")
        results = manager.search_memories("dark")
        assert len(results) == 1

    def test_search_respects_limit(self, manager):
        for i in range(10):
            manager.add_memory(f"memory about topic {i}")
        results = manager.search_memories("topic", limit=3)
        assert len(results) <= 3

    # -- context string --------------------------------------------------------

    def test_get_context_string_empty(self, manager):
        assert manager.get_context_string("random") == ""

    def test_get_context_string_with_matches(self, manager):
        manager.add_memory("user prefers dark theme")
        ctx = manager.get_context_string("dark theme")
        assert "dark theme" in ctx
        assert "[User Memory" in ctx


# ═══════════════════════════════════════════════════════════════════════════════
# token_tracker (module-level functions)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestTokenTracker:
    """Tests for web.token_tracker module functions."""

    @pytest.fixture(autouse=True)
    def _reset_module_state(self):
        """Reset module-level globals before each test and prevent disk I/O."""
        import web.token_tracker as tt
        tt._data = {}
        tt._dirty = False
        with patch.object(tt, "_load"), patch.object(tt, "_save_if_dirty"):
            yield
        tt._data = {}
        tt._dirty = False

    # -- _normalize_model ------------------------------------------------------

    def test_normalize_model_strips_prefix(self):
        from web.token_tracker import _normalize_model
        assert _normalize_model("models/gemini-2.5-pro") == "gemini-2.5-pro"

    def test_normalize_model_lowercases(self):
        from web.token_tracker import _normalize_model
        assert _normalize_model("Gemini-2.5-Pro") == "gemini-2.5-pro"

    def test_normalize_model_strips_whitespace(self):
        from web.token_tracker import _normalize_model
        assert _normalize_model("  gemini-3-flash  ") == "gemini-3-flash"

    # -- _get_price / _calc_cost -----------------------------------------------

    def test_get_price_known_model(self):
        from web.token_tracker import _get_price
        price = _get_price("gemini-2.5-pro")
        assert price["input"] == 1.25
        assert price["output"] == 10.00

    def test_get_price_falls_back_to_default(self):
        from web.token_tracker import _get_price, _PRICING
        price = _get_price("totally-unknown-model-xyz")
        assert price == _PRICING["default"]

    def test_calc_cost(self):
        from web.token_tracker import _calc_cost
        cost = _calc_cost("gemini-2.5-flash", 1_000_000, 1_000_000)
        expected = 0.075 + 0.30  # input + output per 1M
        assert abs(cost - expected) < 0.001

    # -- record_usage ----------------------------------------------------------

    def test_record_usage_skips_zero_tokens(self):
        import web.token_tracker as tt
        with patch.object(tt, "_load"):
            tt.record_usage("gemini-2.5-pro", 0, 0)
        assert tt._dirty is False

    def test_record_usage_updates_daily_and_monthly(self):
        import web.token_tracker as tt
        today = date.today().isoformat()
        month = today[:7]

        with patch.object(tt, "_save_if_dirty"):
            tt.record_usage("gemini-2.5-flash", 100, 50)

        assert today in tt._data["daily"]
        assert month in tt._data["monthly"]
        day_model = tt._data["daily"][today]["gemini-2.5-flash"]
        assert day_model["input"] == 100
        assert day_model["output"] == 50
        assert day_model["calls"] == 1

    def test_record_usage_accumulates(self):
        import web.token_tracker as tt
        today = date.today().isoformat()

        with patch.object(tt, "_save_if_dirty"):
            tt.record_usage("gemini-2.5-flash", 100, 50)
            tt.record_usage("gemini-2.5-flash", 200, 100)

        day_model = tt._data["daily"][today]["gemini-2.5-flash"]
        assert day_model["input"] == 300
        assert day_model["output"] == 150
        assert day_model["calls"] == 2

    # -- record_usage_with_skill -----------------------------------------------

    def test_record_usage_with_skill(self):
        import web.token_tracker as tt

        with patch.object(tt, "_save_if_dirty"):
            tt.record_usage_with_skill(
                "gemini-2.5-pro", 500, 200,
                skill_id="code_review", session_id="sess-001"
            )

        assert "code_review" in tt._data.get("skills", {})
        assert "sess-001" in tt._data.get("sessions", {})
        sess = tt._data["sessions"]["sess-001"]
        assert sess["total_tokens"] == 700
        assert sess["calls"] == 1
        assert sess["cost_cny"] > 0

    # -- get_stats / reset_stats -----------------------------------------------

    def test_get_stats_returns_structure(self):
        import web.token_tracker as tt
        with patch.object(tt, "_save_if_dirty"):
            tt.record_usage("gemini-2.5-flash", 100, 50)
        stats = tt.get_stats()
        assert "today" in stats
        assert "this_month" in stats
        assert "last_7_days" in stats
        assert stats["today"]["total"] == 150

    def test_reset_stats_all(self):
        import web.token_tracker as tt
        with patch.object(tt, "_save_if_dirty"):
            tt.record_usage("gemini-2.5-flash", 100, 50)
            result = tt.reset_stats("all")
        assert result["success"] is True
        assert tt._data.get("daily") == {}

    def test_reset_stats_today(self):
        import web.token_tracker as tt
        today = date.today().isoformat()
        with patch.object(tt, "_save_if_dirty"):
            tt.record_usage("gemini-2.5-flash", 100, 50)
            tt.reset_stats("today")
        assert today not in tt._data.get("daily", {})

    # -- _aggregate_period / _last_n_days (internal) ---------------------------

    def test_aggregate_period_empty(self):
        from web.token_tracker import _aggregate_period
        agg = _aggregate_period({})
        assert agg["total"] == 0
        assert agg["calls"] == 0
        assert agg["cost_usd"] == 0

    def test_last_n_days_length(self):
        import web.token_tracker as tt
        tt._data = tt._empty_data()
        from web.token_tracker import _last_n_days
        result = _last_n_days(7)
        assert len(result) == 7


# ═══════════════════════════════════════════════════════════════════════════════
# SystemInfoCollector
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSystemInfoCollector:
    """Tests for web.system_info.SystemInfoCollector with mocked psutil."""

    @pytest.fixture()
    def collector(self):
        with patch("web.system_info.HAS_WMI", False):
            from web.system_info import SystemInfoCollector
            return SystemInfoCollector(cache_timeout=5.0)

    # -- caching ---------------------------------------------------------------

    def test_cache_set_and_get(self, collector):
        collector._set_cached("test_key", {"value": 42})
        assert collector._get_cached("test_key") == {"value": 42}

    def test_cache_miss_returns_none(self, collector):
        assert collector._get_cached("missing") is None

    def test_cache_expires(self, collector):
        collector.cache_timeout = 0.0  # expire immediately
        collector._set_cached("test_key", {"value": 42})
        assert collector._get_cached("test_key") is None

    def test_cache_custom_ttl(self, collector):
        collector._set_cached("key", "data")
        # With a very short TTL it should expire
        assert collector._get_cached("key", ttl=0.0) is None
        # But with a large TTL it should still be there
        collector._set_cached("key2", "data2")
        assert collector._get_cached("key2", ttl=999) == "data2"

    # -- get_cpu_info ----------------------------------------------------------

    def test_get_cpu_info(self, collector):
        mock_freq = MagicMock()
        mock_freq.current = 3600.0
        with patch("web.system_info.psutil.cpu_percent", return_value=25.5), \
             patch("web.system_info.psutil.cpu_count", side_effect=[4, 8]), \
             patch("web.system_info.platform.processor", return_value="Intel i7"), \
             patch("web.system_info.psutil.cpu_freq", return_value=mock_freq):
            info = collector.get_cpu_info()
        assert info["usage_percent"] == 25.5
        assert info["physical_cores"] == 4
        assert info["logical_cores"] == 8
        assert info["model"] == "Intel i7"
        assert info["frequency_mhz"] == 3600.0

    def test_get_cpu_info_error_fallback(self, collector):
        with patch("web.system_info.psutil.cpu_percent", side_effect=RuntimeError("fail")):
            info = collector.get_cpu_info()
        assert info["usage_percent"] == 0
        assert "error" in info

    # -- get_memory_info -------------------------------------------------------

    def test_get_memory_info(self, collector):
        mock_mem = MagicMock()
        mock_mem.total = 16 * (1024 ** 3)
        mock_mem.used = 8 * (1024 ** 3)
        mock_mem.available = 8 * (1024 ** 3)
        mock_mem.percent = 50.0

        mock_swap = MagicMock()
        mock_swap.total = 4 * (1024 ** 3)
        mock_swap.used = 1 * (1024 ** 3)
        mock_swap.percent = 25.0

        with patch("web.system_info.psutil.virtual_memory", return_value=mock_mem), \
             patch("web.system_info.psutil.swap_memory", return_value=mock_swap):
            info = collector.get_memory_info()
        assert info["total_gb"] == 16.0
        assert info["used_gb"] == 8.0
        assert info["percent"] == 50.0
        assert info["swap_total_gb"] == 4.0

    def test_get_memory_info_error_fallback(self, collector):
        with patch("web.system_info.psutil.virtual_memory", side_effect=OSError("fail")):
            info = collector.get_memory_info()
        assert info["total_gb"] == 0
        assert "error" in info

    # -- get_disk_info ---------------------------------------------------------

    def test_get_disk_info(self, collector):
        mock_partition = MagicMock()
        mock_partition.device = "C:\\"
        mock_partition.mountpoint = "C:\\"
        mock_partition.fstype = "NTFS"

        mock_usage = MagicMock()
        mock_usage.total = 500 * (1024 ** 3)
        mock_usage.used = 200 * (1024 ** 3)
        mock_usage.free = 300 * (1024 ** 3)
        mock_usage.percent = 40.0

        with patch("web.system_info.psutil.disk_partitions", return_value=[mock_partition]), \
             patch("web.system_info.psutil.disk_usage", return_value=mock_usage):
            info = collector.get_disk_info()
        assert "C:" in info["drives"]
        assert info["total_gb"] == 500.0
        assert info["free_gb"] == 300.0

    def test_get_disk_info_skips_empty_fstype(self, collector):
        mock_p = MagicMock()
        mock_p.fstype = ""
        mock_p.device = "X:\\"
        with patch("web.system_info.psutil.disk_partitions", return_value=[mock_p]):
            info = collector.get_disk_info()
        assert info["drives"] == {}

    def test_get_disk_info_error_fallback(self, collector):
        with patch("web.system_info.psutil.disk_partitions", side_effect=RuntimeError("fail")):
            info = collector.get_disk_info()
        assert info["drives"] == {}
        assert "error" in info

    # -- get_network_info ------------------------------------------------------

    def test_get_network_info(self, collector):
        mock_addr = MagicMock()
        mock_addr.family = 2  # AF_INET
        mock_addr.address = "192.168.1.100"
        with patch("web.system_info.socket.gethostname", return_value="test-host"), \
             patch("web.system_info.psutil.net_if_addrs", return_value={"eth0": [mock_addr]}), \
             patch("web.system_info.psutil.net_if_stats", return_value={}):
            info = collector.get_network_info()
        assert info["hostname"] == "test-host"
        assert "eth0" in info["interfaces"]

    # -- get_system_warnings ---------------------------------------------------

    def test_warnings_high_cpu(self, collector):
        collector._set_cached("cpu_info", {"usage_percent": 95, "physical_cores": 4,
                                           "logical_cores": 8, "model": "x",
                                           "frequency_mhz": 3000, "load_average": (0, 0, 0)})
        collector._set_cached("memory_info", {"percent": 50, "total_gb": 16,
                                              "used_gb": 8, "available_gb": 8,
                                              "swap_total_gb": 0, "swap_used_gb": 0,
                                              "swap_percent": 0})
        collector._set_cached("disk_info", {"drives": {}, "total_gb": 500,
                                            "free_gb": 400, "percent_full": 20})
        warnings = collector.get_system_warnings()
        assert any("CPU" in w for w in warnings)

    def test_no_warnings_normal_state(self, collector):
        collector._set_cached("cpu_info", {"usage_percent": 20, "physical_cores": 4,
                                           "logical_cores": 8, "model": "x",
                                           "frequency_mhz": 3000, "load_average": (0, 0, 0)})
        collector._set_cached("memory_info", {"percent": 40, "total_gb": 16,
                                              "used_gb": 6, "available_gb": 10,
                                              "swap_total_gb": 0, "swap_used_gb": 0,
                                              "swap_percent": 0})
        collector._set_cached("disk_info", {"drives": {}, "total_gb": 500,
                                            "free_gb": 400, "percent_full": 20})
        warnings = collector.get_system_warnings()
        assert len(warnings) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# WorkFileLibrary
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestWorkFileLibrary:
    """Tests for web.work_file_library.WorkFileLibrary with in-memory SQLite."""

    @pytest.fixture()
    def library(self, tmp_path):
        """Create a WorkFileLibrary using a temp DB path."""
        from web.work_file_library import WorkFileLibrary
        with patch.object(WorkFileLibrary, "_resolve_db_path",
                          return_value=str(tmp_path / "test.db")):
            lib = WorkFileLibrary()
        return lib

    # -- init / db -------------------------------------------------------------

    def test_init_creates_db(self, library, tmp_path):
        db_path = library._db_path
        assert os.path.exists(db_path)

    def test_tables_exist(self, library):
        with library._conn() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            names = {r["name"] for r in tables}
        assert "work_files" in names
        assert "watch_folders" in names
        assert "meta" in names

    # -- count / is_indexed ----------------------------------------------------

    def test_count_empty(self, library):
        assert library.count() == 0

    def test_is_indexed_false_when_empty(self, library):
        assert library.is_indexed() is False

    def test_count_after_insert(self, library):
        now = time.time()
        rows = [
            ("/tmp/test.docx", "test.docx", "test.docx", ".docx", "Word文档", 1024, now, now),
        ]
        library._batch_upsert(rows)
        assert library.count() == 1
        assert library.is_indexed() is True

    # -- batch_upsert ----------------------------------------------------------

    def test_batch_upsert_multiple(self, library):
        now = time.time()
        rows = [
            (f"/tmp/file{i}.xlsx", f"file{i}.xlsx", f"file{i}.xlsx", ".xlsx",
             "Excel表格", 2048, now, now)
            for i in range(5)
        ]
        library._batch_upsert(rows)
        assert library.count() == 5

    def test_batch_upsert_replaces_on_duplicate(self, library):
        now = time.time()
        rows = [("/tmp/f.docx", "f.docx", "f.docx", ".docx", "Word文档", 100, now, now)]
        library._batch_upsert(rows)
        rows2 = [("/tmp/f.docx", "f.docx", "f.docx", ".docx", "Word文档", 999, now, now)]
        library._batch_upsert(rows2)
        assert library.count() == 1
        with library._conn() as conn:
            row = conn.execute("SELECT size FROM work_files WHERE path = '/tmp/f.docx'").fetchone()
        assert row["size"] == 999

    # -- search ----------------------------------------------------------------

    def test_search_empty_query(self, library):
        assert library.search("") == []
        assert library.search("   ") == []

    def test_search_finds_match(self, library):
        now = time.time()
        rows = [
            ("/tmp/report.docx", "report.docx", "report.docx", ".docx", "Word文档", 1024, now, now),
            ("/tmp/budget.xlsx", "budget.xlsx", "budget.xlsx", ".xlsx", "Excel表格", 2048, now, now),
        ]
        library._batch_upsert(rows)

        with patch("web.work_file_library.os.path.exists", return_value=True):
            results = library.search("report")
        assert len(results) == 1
        assert results[0]["name"] == "report.docx"

    def test_search_with_category_filter(self, library):
        now = time.time()
        rows = [
            ("/tmp/a.docx", "a.docx", "a.docx", ".docx", "Word文档", 100, now, now),
            ("/tmp/a.xlsx", "a.xlsx", "a.xlsx", ".xlsx", "Excel表格", 100, now, now),
        ]
        library._batch_upsert(rows)
        with patch("web.work_file_library.os.path.exists", return_value=True):
            results = library.search("a", category="Excel表格")
        assert all(r["category"] == "Excel表格" for r in results)

    def test_search_multi_keyword(self, library):
        now = time.time()
        rows = [
            ("/tmp/sales_report_2024.xlsx", "sales_report_2024.xlsx",
             "sales_report_2024.xlsx", ".xlsx", "Excel表格", 1024, now, now),
        ]
        library._batch_upsert(rows)
        with patch("web.work_file_library.os.path.exists", return_value=True):
            results = library.search("sales 2024")
        assert len(results) == 1

    # -- watch folders ---------------------------------------------------------

    def test_add_watch_folder(self, library, tmp_path):
        folder = str(tmp_path / "watched")
        os.makedirs(folder, exist_ok=True)
        assert library.add_watch_folder(folder) is True
        folders = library.list_watch_folders()
        assert len(folders) == 1

    def test_add_watch_folder_nonexistent(self, library):
        assert library.add_watch_folder("/nonexistent/path/xyz") is False

    def test_remove_watch_folder(self, library, tmp_path):
        folder = str(tmp_path / "watched")
        os.makedirs(folder, exist_ok=True)
        library.add_watch_folder(folder)
        assert library.remove_watch_folder(folder) is True
        assert len(library.list_watch_folders()) == 0

    # -- get_stats / get_scan_status -------------------------------------------

    def test_get_stats_empty(self, library):
        stats = library.get_stats()
        assert stats["total"] == 0
        assert stats["categories"] == {}

    def test_get_scan_status_default(self, library):
        status = library.get_scan_status()
        assert status["running"] is False
        assert status["done"] is False

    # -- get_by_category -------------------------------------------------------

    def test_get_by_category(self, library):
        now = time.time()
        rows = [
            ("/tmp/x.pdf", "x.pdf", "x.pdf", ".pdf", "PDF文档", 100, now, now),
            ("/tmp/y.pdf", "y.pdf", "y.pdf", ".pdf", "PDF文档", 200, now, now),
            ("/tmp/z.docx", "z.docx", "z.docx", ".docx", "Word文档", 300, now, now),
        ]
        library._batch_upsert(rows)
        with patch("web.work_file_library.os.path.exists", return_value=True):
            results = library.get_by_category("PDF文档")
        assert len(results) == 2
        assert all(r["category"] == "PDF文档" for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# work_file_library helpers
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestWorkFileLibraryHelpers:
    """Tests for module-level helper functions in work_file_library."""

    def test_human_size_bytes(self):
        from web.work_file_library import _human_size
        assert _human_size(500) == "500 B"

    def test_human_size_kb(self):
        from web.work_file_library import _human_size
        assert "KB" in _human_size(2048)

    def test_human_size_mb(self):
        from web.work_file_library import _human_size
        assert "MB" in _human_size(5_000_000)

    def test_human_time(self):
        from web.work_file_library import _human_time
        result = _human_time(1700000000.0)
        assert len(result) > 0
        assert "-" in result  # date format

    def test_detect_category_word(self):
        from web.work_file_library import detect_category_from_input
        assert detect_category_from_input("find my word document") == "Word文档"
        assert detect_category_from_input("open docx file") == "Word文档"

    def test_detect_category_excel(self):
        from web.work_file_library import detect_category_from_input
        assert detect_category_from_input("where is my excel sheet") == "Excel表格"
        assert detect_category_from_input("open xlsx") == "Excel表格"

    def test_detect_category_ppt(self):
        from web.work_file_library import detect_category_from_input
        assert detect_category_from_input("find ppt file") == "PPT演示"

    def test_detect_category_pdf(self):
        from web.work_file_library import detect_category_from_input
        assert detect_category_from_input("open pdf") == "PDF文档"

    def test_detect_category_none(self):
        from web.work_file_library import detect_category_from_input
        assert detect_category_from_input("what's the weather") is None
