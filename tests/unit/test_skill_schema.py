# -*- coding: utf-8 -*-
"""Unit tests for app.core.skills.skill_schema types."""
import logging
import pytest
from app.core.skills.skill_schema import (
    InputVariable,
    OutputFormat,
    OutputSpec,
    SkillCategory,
    SkillDefinition,
    SkillNature,
    VariableType,
)

_LOGGER = "app.core.skills.skill_schema"


def _make_skill(**kwargs) -> SkillDefinition:
    """Factory helper to create a minimal valid SkillDefinition."""
    defaults = dict(
        id="test_skill",
        name="测试技能",
        icon="🧪",
        category=SkillCategory.DOMAIN,
        description="A skill for testing",
    )
    defaults.update(kwargs)
    return SkillDefinition(**defaults)


@pytest.mark.unit
class TestSkillCategory:
    def test_all_categories_exist(self):
        values = {c.value for c in SkillCategory}
        assert values == {"behavior", "style", "domain", "workflow", "memory", "custom"}

    def test_category_is_string_subclass(self):
        assert isinstance(SkillCategory.DOMAIN, str)
        assert SkillCategory.DOMAIN == "domain"


@pytest.mark.unit
class TestSkillNature:
    def test_all_natures_exist(self):
        values = {n.value for n in SkillNature}
        assert values == {"model_hint", "domain_skill", "system"}

    def test_nature_is_string_subclass(self):
        assert isinstance(SkillNature.DOMAIN_SKILL, str)


@pytest.mark.unit
class TestVariableType:
    def test_all_types_exist(self):
        values = {v.value for v in VariableType}
        assert {"string", "integer", "number", "boolean", "array", "object"}.issubset(values)


@pytest.mark.unit
class TestInputVariable:
    def test_required_defaults(self):
        var = InputVariable(name="doc", description="A document")
        assert var.name == "doc"
        assert var.required is True
        assert var.default is None
        assert var.type == VariableType.STRING

    def test_optional_variable(self):
        var = InputVariable(name="length", type=VariableType.INTEGER, required=False, default=300)
        assert var.required is False
        assert var.default == 300

    def test_to_json_schema_property_basic(self):
        var = InputVariable(name="text", description="Input text")
        prop = var.to_json_schema_property()
        assert prop["type"] == "string"
        assert prop["description"] == "Input text"

    def test_to_json_schema_property_with_enum(self):
        var = InputVariable(name="mode", enum=["fast", "slow"])
        prop = var.to_json_schema_property()
        assert prop["enum"] == ["fast", "slow"]

    def test_to_json_schema_property_with_constraints(self):
        var = InputVariable(name="count", type=VariableType.INTEGER, minimum=1.0, maximum=100.0)
        prop = var.to_json_schema_property()
        assert prop["minimum"] == 1.0
        assert prop["maximum"] == 100.0

    def test_to_json_schema_property_with_length_constraints(self):
        var = InputVariable(name="text", min_length=10, max_length=1000)
        prop = var.to_json_schema_property()
        assert prop["minLength"] == 10
        assert prop["maxLength"] == 1000


@pytest.mark.unit
class TestOutputSpec:
    def test_defaults(self):
        spec = OutputSpec()
        assert spec.format == OutputFormat.ANY
        assert spec.must_contain == []
        assert spec.must_not_contain == []
        assert spec.min_chars is None
        assert spec.max_chars is None

    def test_validate_passes_clean_text(self):
        spec = OutputSpec()
        passed, reason = spec.validate("Some output text")
        assert passed

    def test_validate_min_chars_fail(self):
        spec = OutputSpec(min_chars=100)
        passed, reason = spec.validate("short")
        assert not passed
        assert "短" in reason or "short" in reason.lower() or "5" in reason

    def test_validate_max_chars_fail(self):
        spec = OutputSpec(max_chars=5)
        passed, reason = spec.validate("This is too long")
        assert not passed

    def test_validate_must_contain_pass(self):
        spec = OutputSpec(must_contain=["##"])
        passed, _ = spec.validate("## Header\n\nContent here")
        assert passed

    def test_validate_must_contain_fail(self):
        spec = OutputSpec(must_contain=["##"])
        passed, reason = spec.validate("No header here")
        assert not passed
        assert "##" in reason

    def test_validate_must_not_contain_fail(self):
        spec = OutputSpec(must_not_contain=["INTERNAL"])
        passed, reason = spec.validate("This has INTERNAL info")
        assert not passed
        assert "INTERNAL" in reason

    def test_validate_table_format_fail(self):
        spec = OutputSpec(format=OutputFormat.TABLE)
        passed, reason = spec.validate("No table here, just text")
        assert not passed

    def test_validate_table_format_pass(self):
        spec = OutputSpec(format=OutputFormat.TABLE)
        passed, _ = spec.validate("| Col1 | Col2 |\n|------|------|\n| A | B |")
        assert passed

    def test_validate_json_format_pass(self):
        spec = OutputSpec(format=OutputFormat.JSON, required_json_keys=["name", "value"])
        passed, _ = spec.validate('{"name": "test", "value": 42}')
        assert passed

    def test_validate_json_format_missing_key(self):
        spec = OutputSpec(format=OutputFormat.JSON, required_json_keys=["name", "value"])
        passed, reason = spec.validate('{"name": "test"}')
        assert not passed
        assert "value" in reason

    def test_validate_json_format_invalid_json(self):
        spec = OutputSpec(format=OutputFormat.JSON, required_json_keys=["key"])
        passed, reason = spec.validate("not json")
        assert not passed


@pytest.mark.unit
class TestSkillDefinition:
    def test_minimal_construction(self):
        skill = _make_skill()
        assert skill.id == "test_skill"
        assert skill.name == "测试技能"
        assert skill.enabled is False
        assert skill.version == "1.0.0"
        assert skill.author == "builtin"

    def test_default_output_spec(self):
        skill = _make_skill()
        assert isinstance(skill.output_spec, OutputSpec)
        assert skill.output_spec.format == OutputFormat.ANY

    def test_with_input_variables(self):
        vars_ = [
            InputVariable(name="doc", description="Document text"),
            InputVariable(name="length", type=VariableType.INTEGER, required=False, default=300),
        ]
        skill = _make_skill(input_variables=vars_)
        assert len(skill.input_variables) == 2
        assert skill.input_variables[0].name == "doc"

    def test_render_prompt_no_template(self):
        skill = _make_skill(prompt="You are a helpful assistant.")
        assert skill.render_prompt() == "You are a helpful assistant."

    def test_render_prompt_with_variables(self):
        skill = _make_skill(system_prompt_template="Summarize {document} in {max_length} words.")
        rendered = skill.render_prompt({"document": "my doc", "max_length": 100})
        assert "my doc" in rendered
        assert "100" in rendered

    def test_render_prompt_missing_variable_does_not_raise(self):
        skill = _make_skill(system_prompt_template="Hello {name}")
        result = skill.render_prompt({})
        assert "Hello {name}" in result

    def test_to_mcp_tool_structure(self):
        vars_ = [
            InputVariable(name="document", description="Text to summarize", required=True),
            InputVariable(name="max_length", type=VariableType.INTEGER, required=False, default=300),
        ]
        skill = _make_skill(
            id="summarize_doc",
            description="Summarize a document",
            intent_description="User wants to summarize text",
            input_variables=vars_,
            task_types=["CHAT"],
        )
        tool = skill.to_mcp_tool()

        assert tool["name"] == "summarize_doc"
        assert "Summarize a document" in tool["description"]
        assert "User wants to summarize text" in tool["description"]

        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "document" in schema["properties"]
        assert "max_length" in schema["properties"]
        assert "document" in schema["required"]
        assert "max_length" not in schema.get("required", [])

    def test_to_mcp_tool_no_required_fields(self):
        skill = _make_skill()
        tool = skill.to_mcp_tool()
        assert "required" not in tool["inputSchema"]

    def test_to_mcp_tool_koto_meta(self):
        skill = _make_skill(task_types=["CHAT", "DOC_ANNOTATE"], bound_tools=["web_search"])
        tool = skill.to_mcp_tool()
        meta = tool["_koto_meta"]
        assert meta["task_types"] == ["CHAT", "DOC_ANNOTATE"]
        assert meta["bound_tools"] == ["web_search"]
        assert meta["version"] == "1.0.0"

    def test_to_dict_basic_fields(self):
        skill = _make_skill()
        d = skill.to_dict()
        assert d["id"] == "test_skill"
        assert d["name"] == "测试技能"
        assert d["category"] == "domain"
        assert isinstance(d["output_spec"], dict)


@pytest.mark.unit
class TestSkillSchemaLogging:
    """Verify that skill_schema emits the correct log records."""

    def test_render_prompt_missing_var_logs_warning(self, caplog):
        """render_prompt() with a missing variable must emit a WARNING."""
        skill = _make_skill(system_prompt_template="Hello {name}, your score is {score}")
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            result = skill.render_prompt({"name": "Alice"})  # 'score' is missing
        assert result == "Hello {name}, your score is {score}"  # unchanged
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("missing variable" in m.lower() or "render_prompt" in m for m in warnings), warnings

    def test_render_prompt_all_vars_no_warning(self, caplog):
        """render_prompt() with all variables supplied must NOT emit a WARNING (negative)."""
        skill = _make_skill(system_prompt_template="Hello {name}")
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            skill.render_prompt({"name": "Bob"})
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == [], f"Should not warn when all vars provided: {[r.message for r in warnings]}"

    def test_to_mcp_tool_logs_debug(self, caplog):
        """to_mcp_tool() must emit a DEBUG log with the skill id."""
        skill = _make_skill()
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            skill.to_mcp_tool()
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("to_mcp_tool" in m and skill.id in m for m in debug_msgs), debug_msgs

    def test_output_spec_validate_failure_logs_debug(self, caplog):
        """OutputSpec.validate() on failure must emit a DEBUG log with the reason."""
        spec = OutputSpec(must_contain=["##"])
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            passed, reason = spec.validate("No heading here")
        assert not passed
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("OutputSpec" in m and ("validate" in m or "##" in m) for m in debug_msgs), debug_msgs

    def test_output_spec_validate_pass_no_log(self, caplog):
        """OutputSpec.validate() on success must NOT emit any log record (negative)."""
        spec = OutputSpec(must_contain=["##"])
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            passed, _ = spec.validate("## This has a heading")
        assert passed
        assert caplog.records == [], f"Unexpected logs on pass: {[r.message for r in caplog.records]}"

    def test_output_spec_forbidden_content_logs_warning(self, caplog):
        """OutputSpec.validate() when must_not_contain is violated must emit a WARNING."""
        spec = OutputSpec(must_not_contain=["CONFIDENTIAL"])
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            passed, reason = spec.validate("This is CONFIDENTIAL data")
        assert not passed
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("forbidden" in m.lower() or "CONFIDENTIAL" in m or "OutputSpec" in m
                   for m in warnings), warnings

    def test_from_dict_logs_debug(self, caplog):
        """from_dict() must emit a DEBUG log with the skill id being loaded."""
        data = {"id": "my_skill", "name": "My Skill", "icon": "🔧", "category": "custom",
                "description": "test"}
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            SkillDefinition.from_dict(data)
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("from_dict" in m and "my_skill" in m for m in debug_msgs), debug_msgs

