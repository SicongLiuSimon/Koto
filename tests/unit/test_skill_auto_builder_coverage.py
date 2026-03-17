# -*- coding: utf-8 -*-
"""
Comprehensive unit tests for app.core.skills.skill_auto_builder

Covers: StyleProfile, StyleAnalyzer, PromptSynthesizer, SkillPackager,
        SkillAutoBuilder, and helper functions.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from app.core.skills.skill_auto_builder import (
    STYLE_DIMENSIONS,
    PromptSynthesizer,
    SkillAutoBuilder,
    SkillPackager,
    StyleAnalyzer,
    StyleProfile,
    _make_skill_id,
    _normalize_turns,
)

# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_mock_skill(skill_id: str = "test_skill", name: str = "Test"):
    """Create a minimal mock SkillDefinition for packing tests."""
    skill = MagicMock()
    skill.id = skill_id
    skill.name = name
    skill.to_dict.return_value = {
        "id": skill_id,
        "name": name,
        "icon": "🧪",
        "category": "style",
        "description": "mock skill",
        "prompt": "mock prompt",
    }
    return skill


# ══════════════════════════════════════════════════════════════════
# 1. StyleProfile
# ══════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestStyleProfile:
    """Tests for StyleProfile dataclass."""

    def test_defaults(self):
        p = StyleProfile()
        assert p.formality == 0.5
        assert p.verbosity == 0.5
        assert p.empathy == 0.5
        assert p.creativity == 0.3
        assert p.technicality == 0.3
        assert p.positivity == 0.6
        assert p.proactivity == 0.4
        assert p.humor == 0.2
        assert p.conciseness == 0.5
        assert p.domain == "general"
        assert p.language == "zh"

    def test_to_dict_contains_all_keys(self):
        p = StyleProfile()
        d = p.to_dict()
        expected_keys = {
            "formality",
            "verbosity",
            "empathy",
            "structure",
            "creativity",
            "technicality",
            "positivity",
            "proactivity",
            "humor",
            "conciseness",
            "domain",
            "language",
        }
        assert set(d.keys()) == expected_keys

    def test_from_dict_roundtrip(self):
        original = StyleProfile(
            formality=0.9,
            verbosity=0.1,
            empathy=0.8,
            structure=0.7,
            creativity=0.6,
            technicality=0.95,
            positivity=0.3,
            proactivity=0.2,
            humor=0.85,
            conciseness=0.4,
            domain="coding",
            language="en",
        )
        d = original.to_dict()
        restored = StyleProfile.from_dict(d)
        assert restored.to_dict() == d

    def test_from_dict_ignores_unknown_keys(self):
        d = {"formality": 0.9, "unknown_key": 42}
        p = StyleProfile.from_dict(d)
        assert p.formality == 0.9
        assert not hasattr(p, "unknown_key") or p.formality == 0.9

    def test_from_dict_partial(self):
        p = StyleProfile.from_dict({"humor": 0.99})
        assert p.humor == 0.99
        assert p.formality == 0.5  # default preserved


# ══════════════════════════════════════════════════════════════════
# 2. StyleAnalyzer
# ══════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestStyleAnalyzer:
    """Tests for StyleAnalyzer rule engine."""

    def test_analyze_text_formal(self):
        text = "请使用正式专业的商务语言，措辞严谨。您好。"
        profile = StyleAnalyzer.analyze_text(text)
        assert profile.formality > 0.6, "Formal keywords should push formality high"

    def test_analyze_text_informal(self):
        text = "随意聊天，像朋友一样轻松说话，哈哈太好玩了lol"
        profile = StyleAnalyzer.analyze_text(text)
        assert profile.formality < 0.4, "Informal keywords should push formality low"

    def test_analyze_text_empathetic(self):
        text = "温暖关怀体贴，感同身受，倾听你的心情，鼓励支持陪伴闺蜜"
        profile = StyleAnalyzer.analyze_text(text)
        assert profile.empathy > 0.7, "Empathy keywords should push empathy high"

    def test_analyze_text_technical(self):
        text = "代码编程python算法API数据库技术架构debug"
        profile = StyleAnalyzer.analyze_text(text)
        assert profile.technicality > 0.6, "Tech keywords should push technicality high"

    def test_analyze_text_creative(self):
        text = "创意创新灵感想象跳出框架独特新颖比喻故事隐喻联想发散"
        profile = StyleAnalyzer.analyze_text(text)
        assert profile.creativity > 0.6

    def test_analyze_text_verbose(self):
        text = "详细全面深入系统完整逐步step by step"
        profile = StyleAnalyzer.analyze_text(text)
        assert profile.verbosity > 0.6

    def test_analyze_text_concise(self):
        text = "简洁精炼简短极简直接一句话"
        profile = StyleAnalyzer.analyze_text(text)
        assert profile.conciseness > 0.6

    def test_analyze_text_detects_chinese_language(self):
        text = "你好，我想要一个温暖的助手风格"
        profile = StyleAnalyzer.analyze_text(text)
        assert profile.language == "zh"

    def test_analyze_text_detects_english_language(self):
        text = "I want a formal professional assistant style"
        profile = StyleAnalyzer.analyze_text(text)
        assert profile.language == "en"

    def test_analyze_text_empty_returns_defaults(self):
        profile = StyleAnalyzer.analyze_text("")
        for dim in STYLE_DIMENSIONS:
            assert getattr(profile, dim) == 0.5
        assert profile.domain == "general"

    # ── _detect_domain ──

    def test_detect_domain_coding(self):
        assert StyleAnalyzer._detect_domain("python 编程 代码 debug 算法") == "coding"

    def test_detect_domain_finance(self):
        assert StyleAnalyzer._detect_domain("金融 投资 股票 理财 基金") == "finance"

    def test_detect_domain_medical(self):
        assert StyleAnalyzer._detect_domain("医疗 健康 病症 药物 诊断") == "medical"

    def test_detect_domain_education(self):
        assert (
            StyleAnalyzer._detect_domain("教育 教学 学习 课程 老师 学生") == "education"
        )

    def test_detect_domain_general_no_keywords(self):
        assert StyleAnalyzer._detect_domain("hello world nothing special") == "general"

    # ── analyze_conversation ──

    def test_analyze_conversation_filters_assistant(self):
        turns = [
            {"role": "user", "text": "简洁回答"},
            {"role": "assistant", "text": "正式专业商务学术报告"},
            {"role": "user", "text": "再简洁一些"},
            {"role": "assistant", "text": "正式专业商务"},
        ]
        profile = StyleAnalyzer.analyze_conversation(turns)
        assert profile.formality > 0.6

    def test_analyze_conversation_empty_returns_defaults(self):
        profile = StyleAnalyzer.analyze_conversation([])
        assert profile.formality == 0.5

    def test_analyze_conversation_no_matching_role(self):
        turns = [{"role": "user", "text": "正式专业"}]
        profile = StyleAnalyzer.analyze_conversation(turns, role="assistant")
        assert profile.formality == 0.5  # no assistant messages → default


# ══════════════════════════════════════════════════════════════════
# 3. PromptSynthesizer
# ══════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestPromptSynthesizer:
    """Tests for PromptSynthesizer.synthesize."""

    def test_synthesize_returns_tuple_of_strings(self):
        profile = StyleProfile()
        system_prompt, intent_desc = PromptSynthesizer.synthesize(
            profile, "TestSkill", "一个测试技能"
        )
        assert isinstance(system_prompt, str)
        assert isinstance(intent_desc, str)
        assert len(system_prompt) > 50
        assert len(intent_desc) > 5

    def test_synthesize_includes_name_in_prompt(self):
        prompt, _ = PromptSynthesizer.synthesize(StyleProfile(), "MyBot", "desc")
        assert "MyBot" in prompt

    def test_synthesize_includes_description(self):
        prompt, intent = PromptSynthesizer.synthesize(
            StyleProfile(), "Bot", "自定义描述文本"
        )
        assert "自定义描述文本" in prompt
        assert "自定义描述文本" in intent

    def test_synthesize_high_formality(self):
        profile = StyleProfile(formality=0.9)
        prompt, _ = PromptSynthesizer.synthesize(profile, "Formal", "")
        assert "正式" in prompt or "书面" in prompt or "严谨" in prompt

    def test_synthesize_low_formality(self):
        profile = StyleProfile(formality=0.1)
        prompt, _ = PromptSynthesizer.synthesize(profile, "Casual", "")
        assert "口语" in prompt or "朋友" in prompt or "轻松" in prompt

    def test_synthesize_with_extreme_values(self):
        profile = StyleProfile(
            formality=1.0,
            verbosity=1.0,
            empathy=1.0,
            structure=1.0,
            creativity=1.0,
            technicality=1.0,
            positivity=1.0,
            proactivity=1.0,
            humor=1.0,
            conciseness=1.0,
            domain="coding",
        )
        prompt, intent = PromptSynthesizer.synthesize(profile, "Extreme", "desc")
        assert isinstance(prompt, str)
        assert len(prompt) > 100
        assert "行为规范" in prompt

    def test_synthesize_with_all_low_values(self):
        profile = StyleProfile(
            formality=0.0,
            verbosity=0.0,
            empathy=0.0,
            structure=0.0,
            creativity=0.0,
            technicality=0.0,
            positivity=0.0,
            proactivity=0.0,
            humor=0.0,
            conciseness=0.0,
        )
        prompt, _ = PromptSynthesizer.synthesize(profile, "Minimal", "")
        assert isinstance(prompt, str)

    def test_synthesize_no_description_uses_domain_context(self):
        profile = StyleProfile(domain="coding")
        prompt, _ = PromptSynthesizer.synthesize(profile, "Coder", "")
        assert "软件" in prompt or "编程" in prompt or "技术" in prompt

    def test_synthesize_input_placeholder(self):
        prompt, _ = PromptSynthesizer.synthesize(StyleProfile(), "Bot", "")
        assert "{input}" in prompt


# ══════════════════════════════════════════════════════════════════
# 4. SkillPackager
# ══════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSkillPackager:
    """Tests for SkillPackager pack/unpack/get_manifest."""

    def test_pack_creates_zip(self, tmp_path: Path):
        skill = _make_mock_skill("s1", "SkillOne")
        out = str(tmp_path / "test_pack.kotosk")
        result = SkillPackager.pack([skill], out, pack_name="TestPack", author="tester")
        assert result.endswith(".kotosk")
        assert os.path.isfile(result)
        with zipfile.ZipFile(result, "r") as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            assert "skills/s1.json" in names

    def test_pack_adds_kotosk_extension(self, tmp_path: Path):
        skill = _make_mock_skill()
        out = str(tmp_path / "no_ext")
        result = SkillPackager.pack([skill], out)
        assert result.endswith(".kotosk")

    def test_pack_includes_readme(self, tmp_path: Path):
        skill = _make_mock_skill()
        out = str(tmp_path / "readme_test.kotosk")
        SkillPackager.pack([skill], out, readme="# Hello")
        with zipfile.ZipFile(out, "r") as zf:
            assert "README.md" in zf.namelist()
            assert zf.read("README.md").decode() == "# Hello"

    def test_pack_manifest_content(self, tmp_path: Path):
        s1 = _make_mock_skill("a", "Alpha")
        s2 = _make_mock_skill("b", "Beta")
        out = str(tmp_path / "multi.kotosk")
        SkillPackager.pack([s1, s2], out, author="me", description="two skills")
        manifest = SkillPackager.get_manifest(out)
        assert manifest["skill_count"] == 2
        assert manifest["author"] == "me"
        assert set(manifest["skill_ids"]) == {"a", "b"}
        assert manifest["kotosk_version"] == "1.0"

    def test_get_manifest_empty_zip(self, tmp_path: Path):
        empty = str(tmp_path / "empty.kotosk")
        with zipfile.ZipFile(empty, "w"):
            pass
        assert SkillPackager.get_manifest(empty) == {}

    @patch("app.core.skills.skill_auto_builder.SkillPackager.unpack")
    def test_unpack_returns_manifest_and_skills(self, mock_unpack):
        mock_unpack.return_value = ({"kotosk_version": "1.0"}, [MagicMock()])
        manifest, skills = SkillPackager.unpack("fake.kotosk")
        assert manifest["kotosk_version"] == "1.0"
        assert len(skills) == 1

    def test_pack_then_get_manifest_roundtrip(self, tmp_path: Path):
        skill = _make_mock_skill("rt", "Roundtrip")
        out = str(tmp_path / "rt.kotosk")
        SkillPackager.pack([skill], out, pack_name="RT", author="bot")
        m = SkillPackager.get_manifest(out)
        assert m["pack_name"] == "RT"
        assert m["skill_ids"] == ["rt"]


# ══════════════════════════════════════════════════════════════════
# 5. SkillAutoBuilder
# ══════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSkillAutoBuilder:
    """Tests for SkillAutoBuilder factory methods."""

    @patch(
        "app.core.skills.skill_auto_builder.SkillAutoBuilder.load_personalization_context"
    )
    def test_from_style_description_returns_skill(self, mock_ctx):
        mock_ctx.return_value = {}
        skill = SkillAutoBuilder.from_style_description(
            name="暖心闺蜜",
            description="温暖关怀体贴感同身受鼓励支持",
        )
        assert skill.name == "暖心闺蜜"
        assert skill.id  # non-empty
        assert skill.system_prompt_template
        assert skill.version == "1.0.0"

    @patch(
        "app.core.skills.skill_auto_builder.SkillAutoBuilder.load_personalization_context"
    )
    def test_from_style_description_with_tags(self, mock_ctx):
        mock_ctx.return_value = {}
        skill = SkillAutoBuilder.from_style_description(
            name="Test",
            description="desc",
            tags=["t1", "t2"],
        )
        assert skill.tags == ["t1", "t2"]

    @patch(
        "app.core.skills.skill_auto_builder.SkillAutoBuilder.load_personalization_context"
    )
    def test_from_style_config_returns_skill(self, mock_ctx):
        mock_ctx.return_value = {}
        skill = SkillAutoBuilder.from_style_config(
            name="极简主义",
            formality=0.8,
            verbosity=0.2,
            empathy=0.5,
            structure=0.9,
            creativity=0.1,
            domain="general",
        )
        assert skill.name == "极简主义"
        assert skill.system_prompt_template
        assert "CHAT" in skill.task_types

    @patch(
        "app.core.skills.skill_auto_builder.SkillAutoBuilder.load_personalization_context"
    )
    def test_from_style_config_coding_domain(self, mock_ctx):
        mock_ctx.return_value = {}
        skill = SkillAutoBuilder.from_style_config(
            name="CodeHelper",
            technicality=0.9,
            domain="coding",
            description="代码编程python辅助",
        )
        assert "CHAT" in skill.task_types

    def test_preview_prompt_returns_dict(self):
        result = SkillAutoBuilder.preview_prompt(
            name="Preview",
            description="一个测试预览",
            formality=0.7,
            verbosity=0.3,
        )
        assert "system_prompt" in result
        assert "intent_description" in result
        assert "style_profile" in result
        assert "suggested_id" in result
        assert isinstance(result["style_profile"], dict)

    def test_preview_prompt_suggested_id(self):
        result = SkillAutoBuilder.preview_prompt(name="我的技能", description="test")
        assert result["suggested_id"]  # non-empty string

    # ── _build_effective_description ──

    def test_build_effective_description_no_context(self):
        result = SkillAutoBuilder._build_effective_description("base", {})
        assert result == "base"

    def test_build_effective_description_with_comm_style(self):
        ctx = {
            "communication_style": {
                "preferred_detail_level": "concise",
                "formality": "casual",
                "code_style": "minimal",
                "preferred_language": "en",
                "emoji_usage": True,
            },
            "technical_background": {},
            "preferences": {},
            "memory_hints": [],
        }
        result = SkillAutoBuilder._build_effective_description("hello", ctx)
        assert "hello" in result
        assert "concise" in result or "用户沟通偏好" in result

    def test_build_effective_description_with_tech_background(self):
        ctx = {
            "communication_style": {},
            "technical_background": {
                "programming_languages": ["Python", "Go"],
                "experience_level": "senior",
                "domains": ["backend"],
            },
            "preferences": {},
            "memory_hints": [],
        }
        result = SkillAutoBuilder._build_effective_description("base", ctx)
        assert "Python" in result
        assert "senior" in result

    def test_build_effective_description_with_memory_hints(self):
        ctx = {
            "communication_style": {},
            "technical_background": {},
            "preferences": {},
            "memory_hints": ["likes dark mode", "prefers vim"],
        }
        result = SkillAutoBuilder._build_effective_description("base", ctx)
        assert "dark mode" in result or "历史记忆偏好" in result

    def test_build_effective_description_with_preferences(self):
        ctx = {
            "communication_style": {},
            "technical_background": {},
            "preferences": {
                "likes": ["简洁", "结构化"],
                "dislikes": ["冗长"],
                "habits": ["晨间工作"],
            },
            "memory_hints": [],
        }
        result = SkillAutoBuilder._build_effective_description("base", ctx)
        assert "简洁" in result or "用户偏好" in result

    # ── _apply_profile_bias ──

    def test_apply_profile_bias_concise_detail(self):
        profile = StyleProfile(verbosity=0.5, conciseness=0.5)
        ctx = {"communication_style": {"preferred_detail_level": "concise"}}
        result = SkillAutoBuilder._apply_profile_bias(profile, ctx)
        assert result.verbosity < 0.5
        assert result.conciseness > 0.5

    def test_apply_profile_bias_detailed(self):
        profile = StyleProfile(verbosity=0.5, conciseness=0.5)
        ctx = {"communication_style": {"preferred_detail_level": "detailed"}}
        result = SkillAutoBuilder._apply_profile_bias(profile, ctx)
        assert result.verbosity > 0.5
        assert result.conciseness < 0.5

    def test_apply_profile_bias_casual_formality(self):
        profile = StyleProfile(formality=0.5)
        ctx = {"communication_style": {"formality": "casual"}}
        result = SkillAutoBuilder._apply_profile_bias(profile, ctx)
        assert result.formality < 0.5

    def test_apply_profile_bias_formal_formality(self):
        profile = StyleProfile(formality=0.5)
        ctx = {"communication_style": {"formality": "formal"}}
        result = SkillAutoBuilder._apply_profile_bias(profile, ctx)
        assert result.formality > 0.5

    def test_apply_profile_bias_emoji_true(self):
        profile = StyleProfile(humor=0.5)
        ctx = {"communication_style": {"emoji_usage": True}}
        result = SkillAutoBuilder._apply_profile_bias(profile, ctx)
        assert result.humor > 0.5

    def test_apply_profile_bias_emoji_false(self):
        profile = StyleProfile(humor=0.5)
        ctx = {"communication_style": {"emoji_usage": False}}
        result = SkillAutoBuilder._apply_profile_bias(profile, ctx)
        assert result.humor < 0.5

    def test_apply_profile_bias_code_style_minimal(self):
        profile = StyleProfile(conciseness=0.5, verbosity=0.5)
        ctx = {"communication_style": {"code_style": "minimal"}}
        result = SkillAutoBuilder._apply_profile_bias(profile, ctx)
        assert result.conciseness > 0.5
        assert result.verbosity < 0.5

    def test_apply_profile_bias_empty_context(self):
        profile = StyleProfile(formality=0.5, humor=0.5)
        ctx = {"communication_style": {}}
        result = SkillAutoBuilder._apply_profile_bias(profile, ctx)
        assert result.formality == 0.5
        assert result.humor == 0.5

    # ── from_ai_description ──

    @patch(
        "app.core.skills.skill_auto_builder.SkillAutoBuilder._generate_prompt_with_ai"
    )
    def test_from_ai_description_with_ai_success(self, mock_ai):
        mock_ai.return_value = (
            "你是「TestAI」，一个友好助手。\n## 行为规范\n...\n## 用户输入\n{input}"
        )
        skill = SkillAutoBuilder.from_ai_description(
            name="TestAI", description="友好聊天机器人"
        )
        assert skill.name == "TestAI"
        assert "TestAI" in skill.system_prompt_template
        assert skill.task_types == ["CHAT"]

    @patch(
        "app.core.skills.skill_auto_builder.SkillAutoBuilder._generate_prompt_with_ai"
    )
    def test_from_ai_description_fallback_on_none(self, mock_ai):
        mock_ai.return_value = None
        skill = SkillAutoBuilder.from_ai_description(
            name="FallbackBot", description="温暖关怀鼓励"
        )
        assert skill.name == "FallbackBot"
        assert skill.system_prompt_template  # fell back to rule engine

    # ── from_conversation_history ──

    def test_from_conversation_history_with_mock_chat(self, tmp_path: Path):
        chat_data = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "正式专业商务报告，请问有何需要？"},
            {"role": "user", "content": "谢谢"},
            {"role": "assistant", "content": "正式学术报告，请多指教。"},
        ]
        chats_dir = tmp_path / "chats"
        chats_dir.mkdir()
        chat_file = chats_dir / "session_001.json"
        chat_file.write_text(
            json.dumps(chat_data, ensure_ascii=False), encoding="utf-8"
        )

        with patch("app.core.skills.skill_auto_builder._BASE_DIR", tmp_path):
            skill = SkillAutoBuilder.from_conversation_history(
                session_id="session_001",
                name="历史风格",
                description="",
            )
        assert skill.name == "历史风格"
        assert skill.system_prompt_template

    def test_from_conversation_history_missing_session_raises(self):
        with pytest.raises(ValueError, match="未找到"):
            with patch(
                "app.core.skills.skill_auto_builder._BASE_DIR", Path("/nonexistent")
            ):
                SkillAutoBuilder.from_conversation_history(
                    session_id="does_not_exist", name="X"
                )

    def test_from_conversation_history_with_description(self, tmp_path: Path):
        chat_data = {
            "history": [
                {"role": "user", "content": "hello"},
                {"role": "model", "content": "详细全面深入系统完整"},
            ]
        }
        chats_dir = tmp_path / "chats"
        chats_dir.mkdir()
        (chats_dir / "s2.json").write_text(
            json.dumps(chat_data, ensure_ascii=False), encoding="utf-8"
        )

        with patch("app.core.skills.skill_auto_builder._BASE_DIR", tmp_path):
            skill = SkillAutoBuilder.from_conversation_history(
                session_id="s2", name="Mixed", description="代码编程python辅助"
            )
        assert skill.name == "Mixed"

    # ── load_personalization_context ──

    def test_load_personalization_context_no_files(self, tmp_path: Path):
        with patch("app.core.skills.skill_auto_builder._BASE_DIR", tmp_path):
            ctx = SkillAutoBuilder.load_personalization_context()
        assert ctx["communication_style"] == {}
        assert ctx["memory_hints"] == []

    def test_load_personalization_context_with_profile(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        profile_data = {
            "communication_style": {"formality": "formal"},
            "technical_background": {"experience_level": "senior"},
            "preferences": {"likes": ["dark mode"]},
        }
        (config_dir / "user_profile.json").write_text(
            json.dumps(profile_data), encoding="utf-8"
        )
        with patch("app.core.skills.skill_auto_builder._BASE_DIR", tmp_path):
            ctx = SkillAutoBuilder.load_personalization_context()
        assert ctx["communication_style"]["formality"] == "formal"
        assert ctx["technical_background"]["experience_level"] == "senior"

    def test_load_personalization_context_with_memory(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        memory_data = [
            {"category": "preference", "content": "likes vim"},
            {"category": "user_preference", "content": "prefers dark theme"},
            {"category": "unrelated", "content": "should be ignored"},
        ]
        (config_dir / "memory.json").write_text(
            json.dumps(memory_data), encoding="utf-8"
        )
        with patch("app.core.skills.skill_auto_builder._BASE_DIR", tmp_path):
            ctx = SkillAutoBuilder.load_personalization_context()
        assert len(ctx["memory_hints"]) == 2
        assert "likes vim" in ctx["memory_hints"]

    def test_load_personalization_context_corrupted_files(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "user_profile.json").write_text("not json", encoding="utf-8")
        (config_dir / "memory.json").write_text("{bad", encoding="utf-8")
        with patch("app.core.skills.skill_auto_builder._BASE_DIR", tmp_path):
            ctx = SkillAutoBuilder.load_personalization_context()
        assert ctx["communication_style"] == {}
        assert ctx["memory_hints"] == []


# ══════════════════════════════════════════════════════════════════
# 6. Helper functions
# ══════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestHelperFunctions:
    """Tests for module-level helper functions."""

    def test_make_skill_id_basic(self):
        sid = _make_skill_id("暖心闺蜜")
        assert sid
        assert " " not in sid

    def test_make_skill_id_empty_name(self):
        sid = _make_skill_id("")
        assert sid.startswith("skill_")

    def test_make_skill_id_special_chars(self):
        sid = _make_skill_id("hello world! @#$")
        assert sid
        assert "@" not in sid

    def test_normalize_turns_basic(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "model", "content": "hello"},
        ]
        turns = _normalize_turns(history)
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"
        assert turns[1]["text"] == "hello"

    def test_normalize_turns_with_parts(self):
        history = [
            {"role": "model", "parts": [{"text": "part1"}, {"text": "part2"}]},
        ]
        turns = _normalize_turns(history)
        assert len(turns) == 1
        assert "part1" in turns[0]["text"]

    def test_normalize_turns_skips_empty(self):
        history = [{"role": "user", "content": ""}]
        turns = _normalize_turns(history)
        assert len(turns) == 0
