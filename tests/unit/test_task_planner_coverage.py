# -*- coding: utf-8 -*-
"""
Comprehensive unit tests for app.core.tasks.task_planner.

Covers: StepStatus, StepResult, PlanStep, Plan, PlanTemplates, TaskPlanner.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.core.tasks.task_planner import (
    Plan,
    PlanStep,
    PlanTemplates,
    StepResult,
    StepStatus,
    TaskPlanner,
)


# ============================================================================
# StepStatus
# ============================================================================


@pytest.mark.unit
class TestStepStatus:
    """Verify all enum members and their string values."""

    def test_enum_values(self):
        assert StepStatus.PENDING.value == "pending"
        assert StepStatus.READY.value == "ready"
        assert StepStatus.RUNNING.value == "running"
        assert StepStatus.WAITING.value == "waiting"
        assert StepStatus.COMPLETED.value == "completed"
        assert StepStatus.FAILED.value == "failed"
        assert StepStatus.SKIPPED.value == "skipped"

    def test_enum_count(self):
        assert len(StepStatus) == 7

    def test_is_string_subclass(self):
        # StepStatus(str, Enum) means members are also strings
        assert isinstance(StepStatus.PENDING, str)
        assert StepStatus.COMPLETED == "completed"


# ============================================================================
# StepResult
# ============================================================================


@pytest.mark.unit
class TestStepResult:
    """Tests for StepResult dataclass and its context_text() method."""

    def test_context_text_with_summary_only(self):
        sr = StepResult(full_output="raw data", summary="Short summary")
        assert sr.context_text() == "Short summary"

    def test_context_text_with_summary_and_key_facts(self):
        sr = StepResult(
            full_output="raw",
            summary="Summary here",
            key_facts=["fact1", "fact2"],
        )
        text = sr.context_text()
        assert "Summary here" in text
        assert "fact1" in text
        assert "fact2" in text

    def test_context_text_fallback_to_full_output(self):
        sr = StepResult(full_output="A" * 1000, summary="", key_facts=[])
        text = sr.context_text()
        # Falls back to full_output[:500]
        assert len(text) == 500
        assert text == "A" * 500

    def test_context_text_key_facts_limited_to_five(self):
        sr = StepResult(
            full_output="raw",
            summary="",
            key_facts=[f"f{i}" for i in range(10)],
        )
        text = sr.context_text()
        # Only first 5 facts should appear
        assert "f4" in text
        assert "f5" not in text

    def test_defaults(self):
        sr = StepResult(full_output="hello")
        assert sr.summary == ""
        assert sr.key_facts == []
        assert sr.replan_hint == ""
        assert sr.structured is None


# ============================================================================
# PlanStep
# ============================================================================


@pytest.mark.unit
class TestPlanStep:
    """Tests for PlanStep dataclass properties and serialization."""

    def test_to_dict_basic(self):
        step = PlanStep(name="s1", description="do stuff")
        d = step.to_dict()
        assert d["name"] == "s1"
        assert d["description"] == "do stuff"
        assert d["status"] == "pending"
        assert "step_result" not in d  # None → removed

    def test_to_dict_with_step_result(self):
        sr = StepResult(full_output="raw", summary="sum", key_facts=["k1"])
        step = PlanStep(name="s1", description="d", step_result=sr)
        d = step.to_dict()
        assert d["step_result"]["summary"] == "sum"
        assert d["step_result"]["key_facts"] == ["k1"]

    def test_is_terminal_completed(self):
        step = PlanStep(name="s", description="d", status=StepStatus.COMPLETED)
        assert step.is_terminal is True

    def test_is_terminal_failed(self):
        step = PlanStep(name="s", description="d", status=StepStatus.FAILED)
        assert step.is_terminal is True

    def test_is_terminal_skipped(self):
        step = PlanStep(name="s", description="d", status=StepStatus.SKIPPED)
        assert step.is_terminal is True

    def test_is_terminal_pending(self):
        step = PlanStep(name="s", description="d", status=StepStatus.PENDING)
        assert step.is_terminal is False

    def test_is_terminal_running(self):
        step = PlanStep(name="s", description="d", status=StepStatus.RUNNING)
        assert step.is_terminal is False

    def test_is_terminal_waiting(self):
        step = PlanStep(name="s", description="d", status=StepStatus.WAITING)
        assert step.is_terminal is False

    def test_elapsed_seconds_with_start_and_end(self):
        now = datetime(2025, 1, 1, 12, 0, 0, 1000)  # microseconds needed for parser
        later = now + timedelta(seconds=42)
        step = PlanStep(
            name="s",
            description="d",
            started_at=now.isoformat(timespec="milliseconds"),
            completed_at=later.isoformat(timespec="milliseconds"),
        )
        elapsed = step.elapsed_seconds
        assert elapsed is not None
        assert abs(elapsed - 42.0) < 0.01

    def test_elapsed_seconds_no_times(self):
        step = PlanStep(name="s", description="d")
        assert step.elapsed_seconds is None

    def test_elapsed_seconds_started_no_completed(self):
        step = PlanStep(
            name="s",
            description="d",
            started_at=datetime.now().isoformat(),
        )
        # Should return a positive number (uses now as end)
        elapsed = step.elapsed_seconds
        assert elapsed is not None
        assert elapsed >= 0

    def test_default_values(self):
        step = PlanStep(name="x", description="y")
        assert step.step_type == "generic"
        assert step.depends_on == []
        assert step.require_approval is False
        assert step.max_retries == 2
        assert step.timeout_seconds == 120
        assert step.allow_failure is False
        assert step.status == StepStatus.PENDING


# ============================================================================
# Plan
# ============================================================================


@pytest.mark.unit
class TestPlan:
    """Tests for Plan dataclass: step management, readiness, completion."""

    def test_add_step_chaining(self):
        plan = Plan(task_id="t1", original_request="req")
        result = plan.add_step(PlanStep(name="a", description="A"))
        assert result is plan  # returns self for chaining
        plan.add_step(PlanStep(name="b", description="B"))
        assert len(plan.steps) == 2

    def test_get_step_found(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="alpha", description="A"))
        step = plan.get_step("alpha")
        assert step is not None
        assert step.name == "alpha"

    def test_get_step_not_found(self):
        plan = Plan(task_id="t1", original_request="req")
        assert plan.get_step("nonexistent") is None

    def test_ready_steps_no_deps(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A"))
        plan.add_step(PlanStep(name="b", description="B"))
        ready = plan.ready_steps()
        assert len(ready) == 2

    def test_ready_steps_with_met_deps(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A", status=StepStatus.COMPLETED))
        plan.add_step(PlanStep(name="b", description="B", depends_on=["a"]))
        ready = plan.ready_steps()
        assert len(ready) == 1
        assert ready[0].name == "b"

    def test_ready_steps_with_unmet_deps(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A"))  # still PENDING
        plan.add_step(PlanStep(name="b", description="B", depends_on=["a"]))
        ready = plan.ready_steps()
        # Only 'a' is ready (no deps); 'b' blocked on 'a'
        assert len(ready) == 1
        assert ready[0].name == "a"

    def test_ready_steps_allow_failure_unblocks_dep(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(
            name="a", description="A",
            status=StepStatus.FAILED, allow_failure=True,
        ))
        plan.add_step(PlanStep(name="b", description="B", depends_on=["a"]))
        ready = plan.ready_steps()
        assert len(ready) == 1
        assert ready[0].name == "b"

    def test_is_done_all_terminal(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A", status=StepStatus.COMPLETED))
        plan.add_step(PlanStep(name="b", description="B", status=StepStatus.SKIPPED))
        assert plan.is_done() is True

    def test_is_done_with_pending_step(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A", status=StepStatus.COMPLETED))
        plan.add_step(PlanStep(name="b", description="B"))
        assert plan.is_done() is False

    def test_is_done_empty_plan(self):
        plan = Plan(task_id="t1", original_request="req")
        # No steps → all() on empty iterable is True
        assert plan.is_done() is True

    def test_has_blocking_failure_true(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A", status=StepStatus.FAILED))
        assert plan.has_blocking_failure() is True

    def test_has_blocking_failure_allow_failure(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(
            name="a", description="A",
            status=StepStatus.FAILED, allow_failure=True,
        ))
        assert plan.has_blocking_failure() is False

    def test_has_blocking_failure_no_failures(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A", status=StepStatus.COMPLETED))
        assert plan.has_blocking_failure() is False

    def test_progress_percent(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A", status=StepStatus.COMPLETED))
        plan.add_step(PlanStep(name="b", description="B"))
        plan.add_step(PlanStep(name="c", description="C", status=StepStatus.SKIPPED))
        plan.add_step(PlanStep(name="d", description="D"))
        # 2 out of 4 terminal (completed + skipped)
        assert plan.progress_percent() == 50

    def test_progress_percent_empty(self):
        plan = Plan(task_id="t1", original_request="req")
        assert plan.progress_percent() == 0

    def test_to_dict(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A"))
        d = plan.to_dict()
        assert d["task_id"] == "t1"
        assert d["original_request"] == "req"
        assert d["status"] == "planning"
        assert d["progress"] == 0
        assert isinstance(d["steps"], list)
        assert len(d["steps"]) == 1
        assert d["steps"][0]["name"] == "a"
        assert "created_at" in d
        assert isinstance(d["context"], dict)


# ============================================================================
# PlanTemplates
# ============================================================================


@pytest.mark.unit
class TestPlanTemplates:
    """Tests for built-in plan template factory methods."""

    def test_research_and_report_structure(self):
        plan = PlanTemplates.research_and_report("tid", "write a report")
        assert plan.task_id == "tid"
        assert len(plan.steps) == 4
        names = [s.name for s in plan.steps]
        assert names == ["research", "outline", "write", "export"]
        # Verify dependency chain
        assert plan.steps[0].depends_on == []
        assert plan.steps[1].depends_on == ["research"]
        assert plan.steps[2].depends_on == ["outline"]
        assert plan.steps[3].depends_on == ["write"]

    def test_data_pipeline_structure(self):
        plan = PlanTemplates.data_pipeline("tid", "process data")
        assert len(plan.steps) == 5
        names = [s.name for s in plan.steps]
        assert names == ["load", "validate", "transform", "analyze", "report"]
        assert plan.steps[0].depends_on == []
        assert plan.steps[4].depends_on == ["analyze"]

    def test_multi_step_task_custom_steps(self):
        steps_data = [
            {
                "name": "fetch",
                "description": "Fetch data",
                "step_type": "code",
                "depends_on": [],
                "executor_prompt": "Run fetch script",
                "suggested_tools": ["web_search"],
            },
            {
                "name": "summarize",
                "description": "Summarize results",
                "depends_on": ["fetch"],
                "require_approval": True,
            },
        ]
        plan = PlanTemplates.multi_step_task("tid", "custom task", steps_data)
        assert len(plan.steps) == 2
        assert plan.steps[0].name == "fetch"
        assert plan.steps[0].step_type == "code"
        assert plan.steps[0].executor_prompt == "Run fetch script"
        assert plan.steps[0].suggested_tools == ["web_search"]
        assert plan.steps[1].depends_on == ["fetch"]
        assert plan.steps[1].require_approval is True

    def test_multi_step_task_defaults(self):
        steps_data = [{"name": "only_step"}]
        plan = PlanTemplates.multi_step_task("t", "r", steps_data)
        s = plan.steps[0]
        assert s.step_type == "llm"
        assert s.depends_on == []
        assert s.require_approval is False
        assert s.timeout_seconds == 120


# ============================================================================
# TaskPlanner
# ============================================================================


@pytest.mark.unit
class TestTaskPlanner:
    """Tests for TaskPlanner: LLM planning, skill planning, execution."""

    # ── plan_with_llm ──────────────────────────────────────────────────────

    def test_plan_with_llm_valid_json(self):
        planner = TaskPlanner()
        mock_llm = MagicMock()
        mock_llm.generate_content.return_value = {
            "content": json.dumps([
                {"name": "step1", "description": "Do A", "depends_on": []},
                {"name": "step2", "description": "Do B", "depends_on": ["step1"]},
            ])
        }

        plan = planner.plan_with_llm("t1", "user request", mock_llm)

        assert len(plan.steps) == 2
        assert plan.steps[0].name == "step1"
        assert plan.steps[1].depends_on == ["step1"]
        mock_llm.generate_content.assert_called_once()

    def test_plan_with_llm_json_in_code_block(self):
        planner = TaskPlanner()
        mock_llm = MagicMock()
        # LLM wraps JSON in markdown code block
        mock_llm.generate_content.return_value = {
            "content": '```json\n[{"name":"s1","description":"d"}]\n```'
        }
        plan = planner.plan_with_llm("t1", "req", mock_llm)
        assert len(plan.steps) == 1
        assert plan.steps[0].name == "s1"

    def test_plan_with_llm_invalid_response_fallback(self):
        planner = TaskPlanner()
        mock_llm = MagicMock()
        mock_llm.generate_content.return_value = {"content": "not json at all"}

        plan = planner.plan_with_llm("t1", "user request", mock_llm)

        # Should fall back to single "execute" step
        assert len(plan.steps) == 1
        assert plan.steps[0].name == "execute"
        assert plan.steps[0].executor_prompt == "user request"

    def test_plan_with_llm_exception_fallback(self):
        planner = TaskPlanner()
        mock_llm = MagicMock()
        mock_llm.generate_content.side_effect = RuntimeError("LLM down")

        plan = planner.plan_with_llm("t1", "do stuff", mock_llm)

        assert len(plan.steps) == 1
        assert plan.steps[0].name == "execute"

    def test_plan_with_llm_empty_array_fallback(self):
        planner = TaskPlanner()
        mock_llm = MagicMock()
        mock_llm.generate_content.return_value = {"content": "[]"}

        plan = planner.plan_with_llm("t1", "req", mock_llm)
        # Empty list → fallback
        assert len(plan.steps) == 1
        assert plan.steps[0].name == "execute"

    def test_plan_with_llm_passes_available_tools(self):
        planner = TaskPlanner()
        mock_llm = MagicMock()
        mock_llm.generate_content.return_value = {
            "content": '[{"name":"s1","description":"d"}]'
        }
        planner.plan_with_llm(
            "t1", "req", mock_llm, available_tools=["web_search", "file_read"]
        )
        call_args = mock_llm.generate_content.call_args
        prompt_text = call_args[1]["prompt"][0]["content"] if "prompt" in call_args[1] else call_args[0][0][0]["content"]
        # The available tools should appear in the prompt
        assert "web_search" in prompt_text

    # ── plan_from_skill ────────────────────────────────────────────────────

    @patch("app.core.tasks.task_planner.SkillCapabilityRegistry", create=True)
    def test_plan_from_skill_with_template(self, _mock_import):
        planner = TaskPlanner()
        template = [
            {"name": "annotate", "description": "Mark up document"},
            {"name": "export", "description": "Export", "depends_on": ["annotate"]},
        ]
        with patch(
            "app.core.skills.skill_capability.SkillCapabilityRegistry.get_plan_template",
            return_value=template,
        ):
            plan = planner.plan_from_skill("doc_annotate", "t1", "annotate doc")

        assert plan is not None
        assert len(plan.steps) == 2
        assert plan.context["skill_id"] == "doc_annotate"

    def test_plan_from_skill_no_template(self):
        planner = TaskPlanner()
        with patch(
            "app.core.skills.skill_capability.SkillCapabilityRegistry.get_plan_template",
            return_value=None,
        ):
            plan = planner.plan_from_skill("unknown", "t1", "req")
        assert plan is None

    def test_plan_from_skill_import_error(self):
        planner = TaskPlanner()
        with patch(
            "app.core.skills.skill_capability.SkillCapabilityRegistry.get_plan_template",
            side_effect=ImportError("no module"),
        ):
            plan = planner.plan_from_skill("x", "t1", "req")
        assert plan is None

    # ── execute_plan ───────────────────────────────────────────────────────

    @patch.object(TaskPlanner, "_publish_step_event")
    @patch.object(TaskPlanner, "_publish_plan_event")
    def test_execute_plan_basic_flow(self, _mock_plan_evt, _mock_step_evt):
        planner = TaskPlanner()
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A"))
        plan.add_step(PlanStep(name="b", description="B", depends_on=["a"]))

        executor_fn = MagicMock(side_effect=["result_a", "result_b"])

        events = list(planner.execute_plan(plan, executor_fn))

        event_types = [e["event"] for e in events]
        assert "step_start" in event_types
        assert "step_done" in event_types
        assert "plan_done" in event_types
        assert plan.status == "completed"
        assert executor_fn.call_count == 2

    @patch.object(TaskPlanner, "_publish_step_event")
    @patch.object(TaskPlanner, "_publish_plan_event")
    def test_execute_plan_step_failure_blocks(self, _mock_plan_evt, _mock_step_evt):
        planner = TaskPlanner()
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A", max_retries=0))
        plan.add_step(PlanStep(name="b", description="B", depends_on=["a"]))

        executor_fn = MagicMock(side_effect=RuntimeError("boom"))

        events = list(planner.execute_plan(plan, executor_fn))

        event_types = [e["event"] for e in events]
        assert "step_failed" in event_types
        assert plan.status == "failed"
        # Step b should be skipped
        b = plan.get_step("b")
        assert b.status == StepStatus.SKIPPED

    @patch.object(TaskPlanner, "_publish_step_event")
    @patch.object(TaskPlanner, "_publish_plan_event")
    def test_execute_plan_cancel_check(self, _mock_plan_evt, _mock_step_evt):
        planner = TaskPlanner()
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A"))

        cancel_fn = MagicMock(return_value=True)

        events = list(planner.execute_plan(plan, MagicMock(), cancel_check=cancel_fn))

        event_types = [e["event"] for e in events]
        assert "plan_cancelled" in event_types
        assert plan.status == "cancelled"

    @patch.object(TaskPlanner, "_publish_step_event")
    @patch.object(TaskPlanner, "_publish_plan_event")
    def test_execute_plan_with_step_result(self, _mock_plan_evt, _mock_step_evt):
        """When executor returns StepResult, context_text is used downstream."""
        planner = TaskPlanner()
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A"))
        plan.add_step(PlanStep(name="b", description="B", depends_on=["a"]))

        sr = StepResult(full_output="raw", summary="short summary")

        def executor(step, ctx):
            if step.name == "a":
                return sr
            # For step b, verify context contains summary from a
            assert "short summary" in ctx.get("a", "")
            return "done"

        events = list(planner.execute_plan(plan, executor))
        assert plan.status == "completed"

    # ── _skip_dependents ───────────────────────────────────────────────────

    def test_skip_dependents_chain(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A", status=StepStatus.FAILED))
        plan.add_step(PlanStep(name="b", description="B", depends_on=["a"]))
        plan.add_step(PlanStep(name="c", description="C", depends_on=["b"]))
        plan.add_step(PlanStep(name="d", description="D"))  # independent

        TaskPlanner._skip_dependents(plan, "a")

        assert plan.get_step("b").status == StepStatus.SKIPPED
        assert plan.get_step("c").status == StepStatus.SKIPPED
        assert plan.get_step("d").status == StepStatus.PENDING  # unaffected

    def test_skip_dependents_no_deps(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="a", description="A", status=StepStatus.FAILED))
        plan.add_step(PlanStep(name="b", description="B"))

        TaskPlanner._skip_dependents(plan, "a")
        assert plan.get_step("b").status == StepStatus.PENDING


# ============================================================================
# Edge cases
# ============================================================================


@pytest.mark.unit
class TestEdgeCases:
    """Miscellaneous edge-case tests."""

    def test_plan_step_unique_ids(self):
        s1 = PlanStep(name="a", description="A")
        s2 = PlanStep(name="a", description="A")
        assert s1.step_id != s2.step_id

    def test_step_result_with_structured_data(self):
        sr = StepResult(
            full_output="raw",
            structured={"key": "value"},
        )
        assert sr.structured == {"key": "value"}

    def test_plan_context_propagation(self):
        plan = Plan(task_id="t1", original_request="req", context={"foo": "bar"})
        assert plan.context["foo"] == "bar"

    def test_plan_to_dict_round_trip_steps(self):
        plan = Plan(task_id="t1", original_request="req")
        plan.add_step(PlanStep(name="x", description="X", status=StepStatus.COMPLETED))
        d = plan.to_dict()
        assert d["progress"] == 100
        assert d["steps"][0]["status"] == "completed"
