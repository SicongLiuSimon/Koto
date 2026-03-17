"""Unit tests for SkillCapabilityRegistry.

Tests cover: register/unregister lifecycle, capability checks,
plan_template / executor_tools retrieval, dispatch via registry
and entry_point, dynamic import security allowlist, and edge cases.
"""

from __future__ import annotations

import types

import pytest
from unittest.mock import patch, MagicMock

from app.core.skills.skill_capability import SkillCapabilityRegistry

# Path to SkillManager used by the module-under-test (lazy import target)
_SM_PATH = "app.core.skills.skill_manager.SkillManager"


def _dummy_fn(user_input: str, context: dict) -> str:
    return f"echo:{user_input}"


def _another_fn(user_input: str, context: dict) -> str:
    return "another"


# ---------------------------------------------------------------------------
# Register / Unregister lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterUnregister:
    def setup_method(self):
        SkillCapabilityRegistry._registry = {}

    def teardown_method(self):
        SkillCapabilityRegistry._registry = {}

    def test_register_adds_callable(self):
        SkillCapabilityRegistry.register("s1", _dummy_fn)
        assert "s1" in SkillCapabilityRegistry._registry
        assert SkillCapabilityRegistry._registry["s1"] is _dummy_fn

    def test_unregister_existing_returns_true(self):
        SkillCapabilityRegistry.register("s1", _dummy_fn)
        assert SkillCapabilityRegistry.unregister("s1") is True
        assert "s1" not in SkillCapabilityRegistry._registry

    def test_unregister_missing_returns_false(self):
        assert SkillCapabilityRegistry.unregister("nonexistent") is False

    def test_register_overwrites_existing(self):
        SkillCapabilityRegistry.register("s1", _dummy_fn)
        SkillCapabilityRegistry.register("s1", _another_fn)
        assert SkillCapabilityRegistry._registry["s1"] is _another_fn

    def test_list_registered_returns_correct_ids(self):
        SkillCapabilityRegistry.register("alpha", _dummy_fn)
        SkillCapabilityRegistry.register("beta", _another_fn)
        result = SkillCapabilityRegistry.list_registered()
        assert sorted(result) == ["alpha", "beta"]

    def test_list_registered_empty(self):
        assert SkillCapabilityRegistry.list_registered() == []


# ---------------------------------------------------------------------------
# has_capability
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHasCapability:
    def setup_method(self):
        SkillCapabilityRegistry._registry = {}

    def teardown_method(self):
        SkillCapabilityRegistry._registry = {}

    def test_has_capability_registry_hit(self):
        SkillCapabilityRegistry.register("s1", _dummy_fn)
        assert SkillCapabilityRegistry.has_capability("s1") is True

    @patch(_SM_PATH)
    def test_has_capability_via_entry_point(self, mock_sm):
        skill = MagicMock()
        skill.entry_point = "app.some.module:run"
        mock_sm.get_definition.return_value = skill

        assert SkillCapabilityRegistry.has_capability("s_ep") is True
        mock_sm.get_definition.assert_called_once_with("s_ep")

    @patch(_SM_PATH)
    def test_has_capability_no_entry_point(self, mock_sm):
        skill = MagicMock()
        skill.entry_point = ""
        mock_sm.get_definition.return_value = skill

        assert SkillCapabilityRegistry.has_capability("s_none") is False

    @patch(_SM_PATH)
    def test_has_capability_skill_manager_raises(self, mock_sm):
        mock_sm.get_definition.side_effect = RuntimeError("boom")
        assert SkillCapabilityRegistry.has_capability("err") is False


# ---------------------------------------------------------------------------
# get_plan_template
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPlanTemplate:
    def setup_method(self):
        SkillCapabilityRegistry._registry = {}

    def teardown_method(self):
        SkillCapabilityRegistry._registry = {}

    @patch(_SM_PATH)
    def test_get_plan_template_success(self, mock_sm):
        template = [{"step": "write"}, {"step": "review"}]
        skill = MagicMock()
        skill.plan_template = template
        mock_sm.get_definition.return_value = skill

        assert SkillCapabilityRegistry.get_plan_template("s1") == template

    @patch(_SM_PATH)
    def test_get_plan_template_none_when_empty(self, mock_sm):
        skill = MagicMock()
        skill.plan_template = []
        mock_sm.get_definition.return_value = skill

        assert SkillCapabilityRegistry.get_plan_template("s1") is None

    @patch(_SM_PATH)
    def test_get_plan_template_none_when_skill_missing(self, mock_sm):
        mock_sm.get_definition.return_value = None
        assert SkillCapabilityRegistry.get_plan_template("no") is None

    @patch(_SM_PATH)
    def test_get_plan_template_exception_returns_none(self, mock_sm):
        mock_sm.get_definition.side_effect = Exception("db down")
        assert SkillCapabilityRegistry.get_plan_template("err") is None


# ---------------------------------------------------------------------------
# get_executor_tools
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetExecutorTools:
    def setup_method(self):
        SkillCapabilityRegistry._registry = {}

    def teardown_method(self):
        SkillCapabilityRegistry._registry = {}

    @patch(_SM_PATH)
    def test_get_executor_tools_success(self, mock_sm):
        tools = ["read_file", "write_file"]
        skill = MagicMock()
        skill.executor_tools = tools
        mock_sm.get_definition.return_value = skill

        assert SkillCapabilityRegistry.get_executor_tools("s1") == tools

    @patch(_SM_PATH)
    def test_get_executor_tools_none_when_empty(self, mock_sm):
        skill = MagicMock()
        skill.executor_tools = []
        mock_sm.get_definition.return_value = skill

        assert SkillCapabilityRegistry.get_executor_tools("s1") is None

    @patch(_SM_PATH)
    def test_get_executor_tools_exception_returns_none(self, mock_sm):
        mock_sm.get_definition.side_effect = Exception("fail")
        assert SkillCapabilityRegistry.get_executor_tools("err") is None


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDispatch:
    def setup_method(self):
        SkillCapabilityRegistry._registry = {}

    def teardown_method(self):
        SkillCapabilityRegistry._registry = {}

    def test_dispatch_via_registered_callable(self):
        SkillCapabilityRegistry.register("s1", _dummy_fn)
        result = SkillCapabilityRegistry.dispatch("s1", "hello", {"key": "val"})
        assert result == "echo:hello"

    def test_dispatch_context_injection_skill_id(self):
        """skill_id is automatically injected into context."""
        captured = {}

        def capture_fn(user_input, context):
            captured.update(context)
            return "ok"

        SkillCapabilityRegistry.register("inject", capture_fn)
        SkillCapabilityRegistry.dispatch("inject", "x", {"extra": 1})
        assert captured["skill_id"] == "inject"
        assert captured["extra"] == 1

    def test_dispatch_empty_context_defaults(self):
        """When context is None a fresh dict with skill_id is created."""
        captured = {}

        def capture_fn(user_input, context):
            captured.update(context)
            return "ok"

        SkillCapabilityRegistry.register("ctx", capture_fn)
        SkillCapabilityRegistry.dispatch("ctx", "x", None)
        assert captured == {"skill_id": "ctx"}

    def test_dispatch_context_none_omitted(self):
        """Context parameter can be entirely omitted."""
        captured = {}

        def capture_fn(user_input, context):
            captured.update(context)
            return "ok"

        SkillCapabilityRegistry.register("ctx2", capture_fn)
        SkillCapabilityRegistry.dispatch("ctx2", "x")
        assert captured == {"skill_id": "ctx2"}

    @patch("app.core.skills.skill_capability.importlib.import_module")
    @patch(_SM_PATH)
    def test_dispatch_via_entry_point(self, mock_sm, mock_import):
        skill = MagicMock()
        skill.entry_point = "app.skills.impl:run_skill"

        mock_sm.get_definition.return_value = skill

        fake_fn = MagicMock(return_value="ep_result")
        fake_module = types.ModuleType("app.skills.impl")
        fake_module.run_skill = fake_fn
        mock_import.return_value = fake_module

        result = SkillCapabilityRegistry.dispatch("ep_skill", "input", {})
        assert result == "ep_result"
        fake_fn.assert_called_once()
        call_ctx = fake_fn.call_args[1]["context"]
        assert call_ctx["skill_id"] == "ep_skill"

    @patch(_SM_PATH)
    def test_dispatch_no_entry_point_raises_key_error(self, mock_sm):
        skill = MagicMock()
        skill.entry_point = ""
        mock_sm.get_definition.return_value = skill

        with pytest.raises(KeyError, match="没有可调用实现"):
            SkillCapabilityRegistry.dispatch("empty", "x")

    @patch(_SM_PATH)
    def test_dispatch_skill_manager_raises_runtime_error(self, mock_sm):
        mock_sm.get_definition.side_effect = Exception("db error")

        with pytest.raises(RuntimeError, match="无法加载 Skill"):
            SkillCapabilityRegistry.dispatch("broken", "x")


# ---------------------------------------------------------------------------
# _load_entry_point
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadEntryPoint:
    def setup_method(self):
        SkillCapabilityRegistry._registry = {}

    def teardown_method(self):
        SkillCapabilityRegistry._registry = {}

    @patch("app.core.skills.skill_capability.importlib.import_module")
    def test_load_entry_point_valid(self, mock_import):
        fake_fn = MagicMock()
        fake_module = types.ModuleType("app.my.mod")
        fake_module.my_func = fake_fn
        mock_import.return_value = fake_module

        result = SkillCapabilityRegistry._load_entry_point("app.my.mod:my_func")
        assert result is fake_fn
        mock_import.assert_called_once_with("app.my.mod")

    def test_load_entry_point_no_colon_raises_value_error(self):
        with pytest.raises(ValueError, match="格式错误"):
            SkillCapabilityRegistry._load_entry_point("app.my.mod.my_func")

    def test_load_entry_point_disallowed_prefix_raises_import_error(self):
        with pytest.raises(ImportError, match="不在允许的模块前缀列表中"):
            SkillCapabilityRegistry._load_entry_point("os.path:join")

    @patch("app.core.skills.skill_capability.importlib.import_module")
    def test_load_entry_point_import_error(self, mock_import):
        mock_import.side_effect = ImportError("No module named 'app.missing'")

        with pytest.raises(ImportError, match="无法导入 entry_point 模块"):
            SkillCapabilityRegistry._load_entry_point("app.missing:fn")

    @patch("app.core.skills.skill_capability.importlib.import_module")
    def test_load_entry_point_missing_attribute(self, mock_import):
        fake_module = types.ModuleType("app.ok")
        mock_import.return_value = fake_module

        with pytest.raises(AttributeError, match="属性.*不存在"):
            SkillCapabilityRegistry._load_entry_point("app.ok:no_such_attr")

    @patch("app.core.skills.skill_capability.importlib.import_module")
    def test_load_entry_point_non_callable_raises_type_error(self, mock_import):
        fake_module = types.ModuleType("app.data")
        fake_module.CONFIG = {"key": "value"}  # not callable
        mock_import.return_value = fake_module

        with pytest.raises(TypeError, match="不可调用"):
            SkillCapabilityRegistry._load_entry_point("app.data:CONFIG")

    @patch("app.core.skills.skill_capability.importlib.import_module")
    def test_load_entry_point_nested_attr(self, mock_import):
        """Supports 'module:Class.method' style entry_points."""
        inner_fn = MagicMock()

        class FakeClass:
            method = inner_fn

        fake_module = types.ModuleType("app.nested")
        fake_module.FakeClass = FakeClass
        mock_import.return_value = fake_module

        result = SkillCapabilityRegistry._load_entry_point(
            "app.nested:FakeClass.method"
        )
        assert result is inner_fn
