# -*- coding: utf-8 -*-
"""Unit tests for SkillToolAdapter."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.core.skills.skill_tool_adapter import SkillToolAdapter

# Patch targets — both are local imports inside methods, so patch at source.
_CAP_REG = "app.core.skills.skill_capability.SkillCapabilityRegistry"
_SKILL_MGR = "app.core.skills.skill_manager.SkillManager"


# ---------------------------------------------------------------------------
# Helper — lightweight mock SkillDefinition
# ---------------------------------------------------------------------------


def _make_skill(
    skill_id: str = "test_skill",
    name: str = "测试技能",
    description: str = "A test skill",
    icon: str = "🧪",
    enabled: bool = True,
    task_types: Optional[List[str]] = None,
    intent_description: str = "",
    when_not_to_use: str = "",
    mcp_tool: Optional[Dict] = None,
    render_result: str = "rendered prompt",
) -> MagicMock:
    sd = MagicMock()
    sd.id = skill_id
    sd.name = name
    sd.description = description
    sd.icon = icon
    sd.enabled = enabled
    sd.task_types = task_types if task_types is not None else []
    sd.intent_description = intent_description
    sd.when_not_to_use = when_not_to_use

    if mcp_tool is None:
        mcp_tool = {
            "name": skill_id,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "查询内容"},
                },
                "required": ["query"],
            },
        }
    sd.to_mcp_tool.return_value = mcp_tool
    sd.render_prompt.return_value = render_result
    return sd


# ===================================================================
# 1. Class-level attributes
# ===================================================================


class TestClassAttributes:
    def test_prefix_constant(self):
        assert SkillToolAdapter.PREFIX == "skill_"

    def test_has_register_all(self):
        assert callable(getattr(SkillToolAdapter, "register_all", None))

    def test_has_build_tool(self):
        assert callable(getattr(SkillToolAdapter, "_build_tool", None))


# ===================================================================
# 2. _build_tool — constructing the 4-tuple
# ===================================================================


class TestBuildTool:

    @patch(_CAP_REG)
    def test_tool_name_uses_prefix(self, cap):
        cap.has_capability.return_value = False
        name, *_ = SkillToolAdapter._build_tool(_make_skill(skill_id="my_skill"))
        assert name == "skill_my_skill"

    @patch(_CAP_REG)
    def test_description_contains_name_and_desc(self, cap):
        cap.has_capability.return_value = False
        sd = _make_skill(name="翻译技能", description="翻译用户文本", icon="🌐")
        _, _, desc, _ = SkillToolAdapter._build_tool(sd)
        assert "翻译技能" in desc
        assert "翻译用户文本" in desc
        assert "🌐" in desc

    @patch(_CAP_REG)
    def test_description_includes_intent(self, cap):
        cap.has_capability.return_value = False
        sd = _make_skill(intent_description="当用户需要翻译时使用")
        _, _, desc, _ = SkillToolAdapter._build_tool(sd)
        assert "使用时机" in desc
        assert "当用户需要翻译时使用" in desc

    @patch(_CAP_REG)
    def test_description_includes_when_not_to_use(self, cap):
        cap.has_capability.return_value = False
        sd = _make_skill(when_not_to_use="用户请求代码时")
        _, _, desc, _ = SkillToolAdapter._build_tool(sd)
        assert "不要在以下情况使用" in desc
        assert "用户请求代码时" in desc

    @patch(_CAP_REG)
    def test_description_omits_when_not_to_use_if_empty(self, cap):
        cap.has_capability.return_value = False
        _, _, desc, _ = SkillToolAdapter._build_tool(_make_skill(when_not_to_use=""))
        assert "不要在以下情况使用" not in desc

    @patch(_CAP_REG)
    def test_parameters_from_mcp_tool(self, cap):
        cap.has_capability.return_value = False
        sd = _make_skill(
            mcp_tool={
                "inputSchema": {
                    "type": "object",
                    "properties": {"lang": {"type": "string"}},
                    "required": ["lang"],
                }
            }
        )
        _, _, _, params = SkillToolAdapter._build_tool(sd)
        assert "lang" in params["properties"]
        assert "user_input" in params["properties"]
        assert "lang" in params.get("required", [])

    @patch(_CAP_REG)
    def test_user_input_added_when_missing(self, cap):
        cap.has_capability.return_value = False
        sd = _make_skill(
            mcp_tool={
                "inputSchema": {"type": "object", "properties": {}, "required": []}
            }
        )
        _, _, _, params = SkillToolAdapter._build_tool(sd)
        assert "user_input" in params["properties"]
        assert params["properties"]["user_input"]["type"] == "string"

    @patch(_CAP_REG)
    def test_user_input_not_duplicated(self, cap):
        cap.has_capability.return_value = False
        sd = _make_skill(
            mcp_tool={
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "user_input": {"type": "string", "description": "custom"},
                    },
                    "required": ["user_input"],
                }
            }
        )
        _, _, _, params = SkillToolAdapter._build_tool(sd)
        assert params["properties"]["user_input"]["description"] == "custom"

    @patch(_CAP_REG)
    def test_parameters_fallback_on_mcp_exception(self, cap):
        cap.has_capability.return_value = False
        sd = _make_skill()
        sd.to_mcp_tool.side_effect = RuntimeError("boom")
        _, _, _, params = SkillToolAdapter._build_tool(sd)
        assert "user_input" in params["properties"]
        assert "required" not in params

    @patch(_CAP_REG)
    def test_no_required_key_when_empty(self, cap):
        cap.has_capability.return_value = False
        sd = _make_skill(
            mcp_tool={
                "inputSchema": {"type": "object", "properties": {}, "required": []}
            }
        )
        _, _, _, params = SkillToolAdapter._build_tool(sd)
        assert "required" not in params

    @patch(_CAP_REG)
    def test_returned_callable(self, cap):
        cap.has_capability.return_value = False
        _, fn, _, _ = SkillToolAdapter._build_tool(_make_skill())
        assert callable(fn)

    @patch(_CAP_REG)
    def test_icon_missing_attribute_graceful(self, cap):
        """getattr(sd, 'icon', '') should not crash when attr is absent."""
        cap.has_capability.return_value = False
        sd = _make_skill()
        del sd.icon  # remove the attribute entirely
        _, _, desc, _ = SkillToolAdapter._build_tool(sd)
        assert sd.name in desc


# ===================================================================
# 3. Tool invocation routing — dispatch vs prompt guidance
# ===================================================================


class TestToolInvocation:

    @patch(_CAP_REG)
    def test_dispatch_called_when_capability_exists(self, cap):
        cap.has_capability.return_value = True
        cap.dispatch.return_value = "dispatch result"
        _, fn, _, _ = SkillToolAdapter._build_tool(_make_skill(skill_id="s"))
        result = fn(user_input="hello")
        cap.dispatch.assert_called_once_with("s", user_input="hello", context={})
        assert result == "dispatch result"

    @patch(_CAP_REG)
    def test_dispatch_extra_kwargs_passed_as_context(self, cap):
        cap.has_capability.return_value = True
        cap.dispatch.return_value = "ok"
        _, fn, _, _ = SkillToolAdapter._build_tool(_make_skill(skill_id="s"))
        fn(user_input="hi", lang="en", mode="fast")
        cap.dispatch.assert_called_once_with(
            "s", user_input="hi", context={"lang": "en", "mode": "fast"}
        )

    @patch(_CAP_REG)
    def test_dispatch_dict_result_json(self, cap):
        cap.has_capability.return_value = True
        cap.dispatch.return_value = {"key": "value"}
        _, fn, _, _ = SkillToolAdapter._build_tool(_make_skill())
        assert json.loads(fn(user_input="x")) == {"key": "value"}

    @patch(_CAP_REG)
    def test_dispatch_list_result_json(self, cap):
        cap.has_capability.return_value = True
        cap.dispatch.return_value = [1, 2, 3]
        _, fn, _, _ = SkillToolAdapter._build_tool(_make_skill())
        assert json.loads(fn(user_input="x")) == [1, 2, 3]

    @patch(_CAP_REG)
    def test_dispatch_non_serializable_falls_back_to_str(self, cap):
        cap.has_capability.return_value = True
        obj = object()
        cap.dispatch.return_value = obj
        _, fn, _, _ = SkillToolAdapter._build_tool(_make_skill())
        assert fn(user_input="x") == str(obj)

    @patch(_CAP_REG)
    def test_dispatch_none_falls_through_to_prompt(self, cap):
        cap.has_capability.return_value = True
        cap.dispatch.return_value = None
        sd = _make_skill(render_result="guidance text")
        _, fn, _, _ = SkillToolAdapter._build_tool(sd)
        result = fn(user_input="x")
        assert "已激活" in result
        assert "guidance text" in result

    @patch(_CAP_REG)
    def test_dispatch_exception_returns_error_json(self, cap):
        cap.has_capability.return_value = True
        cap.dispatch.side_effect = RuntimeError("kaboom")
        _, fn, _, _ = SkillToolAdapter._build_tool(_make_skill(skill_id="err"))
        parsed = json.loads(fn(user_input="x"))
        assert parsed["status"] == "error"
        assert parsed["skill"] == "err"
        assert "kaboom" in parsed["message"]
        assert "retry_hint" in parsed

    @patch(_CAP_REG)
    def test_prompt_guidance_no_capability(self, cap):
        cap.has_capability.return_value = False
        sd = _make_skill(
            skill_id="p",
            name="提示技能",
            icon="📝",
            render_result="Follow these instructions…",
        )
        _, fn, _, _ = SkillToolAdapter._build_tool(sd)
        result = fn(user_input="do something")
        assert "已激活" in result
        assert "Follow these instructions…" in result
        assert "do something" in result
        sd.render_prompt.assert_called_once()

    @patch(_CAP_REG)
    def test_prompt_empty_falls_back_to_short_message(self, cap):
        cap.has_capability.return_value = False
        sd = _make_skill(skill_id="empty", name="空技能", render_result="   ")
        _, fn, _, _ = SkillToolAdapter._build_tool(sd)
        result = fn(user_input="go")
        assert "empty" in result
        assert "已激活" in result

    @patch(_CAP_REG)
    def test_user_input_defaults_to_empty(self, cap):
        cap.has_capability.return_value = True
        cap.dispatch.return_value = "ok"
        _, fn, _, _ = SkillToolAdapter._build_tool(_make_skill(skill_id="noi"))
        fn()  # no user_input
        cap.dispatch.assert_called_once_with("noi", user_input="", context={})

    @patch(_CAP_REG)
    def test_user_input_none_treated_as_empty(self, cap):
        cap.has_capability.return_value = True
        cap.dispatch.return_value = "ok"
        _, fn, _, _ = SkillToolAdapter._build_tool(_make_skill(skill_id="ni"))
        fn(user_input=None)
        cap.dispatch.assert_called_once_with("ni", user_input="", context={})


# ===================================================================
# 4. register_all — orchestration
# ===================================================================


class TestRegisterAll:

    @patch(_CAP_REG)
    @patch(_SKILL_MGR)
    def test_registers_all_and_returns_count(self, mgr, cap):
        cap.has_capability.return_value = False
        mgr._def_registry = {
            "a": _make_skill(skill_id="a"),
            "b": _make_skill(skill_id="b"),
        }
        reg = MagicMock()
        assert SkillToolAdapter.register_all(reg) == 2
        assert reg.register_tool.call_count == 2

    @patch(_CAP_REG)
    @patch(_SKILL_MGR)
    def test_tool_name_in_register_call(self, mgr, cap):
        cap.has_capability.return_value = False
        mgr._def_registry = {"hello": _make_skill(skill_id="hello")}
        reg = MagicMock()
        SkillToolAdapter.register_all(reg)
        kwargs = reg.register_tool.call_args[1]
        assert kwargs["name"] == "skill_hello"

    @patch(_CAP_REG)
    @patch(_SKILL_MGR)
    def test_only_enabled_true(self, mgr, cap):
        cap.has_capability.return_value = False
        mgr._def_registry = {
            "on": _make_skill(skill_id="on", enabled=True),
            "off": _make_skill(skill_id="off", enabled=False),
        }
        reg = MagicMock()
        assert SkillToolAdapter.register_all(reg, only_enabled=True) == 1

    @patch(_CAP_REG)
    @patch(_SKILL_MGR)
    def test_only_enabled_false_registers_all(self, mgr, cap):
        cap.has_capability.return_value = False
        mgr._def_registry = {
            "on": _make_skill(skill_id="on", enabled=True),
            "off": _make_skill(skill_id="off", enabled=False),
        }
        reg = MagicMock()
        assert SkillToolAdapter.register_all(reg, only_enabled=False) == 2

    @patch(_CAP_REG)
    @patch(_SKILL_MGR)
    def test_task_type_filter_match(self, mgr, cap):
        cap.has_capability.return_value = False
        mgr._def_registry = {
            "m": _make_skill(skill_id="m", task_types=["chat", "code"]),
            "n": _make_skill(skill_id="n", task_types=["code"]),
        }
        reg = MagicMock()
        assert SkillToolAdapter.register_all(reg, task_type="chat") == 1

    @patch(_CAP_REG)
    @patch(_SKILL_MGR)
    def test_task_type_empty_list_not_filtered(self, mgr, cap):
        """Skills with empty task_types pass any task_type filter."""
        cap.has_capability.return_value = False
        mgr._def_registry = {
            "u": _make_skill(skill_id="u", task_types=[]),
        }
        reg = MagicMock()
        assert SkillToolAdapter.register_all(reg, task_type="anything") == 1

    @patch(_SKILL_MGR)
    def test_manager_init_failure_returns_zero(self, mgr):
        mgr._ensure_init.side_effect = RuntimeError("init fail")
        reg = MagicMock()
        assert SkillToolAdapter.register_all(reg) == 0
        reg.register_tool.assert_not_called()

    @patch(_SKILL_MGR)
    def test_def_registry_access_failure_returns_zero(self, mgr):
        mgr._def_registry.values.side_effect = RuntimeError("no reg")
        reg = MagicMock()
        assert SkillToolAdapter.register_all(reg) == 0

    @patch(_CAP_REG)
    @patch(_SKILL_MGR)
    def test_single_skill_build_failure_skipped(self, mgr, cap):
        cap.has_capability.return_value = False
        mgr._def_registry = {
            "good": _make_skill(skill_id="good"),
            "bad": _make_skill(skill_id="bad"),
        }
        reg = MagicMock()
        call_n = {"n": 0}

        def _side(**kw):
            call_n["n"] += 1
            if call_n["n"] == 1:
                raise RuntimeError("register failed")

        reg.register_tool.side_effect = _side
        count = SkillToolAdapter.register_all(reg)
        assert count == 1


# ===================================================================
# 5. Edge cases
# ===================================================================


class TestEdgeCases:

    @patch(_CAP_REG)
    @patch(_SKILL_MGR)
    def test_empty_skill_list(self, mgr, cap):
        mgr._def_registry = {}
        reg = MagicMock()
        assert SkillToolAdapter.register_all(reg) == 0
        reg.register_tool.assert_not_called()

    @patch(_CAP_REG)
    @patch(_SKILL_MGR)
    def test_default_params_registers_disabled(self, mgr, cap):
        """Default only_enabled=False → disabled skills ARE registered."""
        cap.has_capability.return_value = False
        mgr._def_registry = {"d": _make_skill(skill_id="d", enabled=False)}
        reg = MagicMock()
        assert SkillToolAdapter.register_all(reg) == 1

    @patch(_CAP_REG)
    def test_skill_with_empty_description(self, cap):
        cap.has_capability.return_value = False
        sd = _make_skill(description="")
        _, _, desc, _ = SkillToolAdapter._build_tool(sd)
        assert sd.name in desc

    @patch(_CAP_REG)
    def test_mcp_tool_missing_input_schema(self, cap):
        cap.has_capability.return_value = False
        sd = _make_skill(mcp_tool={"name": "x"})
        _, _, _, params = SkillToolAdapter._build_tool(sd)
        assert "user_input" in params["properties"]

    @patch(_CAP_REG)
    def test_mcp_tool_missing_properties(self, cap):
        cap.has_capability.return_value = False
        sd = _make_skill(mcp_tool={"inputSchema": {"type": "object"}})
        _, _, _, params = SkillToolAdapter._build_tool(sd)
        assert "user_input" in params["properties"]

    @patch(_CAP_REG)
    def test_dispatch_error_message_truncated(self, cap):
        cap.has_capability.return_value = True
        long_msg = "x" * 500
        cap.dispatch.side_effect = RuntimeError(long_msg)
        _, fn, _, _ = SkillToolAdapter._build_tool(_make_skill(skill_id="tr"))
        parsed = json.loads(fn(user_input="go"))
        assert "x" * 200 in parsed["message"]
        assert "x" * 201 not in parsed["message"]

    @patch(_CAP_REG)
    @patch(_SKILL_MGR)
    def test_task_type_no_filter_when_empty(self, mgr, cap):
        """task_type='' → no filtering at all."""
        cap.has_capability.return_value = False
        mgr._def_registry = {
            "a": _make_skill(skill_id="a", task_types=["code"]),
        }
        reg = MagicMock()
        assert SkillToolAdapter.register_all(reg, task_type="") == 1

    @patch(_CAP_REG)
    def test_description_no_intent_no_when_not(self, cap):
        """No intent_description and no when_not_to_use → clean description."""
        cap.has_capability.return_value = False
        sd = _make_skill(intent_description="", when_not_to_use="")
        _, _, desc, _ = SkillToolAdapter._build_tool(sd)
        assert "使用时机" not in desc
        assert "不要在以下情况使用" not in desc
