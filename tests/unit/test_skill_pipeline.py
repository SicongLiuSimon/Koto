# -*- coding: utf-8 -*-
"""Unit tests for app.core.skills.skill_pipeline module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.skills.skill_pipeline import (
    PipelineResult,
    PipelineStep,
    SkillChain,
    SkillPipeline,
)

# Patch targets — these modules use local imports so we patch at source
_PATCH_REGISTRY = "app.core.skills.skill_capability.SkillCapabilityRegistry"
_PATCH_MANAGER = "app.core.skills.skill_manager.SkillManager"


# ─── PipelineStep dataclass tests ────────────────────────────────


class TestPipelineStep:
    def test_defaults(self):
        step = PipelineStep(skill_id="my_skill")
        assert step.skill_id == "my_skill"
        assert step.output_key is None
        assert step.input_from == {}
        assert step.pass_full_ctx is False
        assert step.skip_on_error is True

    def test_effective_output_key_uses_skill_id_when_output_key_is_none(self):
        step = PipelineStep(skill_id="foo")
        assert step.effective_output_key == "foo"

    def test_effective_output_key_uses_explicit_value(self):
        step = PipelineStep(skill_id="foo", output_key="bar")
        assert step.effective_output_key == "bar"


# ─── PipelineResult tests ────────────────────────────────────────


class TestPipelineResult:
    def test_success_when_all_executed(self):
        r = PipelineResult(
            final_output="ok",
            context={},
            steps_executed=["a"],
            steps_skipped=[],
            elapsed_ms=1.0,
        )
        assert r.success is True

    def test_success_despite_steps_skipped(self):
        """Skipped steps (skip_on_error=True) are non-fatal; success = any step ran."""
        r = PipelineResult(
            final_output="ok",
            context={},
            steps_executed=["a"],
            steps_skipped=["b"],
            elapsed_ms=1.0,
        )
        assert r.success is True

    def test_not_success_when_nothing_executed(self):
        r = PipelineResult(
            final_output=None,
            context={},
            steps_executed=[],
            steps_skipped=[],
            elapsed_ms=0.0,
        )
        assert r.success is False


# ─── SkillPipeline tests ─────────────────────────────────────────


class TestSkillPipelineInit:
    def test_empty_steps_raises(self):
        with pytest.raises(ValueError, match="至少一个"):
            SkillPipeline(steps=[])

    def test_valid_steps_stored(self):
        steps = [PipelineStep("a"), PipelineStep("b")]
        p = SkillPipeline(steps=steps)
        assert p.steps is steps


class TestSkillPipelineSingleStep:
    """Single-step pipeline execution."""

    @patch(_PATCH_REGISTRY)
    def test_single_step_returns_output(self, mock_registry):
        mock_registry.dispatch.return_value = "result_A"

        pipeline = SkillPipeline(steps=[PipelineStep("skill_a")])
        result = pipeline.run(user_input="hello")

        assert result.final_output == "result_A"
        assert result.steps_executed == ["skill_a"]
        assert result.steps_skipped == []
        assert result.success is True
        assert result.elapsed_ms >= 0

    @patch(_PATCH_REGISTRY)
    def test_single_step_stores_output_under_effective_key(self, mock_registry):
        mock_registry.dispatch.return_value = 42

        pipeline = SkillPipeline(steps=[PipelineStep("skill_a", output_key="my_key")])
        result = pipeline.run(user_input="hi")

        assert result.context["my_key"] == 42

    @patch(_PATCH_REGISTRY)
    def test_single_step_none_output_not_stored(self, mock_registry):
        mock_registry.dispatch.return_value = None

        pipeline = SkillPipeline(steps=[PipelineStep("skill_a")])
        result = pipeline.run(user_input="x")

        assert "skill_a" not in result.context
        assert result.final_output is None
        assert result.steps_executed == ["skill_a"]


class TestSkillPipelineMultiStep:
    """Multi-step pipeline: output feeds into next step."""

    @patch(_PATCH_REGISTRY)
    def test_output_feeds_to_next_step_via_input_from(self, mock_registry):
        mock_registry.dispatch.side_effect = ["output_1", "output_2"]

        steps = [
            PipelineStep("step1", output_key="result1"),
            PipelineStep(
                "step2",
                input_from={"result1": "source"},
                output_key="result2",
            ),
        ]
        pipeline = SkillPipeline(steps=steps)
        result = pipeline.run(user_input="go")

        # Verify step2 received result1 via input_from mapping
        call_args_step2 = mock_registry.dispatch.call_args_list[1]
        assert call_args_step2.kwargs["context"]["source"] == "output_1"

        assert result.final_output == "output_2"
        assert result.context["result1"] == "output_1"
        assert result.context["result2"] == "output_2"
        assert result.steps_executed == ["step1", "step2"]

    @patch(_PATCH_REGISTRY)
    def test_three_step_chain(self, mock_registry):
        mock_registry.dispatch.side_effect = ["A", "B", "C"]

        steps = [
            PipelineStep("s1"),
            PipelineStep("s2"),
            PipelineStep("s3"),
        ]
        result = SkillPipeline(steps=steps).run(user_input="x")

        assert result.final_output == "C"
        assert result.steps_executed == ["s1", "s2", "s3"]
        assert result.context["s1"] == "A"
        assert result.context["s2"] == "B"
        assert result.context["s3"] == "C"

    @patch(_PATCH_REGISTRY)
    def test_dispatch_receives_user_input(self, mock_registry):
        mock_registry.dispatch.return_value = "done"

        SkillPipeline(steps=[PipelineStep("s1")]).run(user_input="my input")

        mock_registry.dispatch.assert_called_once()
        assert mock_registry.dispatch.call_args.kwargs["user_input"] == "my input"


class TestSkillPipelineContextPassing:
    """Context initialisation and pass_full_ctx flag."""

    @patch(_PATCH_REGISTRY)
    def test_initial_context_available_to_first_step(self, mock_registry):
        mock_registry.dispatch.return_value = "ok"

        step = PipelineStep("s1", pass_full_ctx=True)
        pipeline = SkillPipeline(steps=[step])
        result = pipeline.run(user_input="x", context={"file": "a.py"})

        ctx_passed = mock_registry.dispatch.call_args.kwargs["context"]
        # With pass_full_ctx=True, context keys are spread into call_ctx directly
        assert ctx_passed["file"] == "a.py"

    @patch(_PATCH_REGISTRY)
    def test_pass_full_ctx_flag(self, mock_registry):
        mock_registry.dispatch.return_value = "ok"

        step = PipelineStep("s1", pass_full_ctx=True)
        SkillPipeline(steps=[step]).run(user_input="x", context={"key": "val"})

        ctx_passed = mock_registry.dispatch.call_args.kwargs["context"]
        # When pass_full_ctx=True, context keys are spread directly into call_ctx
        assert ctx_passed["key"] == "val"

    @patch(_PATCH_REGISTRY)
    def test_input_from_maps_context_keys(self, mock_registry):
        mock_registry.dispatch.side_effect = ["val1", "val2"]

        steps = [
            PipelineStep("s1", output_key="out1"),
            PipelineStep("s2", input_from={"out1": "my_param"}),
        ]
        SkillPipeline(steps=steps).run(user_input="x")

        ctx_step2 = mock_registry.dispatch.call_args_list[1].kwargs["context"]
        assert ctx_step2["my_param"] == "val1"

    @patch(_PATCH_REGISTRY)
    def test_input_from_missing_key_is_silently_skipped(self, mock_registry):
        """If input_from references a context key that doesn't exist, it is simply not injected."""
        mock_registry.dispatch.return_value = "ok"

        step = PipelineStep("s1", input_from={"nonexistent": "p"})
        result = SkillPipeline(steps=[step]).run(user_input="x")

        ctx_passed = mock_registry.dispatch.call_args.kwargs["context"]
        assert "p" not in ctx_passed
        assert result.steps_executed == ["s1"]

    @patch(_PATCH_REGISTRY)
    def test_original_context_not_mutated(self, mock_registry):
        mock_registry.dispatch.return_value = "new_data"

        original = {"initial": "value"}
        SkillPipeline(steps=[PipelineStep("s1")]).run(user_input="x", context=original)

        # The original dict must not be mutated
        assert "s1" not in original

    @patch(_PATCH_REGISTRY)
    def test_none_context_defaults_to_empty(self, mock_registry):
        mock_registry.dispatch.return_value = "ok"

        result = SkillPipeline(steps=[PipelineStep("s1")]).run(
            user_input="x", context=None
        )
        assert result.context.get("s1") == "ok"


class TestSkillPipelineErrorHandling:
    """Error handling: skip_on_error vs hard stop."""

    @patch(_PATCH_REGISTRY)
    def test_skip_on_error_true_continues(self, mock_registry):
        mock_registry.dispatch.side_effect = [
            RuntimeError("boom"),
            "ok",
        ]

        steps = [
            PipelineStep("bad", skip_on_error=True),
            PipelineStep("good"),
        ]
        result = SkillPipeline(steps=steps).run(user_input="x")

        assert result.steps_skipped == ["bad"]
        assert result.steps_executed == ["good"]
        assert result.final_output == "ok"

    @patch(_PATCH_REGISTRY)
    def test_skip_on_error_false_stops_pipeline(self, mock_registry):
        mock_registry.dispatch.side_effect = [
            RuntimeError("fatal"),
            "never_reached",
        ]

        steps = [
            PipelineStep("fatal_step", skip_on_error=False),
            PipelineStep("unreachable"),
        ]
        result = SkillPipeline(steps=steps).run(user_input="x")

        assert "fatal_step" not in result.steps_executed
        assert result.steps_skipped == []
        assert result.final_output is None
        # Second step should never be dispatched
        assert mock_registry.dispatch.call_count == 1

    @patch(_PATCH_REGISTRY)
    def test_error_mid_pipeline_preserves_prior_results(self, mock_registry):
        mock_registry.dispatch.side_effect = [
            "first_ok",
            RuntimeError("mid fail"),
            "third_ok",
        ]

        steps = [
            PipelineStep("s1"),
            PipelineStep("s2", skip_on_error=True),
            PipelineStep("s3"),
        ]
        result = SkillPipeline(steps=steps).run(user_input="x")

        assert result.context["s1"] == "first_ok"
        assert "s2" not in result.context
        assert result.context["s3"] == "third_ok"
        assert result.steps_executed == ["s1", "s3"]
        assert result.steps_skipped == ["s2"]

    @patch(_PATCH_REGISTRY)
    def test_hard_stop_returns_partial_result_with_elapsed(self, mock_registry):
        mock_registry.dispatch.side_effect = RuntimeError("boom")

        steps = [PipelineStep("s1", skip_on_error=False)]
        result = SkillPipeline(steps=steps).run(user_input="x")

        assert result.elapsed_ms >= 0
        assert result.final_output is None
        assert result.steps_executed == []


# ─── SkillChain tests ────────────────────────────────────────────


class TestSkillChainFromChainsTo:
    """SkillChain.from_chains_to() builds a pipeline from skill definitions."""

    @patch(_PATCH_MANAGER)
    def test_single_skill_no_chains(self, mock_sm):
        mock_sm._ensure_init.return_value = None
        mock_sm._def_registry = {"root": MagicMock(chains_to=[])}

        pipeline = SkillChain.from_chains_to("root", depth=1)

        assert isinstance(pipeline, SkillPipeline)
        assert len(pipeline.steps) == 1
        assert pipeline.steps[0].skill_id == "root"

    @patch(_PATCH_MANAGER)
    def test_chain_follows_chains_to(self, mock_sm):
        mock_sm._ensure_init.return_value = None

        root_def = MagicMock(chains_to=["child"])
        child_def = MagicMock(chains_to=[])

        mock_sm._def_registry = {"root": root_def, "child": child_def}

        pipeline = SkillChain.from_chains_to("root", depth=2)

        ids = [s.skill_id for s in pipeline.steps]
        assert ids == ["root", "child"]

    @patch(_PATCH_MANAGER)
    def test_depth_limits_recursion(self, mock_sm):
        mock_sm._ensure_init.return_value = None

        a_def = MagicMock(chains_to=["b"])
        b_def = MagicMock(chains_to=["c"])
        c_def = MagicMock(chains_to=[])

        mock_sm._def_registry = {"a": a_def, "b": b_def, "c": c_def}

        # depth=1 means root + 1 level of chains_to
        pipeline = SkillChain.from_chains_to("a", depth=1)
        ids = [s.skill_id for s in pipeline.steps]
        assert ids == ["a", "b"]

    @patch(_PATCH_MANAGER)
    def test_cycle_prevention(self, mock_sm):
        mock_sm._ensure_init.return_value = None

        a_def = MagicMock(chains_to=["b"])
        b_def = MagicMock(chains_to=["a"])  # cycle back

        mock_sm._def_registry = {"a": a_def, "b": b_def}

        pipeline = SkillChain.from_chains_to("a", depth=5)
        ids = [s.skill_id for s in pipeline.steps]
        assert ids == ["a", "b"]  # each visited only once

    @patch(_PATCH_MANAGER)
    def test_pass_full_ctx_propagated(self, mock_sm):
        mock_sm._ensure_init.return_value = None
        mock_sm._def_registry = {"x": MagicMock(chains_to=[])}

        pipeline = SkillChain.from_chains_to("x", pass_full_ctx=False)
        assert pipeline.steps[0].pass_full_ctx is False

    @patch(_PATCH_MANAGER)
    def test_missing_root_skill_still_creates_step(self, mock_sm):
        """Root skill not in _def_registry still gets a step (just no chaining)."""
        mock_sm._ensure_init.return_value = None
        mock_sm._def_registry = {}

        pipeline = SkillChain.from_chains_to("nonexistent")
        assert len(pipeline.steps) == 1
        assert pipeline.steps[0].skill_id == "nonexistent"

    @patch(_PATCH_MANAGER)
    def test_ensure_init_failure_raises_runtime_error(self, mock_sm):
        mock_sm._ensure_init.side_effect = Exception("init failed")

        with pytest.raises(RuntimeError, match="加载失败"):
            SkillChain.from_chains_to("anything")

    @patch(_PATCH_MANAGER)
    def test_chains_to_none_treated_as_empty(self, mock_sm):
        """chains_to=None should be handled gracefully."""
        mock_sm._ensure_init.return_value = None
        mock_sm._def_registry = {"x": MagicMock(chains_to=None)}

        pipeline = SkillChain.from_chains_to("x", depth=2)
        assert [s.skill_id for s in pipeline.steps] == ["x"]


class TestSkillChainBuildFromActive:
    """SkillChain.build_from_active() filters skills with entry_point or capability."""

    @patch(_PATCH_REGISTRY)
    @patch(_PATCH_MANAGER)
    def test_builds_pipeline_for_capable_skills(self, mock_sm, mock_cap):
        mock_sm._ensure_init.return_value = None

        s1_def = MagicMock(entry_point="mod:func")
        s2_def = MagicMock(entry_point=None)

        mock_sm._def_registry = {"s1": s1_def, "s2": s2_def}
        mock_cap.has_capability.side_effect = lambda sid: sid == "s2"

        pipeline = SkillChain.build_from_active(["s1", "s2"])

        assert pipeline is not None
        ids = [s.skill_id for s in pipeline.steps]
        assert "s1" in ids  # has entry_point
        assert "s2" in ids  # has capability

    @patch(_PATCH_REGISTRY)
    @patch(_PATCH_MANAGER)
    def test_returns_none_when_no_executable_skills(self, mock_sm, mock_cap):
        mock_sm._ensure_init.return_value = None

        # Skill with no entry_point and no capability
        s_def = MagicMock(entry_point=None)
        mock_sm._def_registry = {"s1": s_def}
        mock_cap.has_capability.return_value = False

        result = SkillChain.build_from_active(["s1"])
        assert result is None

    @patch(_PATCH_REGISTRY)
    @patch(_PATCH_MANAGER)
    def test_returns_none_when_skills_not_in_registry(self, mock_sm, mock_cap):
        mock_sm._ensure_init.return_value = None
        mock_sm._def_registry = {}

        result = SkillChain.build_from_active(["unknown"])
        assert result is None

    @patch(_PATCH_MANAGER)
    def test_returns_none_when_init_fails(self, mock_sm):
        mock_sm._ensure_init.side_effect = Exception("init failed")

        result = SkillChain.build_from_active(["s1"])
        assert result is None

    @patch(_PATCH_REGISTRY)
    @patch(_PATCH_MANAGER)
    def test_empty_active_list_returns_none(self, mock_sm, mock_cap):
        mock_sm._ensure_init.return_value = None
        mock_sm._def_registry = {}

        result = SkillChain.build_from_active([])
        assert result is None

    @patch(_PATCH_REGISTRY)
    @patch(_PATCH_MANAGER)
    def test_pass_full_ctx_flag_propagated(self, mock_sm, mock_cap):
        mock_sm._ensure_init.return_value = None
        s_def = MagicMock(entry_point="m:f")
        mock_sm._def_registry = {"s1": s_def}
        mock_cap.has_capability.return_value = False

        pipeline = SkillChain.build_from_active(["s1"], pass_full_ctx=False)
        assert pipeline is not None
        assert pipeline.steps[0].pass_full_ctx is False
