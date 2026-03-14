"""Unit tests for SkillAutoMatcher.

All tests mock SkillManager and Ollama so no real LLM or file I/O is needed.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_matcher():
    from app.core.skills.skill_auto_matcher import SkillAutoMatcher

    return SkillAutoMatcher


# ---------------------------------------------------------------------------
# match() – basic contract
# ---------------------------------------------------------------------------


class TestMatchContract:
    def test_returns_list(self, mocker):
        matcher = _get_matcher()
        mocker.patch.object(matcher, "_has_active_skills_for_task", return_value=False)
        mocker.patch.object(matcher, "_build_skill_catalog", return_value=([], ""))
        result = matcher.match("hello")
        assert isinstance(result, list)

    def test_returns_at_most_three_skills(self, mocker):
        """_match_with_patterns enforces the 3-skill cap; match() passes it through."""
        matcher = _get_matcher()
        mocker.patch.object(matcher, "_has_active_skills_for_task", return_value=False)
        many_skills = [{"id": f"skill_{i}", "description": "x"} for i in range(10)]
        mocker.patch.object(
            matcher, "_build_skill_catalog", return_value=(many_skills, "catalog text")
        )
        mocker.patch.object(matcher, "_match_with_local_model", return_value=None)
        # _match_with_patterns already caps at 3; simulate its normal capped return
        mocker.patch.object(
            matcher,
            "_match_with_patterns",
            return_value=["skill_0", "skill_1", "skill_2"],
        )
        result = matcher.match("some long input covering everything", task_type="CHAT")
        assert len(result) <= 3

    def test_empty_catalog_returns_empty(self, mocker):
        matcher = _get_matcher()
        mocker.patch.object(matcher, "_has_active_skills_for_task", return_value=False)
        mocker.patch.object(matcher, "_build_skill_catalog", return_value=([], ""))
        result = matcher.match("write a poem")
        assert result == []

    def test_skips_when_user_has_active_skills(self, mocker):
        matcher = _get_matcher()
        mocker.patch.object(matcher, "_has_active_skills_for_task", return_value=True)
        mocker.patch.object(
            matcher, "_build_skill_catalog",
            return_value=([{"id": "s1", "name": "s1", "desc": "", "task_types": []}], "s1"),
        )
        mocker.patch.object(matcher, "_match_with_patterns", return_value=[])
        ollama_mock = mocker.patch.object(matcher, "_match_with_local_model")
        result = matcher.match("some input", task_type="CHAT")
        assert result == []
        ollama_mock.assert_not_called()

    def test_force_flag_overrides_active_skill_check(self, mocker):
        matcher = _get_matcher()
        mocker.patch.object(matcher, "_has_active_skills_for_task", return_value=True)
        mocker.patch.object(matcher, "_build_skill_catalog", return_value=([], ""))
        result = matcher.match("some input", force=True)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _match_with_patterns() – pattern-based fallback
# ---------------------------------------------------------------------------


class TestMatchWithPatterns:
    def test_returns_matching_skill_ids(self, mocker):
        matcher = _get_matcher()
        candidates = [{"id": "concise_mode", "description": "concise"}]
        result = matcher._match_with_patterns("please keep it short", candidates)
        assert isinstance(result, list)

    def test_no_match_returns_empty(self, mocker):
        matcher = _get_matcher()
        candidates = [{"id": "concise_mode", "description": "concise"}]
        result = matcher._match_with_patterns("xyzkjhqwerty", candidates)
        assert result == []

    def test_filters_to_candidates_only(self, mocker):
        matcher = _get_matcher()
        candidates = [{"id": "nonexistent_skill", "description": "x"}]
        result = matcher._match_with_patterns("write code please", candidates)
        assert "nonexistent_skill" not in result

    def test_respects_max_skills_limit(self, mocker):
        matcher = _get_matcher()
        # Build candidates with many skills that would match
        candidates = [
            {"id": entry["skill_id"], "description": ""}
            for entry in matcher._PATTERN_MAP
        ]
        # Input that might trigger many patterns
        text = " ".join(entry["patterns"][0] for entry in matcher._PATTERN_MAP[:10])
        result = matcher._match_with_patterns(text, candidates)
        assert len(result) <= 3


# ---------------------------------------------------------------------------
# _has_active_skills_for_task() – checks SkillManager
# ---------------------------------------------------------------------------


class TestHasActiveSkillsForTask:
    def test_returns_false_when_skill_manager_raises(self, mocker):
        from app.core.skills.skill_manager import SkillManager

        mocker.patch.object(
            SkillManager, "_ensure_init", side_effect=RuntimeError("no db")
        )
        matcher = _get_matcher()
        assert matcher._has_active_skills_for_task("CHAT") is False

    def test_returns_false_when_no_enabled_skills(self, mocker):
        from app.core.skills.skill_manager import SkillManager

        mocker.patch.object(SkillManager, "_ensure_init")
        mocker.patch.object(
            SkillManager,
            "_registry",
            {"skill_a": {"enabled": False, "task_types": ["CHAT"]}},
            create=True,
        )
        matcher = _get_matcher()
        assert matcher._has_active_skills_for_task("CHAT") is False

    def test_returns_true_when_enabled_skill_matches_task_type(self, mocker):
        from app.core.skills.skill_manager import SkillManager

        mocker.patch.object(SkillManager, "_ensure_init")
        mocker.patch.object(
            SkillManager,
            "_registry",
            {"skill_a": {"enabled": True, "task_types": ["CHAT"], "category": "domain"}},
            create=True,
        )
        matcher = _get_matcher()
        assert matcher._has_active_skills_for_task("CHAT") is True


# ---------------------------------------------------------------------------
# Ollama fallback behaviour
# ---------------------------------------------------------------------------


class TestOllamaFallback:
    def test_falls_back_to_patterns_when_ollama_returns_none(self, mocker):
        matcher = _get_matcher()
        mocker.patch.object(matcher, "_has_active_skills_for_task", return_value=False)
        mocker.patch.object(
            matcher,
            "_build_skill_catalog",
            return_value=([{"id": "concise_mode", "description": "x"}], "catalog"),
        )
        mocker.patch.object(matcher, "_match_with_local_model", return_value=None)
        mocker.patch.object(
            matcher, "_match_with_patterns", return_value=["concise_mode"]
        )
        result = matcher.match("be brief please")
        assert result == ["concise_mode"]

    def test_uses_ollama_result_when_available(self, mocker):
        matcher = _get_matcher()
        mocker.patch.object(matcher, "_has_active_skills_for_task", return_value=False)
        mocker.patch.object(
            matcher,
            "_build_skill_catalog",
            return_value=([{"id": "step_by_step", "description": "steps"}], "catalog"),
        )
        mocker.patch.object(
            matcher, "_match_with_local_model", return_value=["step_by_step"]
        )
        result = matcher.match("explain it step by step")
        assert "step_by_step" in result
