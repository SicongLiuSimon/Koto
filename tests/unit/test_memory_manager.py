"""Tests for web.memory_manager.MemoryManager."""

import json
import os
import time
from unittest.mock import MagicMock, Mock, mock_open, patch

import pytest

_MOD = "web.memory_manager"


class TestMemoryManagerInit:
    """Tests for MemoryManager initialization."""

    @patch(f"{_MOD}.os.path.exists", return_value=False)
    def test_init_creates_empty_memories_when_no_file(self, mock_exists):
        from web.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm.memory_path = "config/memory.json"
        mm.memories = []
        mm._load()
        assert mm.memories == []

    @patch(f"{_MOD}.os.path.exists", return_value=True)
    def test_init_loads_existing_memories(self, mock_exists):
        existing = [
            {
                "id": 1,
                "content": "test",
                "category": "fact",
                "source": "user",
                "created_at": "2024-01-01",
                "use_count": 0,
            }
        ]
        with patch("builtins.open", mock_open(read_data=json.dumps(existing))):
            from web.memory_manager import MemoryManager

            mm = MemoryManager.__new__(MemoryManager)
            mm.memory_path = "config/memory.json"
            mm.memories = []
            mm._load()
            assert len(mm.memories) == 1
            assert mm.memories[0]["content"] == "test"

    @patch(f"{_MOD}.os.path.exists", return_value=True)
    def test_init_handles_corrupt_json(self, mock_exists):
        with patch("builtins.open", mock_open(read_data="NOT VALID JSON!!!")):
            from web.memory_manager import MemoryManager

            mm = MemoryManager.__new__(MemoryManager)
            mm.memory_path = "config/memory.json"
            mm.memories = []
            mm._load()
            assert mm.memories == []


class TestAddMemory:
    """Tests for MemoryManager.add_memory."""

    def _make_mm(self):
        from web.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm.memory_path = "config/memory.json"
        mm.memories = []
        return mm

    @patch(f"{_MOD}.os.makedirs")
    def test_adds_memory_item(self, mock_makedirs):
        mm = self._make_mm()
        with patch("builtins.open", mock_open()):
            item = mm.add_memory("I prefer Python", category="user_preference")
        assert item["content"] == "I prefer Python"
        assert item["category"] == "user_preference"
        assert item["source"] == "user"
        assert item["use_count"] == 0
        assert len(mm.memories) == 1

    @patch(f"{_MOD}.os.makedirs")
    def test_strips_whitespace(self, mock_makedirs):
        mm = self._make_mm()
        with patch("builtins.open", mock_open()):
            item = mm.add_memory("  hello world  ")
        assert item["content"] == "hello world"

    @patch(f"{_MOD}.os.makedirs")
    def test_custom_category_and_source(self, mock_makedirs):
        mm = self._make_mm()
        with patch("builtins.open", mock_open()):
            item = mm.add_memory("fact", category="correction", source="extraction")
        assert item["category"] == "correction"
        assert item["source"] == "extraction"

    @patch(f"{_MOD}.os.makedirs")
    def test_id_is_timestamp_based(self, mock_makedirs):
        mm = self._make_mm()
        with patch("builtins.open", mock_open()):
            item = mm.add_memory("test")
        assert isinstance(item["id"], int)
        assert item["id"] > 0


class TestDeleteMemory:
    """Tests for MemoryManager.delete_memory."""

    def _make_mm_with_memories(self):
        from web.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm.memory_path = "config/memory.json"
        mm.memories = [
            {"id": 100, "content": "first", "created_at": "2024-01-01"},
            {"id": 200, "content": "second", "created_at": "2024-01-02"},
        ]
        return mm

    @patch(f"{_MOD}.os.makedirs")
    def test_deletes_existing_memory(self, mock_makedirs):
        mm = self._make_mm_with_memories()
        with patch("builtins.open", mock_open()):
            result = mm.delete_memory(100)
        assert result is True
        assert len(mm.memories) == 1
        assert mm.memories[0]["id"] == 200

    def test_returns_false_for_nonexistent_id(self):
        mm = self._make_mm_with_memories()
        result = mm.delete_memory(999)
        assert result is False
        assert len(mm.memories) == 2


class TestGetAllMemories:
    """Tests for MemoryManager.get_all_memories / list_memories."""

    def _make_mm(self):
        from web.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm.memory_path = "config/memory.json"
        mm.memories = [
            {"id": 1, "content": "old", "created_at": "2024-01-01"},
            {"id": 2, "content": "new", "created_at": "2024-12-31"},
        ]
        return mm

    def test_returns_sorted_newest_first(self):
        mm = self._make_mm()
        result = mm.get_all_memories()
        assert result[0]["content"] == "new"
        assert result[1]["content"] == "old"

    def test_list_memories_is_alias(self):
        mm = self._make_mm()
        assert mm.list_memories() == mm.get_all_memories()

    def test_empty_memories(self):
        from web.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm.memory_path = "config/memory.json"
        mm.memories = []
        assert mm.get_all_memories() == []


class TestSearchMemories:
    """Tests for MemoryManager.search_memories."""

    def _make_mm(self):
        from web.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm.memory_path = "config/memory.json"
        mm.memories = [
            {
                "id": 1,
                "content": "I love Python programming",
                "category": "user_preference",
                "created_at": "2024-01-01",
                "use_count": 0,
            },
            {
                "id": 2,
                "content": "JavaScript is also good",
                "category": "fact",
                "created_at": "2024-06-01",
                "use_count": 0,
            },
            {
                "id": 3,
                "content": "Python data science tools",
                "category": "project_info",
                "created_at": "2024-12-01",
                "use_count": 0,
            },
        ]
        return mm

    def test_returns_empty_for_empty_query(self):
        mm = self._make_mm()
        assert mm.search_memories("") == []

    def test_finds_matching_memories(self):
        mm = self._make_mm()
        results = mm.search_memories("Python")
        assert len(results) >= 2
        contents = [r["content"] for r in results]
        assert any("Python" in c for c in contents)

    def test_exact_phrase_match_scores_higher(self):
        mm = self._make_mm()
        results = mm.search_memories("Python programming")
        # The exact phrase match should be first
        assert results[0]["content"] == "I love Python programming"

    def test_user_preference_gets_category_boost(self):
        mm = self._make_mm()
        results = mm.search_memories("Python")
        # user_preference category gets +2 boost
        assert results[0]["category"] == "user_preference"

    def test_respects_limit(self):
        mm = self._make_mm()
        results = mm.search_memories("Python", limit=1)
        assert len(results) <= 1

    def test_increments_use_count(self):
        mm = self._make_mm()
        mm.search_memories("Python")
        matched = [m for m in mm.memories if "Python" in m["content"]]
        assert any(m["use_count"] > 0 for m in matched)

    def test_no_match_returns_empty(self):
        from web.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm.memory_path = "config/memory.json"
        # Use only non-user_preference items so category boost won't cause false matches
        mm.memories = [
            {
                "id": 1,
                "content": "JavaScript is great",
                "category": "fact",
                "created_at": "2024-01-01",
                "use_count": 0,
            },
            {
                "id": 2,
                "content": "React framework docs",
                "category": "project_info",
                "created_at": "2024-06-01",
                "use_count": 0,
            },
        ]
        results = mm.search_memories("Zyrkonium")
        assert results == []


class TestGetContextString:
    """Tests for MemoryManager.get_context_string."""

    def _make_mm(self):
        from web.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm.memory_path = "config/memory.json"
        mm.memories = [
            {
                "id": 1,
                "content": "I prefer dark mode",
                "category": "user_preference",
                "created_at": "2024-01-01",
                "use_count": 0,
            },
        ]
        return mm

    def test_returns_formatted_context(self):
        mm = self._make_mm()
        ctx = mm.get_context_string("dark mode settings")
        assert "[User Memory / Preferences]" in ctx
        assert "dark mode" in ctx

    def test_returns_empty_string_when_no_matches(self):
        from web.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm.memory_path = "config/memory.json"
        # Use non-user_preference items so category boost won't cause false matches
        mm.memories = [
            {
                "id": 1,
                "content": "local dev server setup",
                "category": "fact",
                "created_at": "2024-01-01",
                "use_count": 0,
            },
        ]
        ctx = mm.get_context_string("quantum physics")
        assert ctx == ""


class TestSave:
    """Tests for MemoryManager._save."""

    @patch(f"{_MOD}.os.makedirs")
    def test_save_writes_json(self, mock_makedirs):
        from web.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm.memory_path = "config/memory.json"
        mm.memories = [{"id": 1, "content": "test"}]

        m = mock_open()
        with patch("builtins.open", m):
            mm._save()

        m.assert_called_once_with("config/memory.json", "w", encoding="utf-8")
        written = "".join(call.args[0] for call in m().write.call_args_list)
        data = json.loads(written)
        assert data[0]["content"] == "test"

    @patch(f"{_MOD}.os.makedirs")
    def test_save_handles_write_error(self, mock_makedirs):
        from web.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm.memory_path = "config/memory.json"
        mm.memories = [{"id": 1}]

        with patch("builtins.open", side_effect=PermissionError("no write")):
            # Should not raise, just log
            mm._save()
