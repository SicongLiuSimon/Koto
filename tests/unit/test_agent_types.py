# -*- coding: utf-8 -*-
"""Unit tests for app.core.agent.types dataclasses."""
import logging
import pytest
from app.core.agent.types import AgentAction, AgentResponse, AgentStep, AgentStepType

_LOGGER = "app.core.agent.types"


@pytest.mark.unit
class TestAgentStepType:
    def test_all_expected_values_exist(self):
        values = {e.value for e in AgentStepType}
        assert values == {"thought", "action", "observation", "answer", "error"}

    def test_enum_accessible_by_value(self):
        assert AgentStepType("thought") is AgentStepType.THOUGHT
        assert AgentStepType("error") is AgentStepType.ERROR


@pytest.mark.unit
class TestAgentAction:
    def test_required_fields(self):
        action = AgentAction(tool_name="read_file", tool_args={"path": "/tmp/x"})
        assert action.tool_name == "read_file"
        assert action.tool_args == {"path": "/tmp/x"}
        assert action.tool_call_id is None

    def test_optional_tool_call_id(self):
        action = AgentAction(tool_name="search", tool_args={}, tool_call_id="call-1")
        assert action.tool_call_id == "call-1"


@pytest.mark.unit
class TestAgentStep:
    def test_minimal_step(self):
        step = AgentStep(step_type=AgentStepType.THOUGHT, content="Let me think…")
        assert step.step_type is AgentStepType.THOUGHT
        assert step.content == "Let me think…"
        assert step.action is None
        assert step.observation is None
        assert step.metadata == {}

    def test_to_dict_minimal(self):
        step = AgentStep(step_type=AgentStepType.ANSWER, content="42")
        d = step.to_dict()
        assert d["step_type"] == "answer"
        assert d["content"] == "42"
        assert "action" not in d
        assert "observation" not in d

    def test_to_dict_with_action(self):
        action = AgentAction(tool_name="web_search", tool_args={"query": "koto"}, tool_call_id="tc-1")
        step = AgentStep(
            step_type=AgentStepType.ACTION,
            content="Searching the web",
            action=action,
        )
        d = step.to_dict()
        assert d["action"]["tool_name"] == "web_search"
        assert d["action"]["tool_args"] == {"query": "koto"}
        assert d["action"]["tool_call_id"] == "tc-1"

    def test_to_dict_with_observation(self):
        step = AgentStep(
            step_type=AgentStepType.OBSERVATION,
            content="",
            observation="Result: 42",
        )
        d = step.to_dict()
        assert d["observation"] == "Result: 42"

    def test_to_dict_preserves_metadata(self):
        step = AgentStep(
            step_type=AgentStepType.THOUGHT,
            content="x",
            metadata={"tokens": 10, "model": "gemini"},
        )
        d = step.to_dict()
        assert d["metadata"]["tokens"] == 10
        assert d["metadata"]["model"] == "gemini"

    def test_error_step(self):
        step = AgentStep(step_type=AgentStepType.ERROR, content="Something went wrong")
        assert step.step_type is AgentStepType.ERROR
        d = step.to_dict()
        assert d["step_type"] == "error"


@pytest.mark.unit
class TestAgentResponse:
    def test_minimal_response(self):
        resp = AgentResponse(content="Hello", steps=[])
        assert resp.content == "Hello"
        assert resp.steps == []
        assert resp.metadata == {}

    def test_to_dict_empty_steps(self):
        resp = AgentResponse(content="Done", steps=[])
        d = resp.to_dict()
        assert d["content"] == "Done"
        assert d["steps"] == []
        assert d["metadata"] == {}

    def test_to_dict_with_steps(self):
        steps = [
            AgentStep(step_type=AgentStepType.THOUGHT, content="thinking"),
            AgentStep(step_type=AgentStepType.ANSWER, content="answer"),
        ]
        resp = AgentResponse(content="answer", steps=steps, metadata={"duration": 1.2})
        d = resp.to_dict()
        assert len(d["steps"]) == 2
        assert d["steps"][0]["step_type"] == "thought"
        assert d["steps"][1]["step_type"] == "answer"
        assert d["metadata"]["duration"] == 1.2

    def test_to_dict_step_serialization_roundtrip(self):
        action = AgentAction(tool_name="tool", tool_args={"k": "v"})
        step = AgentStep(
            step_type=AgentStepType.ACTION,
            content="calling tool",
            action=action,
            observation="result",
        )
        resp = AgentResponse(content="final", steps=[step])
        d = resp.to_dict()
        assert d["steps"][0]["action"]["tool_name"] == "tool"
        assert d["steps"][0]["observation"] == "result"


@pytest.mark.unit
class TestAgentTypesLogging:
    """Verify that agent types emit the correct log records."""

    def test_error_step_to_dict_logs_warning(self, caplog):
        """An ERROR step serialized via to_dict() must emit a WARNING."""
        step = AgentStep(step_type=AgentStepType.ERROR, content="Something went wrong badly")
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            step.to_dict()
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("ERROR" in m or "Something went wrong" in m for m in warnings), warnings

    def test_non_error_step_no_warning(self, caplog):
        """Non-ERROR steps must NOT emit a WARNING when serialized (negative test)."""
        for step_type in (AgentStepType.THOUGHT, AgentStepType.ACTION,
                          AgentStepType.OBSERVATION, AgentStepType.ANSWER):
            step = AgentStep(step_type=step_type, content="content")
            with caplog.at_level(logging.WARNING, logger=_LOGGER):
                step.to_dict()
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == [], f"Non-ERROR steps should not warn: {[r.message for r in warnings]}"

    def test_agent_response_to_dict_logs_debug(self, caplog):
        """AgentResponse.to_dict() must emit a DEBUG log with step count."""
        steps = [
            AgentStep(step_type=AgentStepType.THOUGHT, content="thinking"),
            AgentStep(step_type=AgentStepType.ANSWER, content="done"),
        ]
        resp = AgentResponse(content="answer", steps=steps)
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            resp.to_dict()
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("AgentResponse" in m and "2" in m for m in debug_msgs), debug_msgs

    def test_error_step_warning_contains_content_preview(self, caplog):
        """The WARNING for an ERROR step must include a preview of the content."""
        content = "Detailed error: connection refused to database"
        step = AgentStep(step_type=AgentStepType.ERROR, content=content)
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            step.to_dict()
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("connection refused" in m or "Detailed error" in m for m in warnings), warnings

