"""Unit tests for MemoryRouter.

All memory manager interactions are mocked — no real DB or LLM needed.
"""

from __future__ import annotations

import pytest


def _get_router():
    from app.core.memory.memory_router import MemoryRouter

    return MemoryRouter


def _format_block(parts):
    from app.core.memory.memory_router import _format_block

    return _format_block(parts)


# ---------------------------------------------------------------------------
# _format_block helper
# ---------------------------------------------------------------------------


class TestFormatBlock:
    def test_empty_parts_returns_empty_string(self):
        assert _format_block([]) == ""

    def test_blank_only_parts_skips_empty_strings(self):
        # _format_block filters with `if p` — empty strings are excluded, whitespace-only are not
        assert _format_block([""]) == ""
        assert _format_block(["", ""]) == ""

    def test_non_empty_parts_wrapped_in_block(self):
        result = _format_block(["hello"])
        assert "hello" in result
        assert "🧠" in result

    def test_multiple_parts_joined(self):
        result = _format_block(["part1", "part2"])
        assert "part1" in result
        assert "part2" in result


# ---------------------------------------------------------------------------
# MemoryRouter.read — no manager
# ---------------------------------------------------------------------------


class TestReadNoManager:
    def test_returns_empty_when_manager_is_none(self):
        router = _get_router()
        result = router.read(
            query="test",
            session_name="s1",
            get_memory_fn=lambda: None,
        )
        assert result == ""

    def test_returns_empty_when_get_memory_fn_raises(self):
        router = _get_router()

        def bad_fn():
            raise RuntimeError("db down")

        result = router.read(query="hello", session_name="s", get_memory_fn=bad_fn)
        assert result == ""


# ---------------------------------------------------------------------------
# MemoryRouter.read — extra_context only
# ---------------------------------------------------------------------------


class TestReadExtraContext:
    def test_extra_context_included_in_output(self):
        router = _get_router()
        result = router.read(
            query="test",
            session_name="s",
            get_memory_fn=lambda: None,
            extra_context="previous context snippet",
        )
        assert "previous context snippet" in result

    def test_blank_extra_context_not_included(self):
        router = _get_router()
        result = router.read(
            query="test",
            session_name="s",
            get_memory_fn=lambda: None,
            extra_context="   ",
        )
        assert result == ""


# ---------------------------------------------------------------------------
# MemoryRouter.read — with mocked manager
# ---------------------------------------------------------------------------


class TestReadWithManager:
    def _make_manager(self, profile_str=None, memory_hits=None):
        """Build a minimal mock memory manager."""

        class FakeProfile:
            def to_context_string(self):
                return profile_str or ""

        class FakeManager:
            user_profile = FakeProfile()

            def search_memories(self, query, limit=5, boost_categories=None):
                return memory_hits or []

        return FakeManager()

    def test_profile_included_when_non_empty(self):
        router = _get_router()
        mgr = self._make_manager(profile_str="User speaks English")
        result = router.read(
            query="hi",
            session_name="s",
            get_memory_fn=lambda: mgr,
            include_profile=True,
        )
        assert "User speaks English" in result

    def test_profile_excluded_when_include_profile_false(self):
        router = _get_router()
        mgr = self._make_manager(profile_str="User speaks English")
        result = router.read(
            query="hi",
            session_name="s",
            get_memory_fn=lambda: mgr,
            include_profile=False,
        )
        assert "User speaks English" not in result

    def test_memory_hits_included(self):
        router = _get_router()
        mgr = self._make_manager(
            memory_hits=[
                {"category": "user_fact", "content": "User likes cats"},
            ]
        )
        result = router.read(
            query="tell me something", session_name="s", get_memory_fn=lambda: mgr
        )
        assert "User likes cats" in result

    def test_memory_hit_content_truncated_at_150_chars(self):
        router = _get_router()
        long_content = "x" * 200
        mgr = self._make_manager(
            memory_hits=[{"category": "user_fact", "content": long_content}]
        )
        result = router.read(query="q", session_name="s", get_memory_fn=lambda: mgr)
        # The truncated content should appear with ellipsis
        assert "x" * 150 in result
        assert "…" in result

    def test_empty_memory_hits_returns_empty(self):
        router = _get_router()
        mgr = self._make_manager(memory_hits=[])

        class EmptyProfileManager:
            class user_profile:
                @staticmethod
                def to_context_string():
                    return ""

            def search_memories(self, query, limit=5, boost_categories=None):
                return []

        result = router.read(
            query="q",
            session_name="s",
            get_memory_fn=lambda: EmptyProfileManager(),
        )
        assert result == ""

    def test_search_exception_does_not_crash(self):
        router = _get_router()

        class BrokenManager:
            class user_profile:
                @staticmethod
                def to_context_string():
                    return ""

            def search_memories(self, *a, **kw):
                raise RuntimeError("index corrupt")

        result = router.read(
            query="q", session_name="s", get_memory_fn=lambda: BrokenManager()
        )
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# task_type cube mapping
# ---------------------------------------------------------------------------


class TestTaskTypeCubeMap:
    def test_task_cube_map_has_expected_keys(self):
        from app.core.memory.memory_router import _TASK_CUBE_MAP

        expected = {
            "CHAT",
            "RESEARCH",
            "WEB_SEARCH",
            "CODER",
            "FILE_GEN",
            "AGENT",
            "MULTI_STEP",
        }
        assert expected.issubset(set(_TASK_CUBE_MAP.keys()))

    def test_chat_cube_includes_user_fact(self):
        from app.core.memory.memory_router import _TASK_CUBE_MAP

        assert "user_fact" in _TASK_CUBE_MAP["CHAT"]
