"""Tests for app.core.agent.multi_agent module."""

from dataclasses import asdict
from unittest.mock import MagicMock, Mock, patch

import pytest

_MOD = "app.core.agent.multi_agent"
_LLM_CLS = "app.core.llm.langchain_adapter.KotoLangChainLLM"
_GET_CP = "app.core.agent.checkpoint_manager.get_checkpointer"


# ===================================================================
# 1. AgentRole dataclass
# ===================================================================


class TestAgentRole:
    """Tests for AgentRole dataclass."""

    def test_basic_creation(self):
        from app.core.agent.multi_agent import AgentRole

        role = AgentRole(
            name="test",
            display_name="Test Agent",
            system_prompt="You are test.",
            output_field="test_output",
        )
        assert role.name == "test"
        assert role.is_critic is False
        assert role.temperature == 0.7
        assert role.input_fields is None

    def test_critic_role(self):
        from app.core.agent.multi_agent import AgentRole

        role = AgentRole(
            name="critic",
            display_name="Critic",
            system_prompt="Review...",
            output_field="feedback",
            is_critic=True,
        )
        assert role.is_critic is True

    def test_custom_temperature(self):
        from app.core.agent.multi_agent import AgentRole

        role = AgentRole(
            name="hot",
            display_name="Hot",
            system_prompt="...",
            output_field="out",
            temperature=0.95,
        )
        assert role.temperature == 0.95


# ===================================================================
# 2. ROLES preset library
# ===================================================================


class TestROLES:
    """Tests for the ROLES preset library."""

    def test_researcher_exists(self):
        from app.core.agent.multi_agent import ROLES

        assert ROLES.RESEARCHER.name == "researcher"
        assert ROLES.RESEARCHER.output_field == "research_result"
        assert not ROLES.RESEARCHER.is_critic

    def test_critic_is_critic(self):
        from app.core.agent.multi_agent import ROLES

        assert ROLES.CRITIC.is_critic is True

    def test_reviewer_is_critic(self):
        from app.core.agent.multi_agent import ROLES

        assert ROLES.REVIEWER.is_critic is True

    def test_all_roles_have_required_fields(self):
        from app.core.agent.multi_agent import ROLES

        for role_name in [
            "RESEARCHER",
            "WRITER",
            "CRITIC",
            "REVISE",
            "CODER",
            "REVIEWER",
            "DATA_ANALYST",
        ]:
            role = getattr(ROLES, role_name)
            assert role.name, f"{role_name} missing name"
            assert role.display_name, f"{role_name} missing display_name"
            assert role.system_prompt, f"{role_name} missing system_prompt"
            assert role.output_field, f"{role_name} missing output_field"


# ===================================================================
# 3. Helper functions: _llm_call, _build_context
# ===================================================================


class TestLlmCall:
    """Tests for module-level _llm_call helper."""

    @patch(_LLM_CLS)
    def test_returns_content_string(self, mock_cls):
        from app.core.agent.multi_agent import _llm_call

        mock_resp = Mock(content="LLM output")
        mock_llm = Mock()
        mock_llm.invoke = Mock(return_value=mock_resp)
        mock_cls.return_value = mock_llm

        result = _llm_call("model-id", "system", "user", 0.7)
        assert result == "LLM output"

    @patch(_LLM_CLS)
    def test_returns_error_string_on_failure(self, mock_cls):
        from app.core.agent.multi_agent import _llm_call

        mock_llm = Mock()
        mock_llm.invoke = Mock(side_effect=ConnectionError("timeout"))
        mock_cls.return_value = mock_llm

        result = _llm_call("model-id", "system", "user", 0.7)
        assert "错误" in result or "timeout" in result


class TestBuildContext:
    """Tests for _build_context helper."""

    def _skip_if_unavailable(self):
        from app.core.agent import multi_agent as mod

        if not mod._LG_AVAILABLE:
            pytest.skip("langgraph not installed")

    def test_includes_user_input(self):
        self._skip_if_unavailable()
        from app.core.agent.multi_agent import AgentRole, _build_context

        role = AgentRole(
            name="t", display_name="T", system_prompt="...", output_field="out"
        )
        state = {
            "user_input": "Write about AI",
            "research_result": None,
            "draft": None,
            "critic_feedback": None,
            "code": None,
            "analysis": None,
            "extra_outputs": {},
        }
        ctx = _build_context(state, role)
        assert "Write about AI" in ctx

    def test_includes_specified_input_fields(self):
        self._skip_if_unavailable()
        from app.core.agent.multi_agent import AgentRole, _build_context

        role = AgentRole(
            name="t",
            display_name="T",
            system_prompt="...",
            output_field="out",
            input_fields=["research_result"],
        )
        state = {
            "user_input": "topic",
            "research_result": "Research findings here",
            "draft": "Draft text",
            "critic_feedback": None,
            "code": None,
            "analysis": None,
            "extra_outputs": {},
        }
        ctx = _build_context(state, role)
        assert "Research findings here" in ctx
        # draft should NOT appear since we explicitly specified input_fields
        assert "Draft text" not in ctx


# ===================================================================
# 4. _make_agent_node
# ===================================================================


class TestMakeAgentNode:
    """Tests for the _make_agent_node factory."""

    def _skip_if_unavailable(self):
        from app.core.agent import multi_agent as mod

        if not mod._LG_AVAILABLE:
            pytest.skip("langgraph not installed")

    @patch(f"{_MOD}._llm_call", return_value="Generated output text")
    def test_node_fn_returns_update_dict(self, mock_call):
        self._skip_if_unavailable()
        from app.core.agent.multi_agent import AgentRole, _make_agent_node

        role = AgentRole(
            name="writer",
            display_name="Writer",
            system_prompt="Write.",
            output_field="draft",
        )
        node = _make_agent_node(role)

        state = {
            "user_input": "topic",
            "model_id": "gemini-3-flash-preview",
            "research_result": None,
            "draft": None,
            "critic_feedback": None,
            "code": None,
            "analysis": None,
            "extra_outputs": {},
            "steps": [],
            "messages": [],
        }
        result = node(state)
        assert "writer" in result["steps"]
        assert "messages" in result


# ===================================================================
# 5. _make_critic_router
# ===================================================================


class TestMakeCriticRouter:
    """Tests for the critic routing function factory."""

    def _skip_if_unavailable(self):
        from app.core.agent import multi_agent as mod

        if not mod._LG_AVAILABLE:
            pytest.skip("langgraph not installed")

    def test_routes_to_revise_on_revise_decision(self):
        self._skip_if_unavailable()
        from app.core.agent.multi_agent import AgentRole, _make_critic_router

        critic = AgentRole(
            name="critic",
            display_name="Critic",
            system_prompt="...",
            output_field="critic_feedback",
            is_critic=True,
        )
        router = _make_critic_router(critic, "revise", "finalize")
        state = {
            "critic_feedback": "[DECISION: REVISE]\nPlease fix paragraph 2.",
            "revision_count": 0,
            "max_revisions": 2,
        }
        assert router(state) == "revise"

    def test_routes_to_next_on_pass(self):
        self._skip_if_unavailable()
        from app.core.agent.multi_agent import AgentRole, _make_critic_router

        critic = AgentRole(
            name="critic",
            display_name="Critic",
            system_prompt="...",
            output_field="critic_feedback",
            is_critic=True,
        )
        router = _make_critic_router(critic, "revise", "finalize")
        state = {
            "critic_feedback": "[DECISION: PASS]\nLooks great!",
            "revision_count": 0,
            "max_revisions": 2,
        }
        assert router(state) == "finalize"

    def test_routes_to_next_when_max_revisions_reached(self):
        self._skip_if_unavailable()
        from app.core.agent.multi_agent import AgentRole, _make_critic_router

        critic = AgentRole(
            name="critic",
            display_name="Critic",
            system_prompt="...",
            output_field="critic_feedback",
            is_critic=True,
        )
        router = _make_critic_router(critic, "revise", "finalize")
        state = {
            "critic_feedback": "[DECISION: REVISE]\nNeeds work",
            "revision_count": 2,
            "max_revisions": 2,
        }
        assert router(state) == "finalize"

    def test_routes_to_next_when_empty_feedback(self):
        self._skip_if_unavailable()
        from app.core.agent.multi_agent import AgentRole, _make_critic_router

        critic = AgentRole(
            name="critic",
            display_name="Critic",
            system_prompt="...",
            output_field="critic_feedback",
            is_critic=True,
        )
        router = _make_critic_router(critic, "revise", "finalize")
        state = {
            "critic_feedback": "",
            "revision_count": 0,
            "max_revisions": 2,
        }
        assert router(state) == "finalize"


# ===================================================================
# 6. MultiAgentOrchestrator initialization
# ===================================================================


class TestMultiAgentOrchestrator:
    """Tests for MultiAgentOrchestrator."""

    def _skip_if_unavailable(self):
        from app.core.agent import multi_agent as mod

        if not mod._LG_AVAILABLE:
            pytest.skip("langgraph not installed")

    @patch(_GET_CP)
    def test_init_with_minimal_roles(self, mock_cp):
        self._skip_if_unavailable()
        from langgraph.checkpoint.memory import MemorySaver

        from app.core.agent.multi_agent import AgentRole, MultiAgentOrchestrator

        mock_cp.return_value = MemorySaver()

        role = AgentRole(
            name="solo",
            display_name="Solo Agent",
            system_prompt="Do it.",
            output_field="draft",
        )
        orch = MultiAgentOrchestrator(roles=[role])
        assert orch.roles == [role]
        assert orch.max_revisions == 1

    def test_init_raises_on_empty_roles(self):
        self._skip_if_unavailable()
        from app.core.agent.multi_agent import MultiAgentOrchestrator

        with pytest.raises(ValueError, match="roles"):
            MultiAgentOrchestrator(roles=[])

    @patch(_GET_CP)
    def test_run_returns_result_dict(self, mock_cp):
        self._skip_if_unavailable()
        from langgraph.checkpoint.memory import MemorySaver

        from app.core.agent.multi_agent import AgentRole, MultiAgentOrchestrator

        mock_cp.return_value = MemorySaver()

        role = AgentRole(
            name="writer",
            display_name="Writer",
            system_prompt="Write.",
            output_field="draft",
        )

        with patch(f"{_MOD}._llm_call", return_value="Generated content"):
            orch = MultiAgentOrchestrator(roles=[role])
            result = orch.run("Write about AI")

        assert "output" in result
        assert "steps" in result
        assert "error" in result

    @patch(_GET_CP)
    def test_run_handles_exception(self, mock_cp):
        self._skip_if_unavailable()
        from langgraph.checkpoint.memory import MemorySaver

        from app.core.agent.multi_agent import AgentRole, MultiAgentOrchestrator

        mock_cp.return_value = MemorySaver()

        role = AgentRole(
            name="writer",
            display_name="Writer",
            system_prompt="Write.",
            output_field="draft",
        )
        orch = MultiAgentOrchestrator(roles=[role])
        # Force the graph to raise
        orch._graph = Mock()
        orch._graph.invoke = Mock(side_effect=RuntimeError("graph failed"))
        result = orch.run("test")
        assert result["error"] is not None
        assert "graph failed" in result["error"]

    @patch(_GET_CP)
    def test_get_graph_mermaid(self, mock_cp):
        self._skip_if_unavailable()
        from langgraph.checkpoint.memory import MemorySaver

        from app.core.agent.multi_agent import AgentRole, MultiAgentOrchestrator

        mock_cp.return_value = MemorySaver()

        role = AgentRole(
            name="solo",
            display_name="Solo",
            system_prompt="...",
            output_field="draft",
        )
        orch = MultiAgentOrchestrator(roles=[role])
        mermaid = orch.get_graph_mermaid()
        assert isinstance(mermaid, str)


# ===================================================================
# 7. Preset pipelines
# ===================================================================


class TestPresetPipelines:
    """Tests for preset_content_pipeline and preset_code_pipeline."""

    def _skip_if_unavailable(self):
        from app.core.agent import multi_agent as mod

        if not mod._LG_AVAILABLE:
            pytest.skip("langgraph not installed")

    @patch(_GET_CP)
    def test_content_pipeline_has_four_roles(self, mock_cp):
        self._skip_if_unavailable()
        from langgraph.checkpoint.memory import MemorySaver

        from app.core.agent.multi_agent import MultiAgentOrchestrator

        mock_cp.return_value = MemorySaver()

        orch = MultiAgentOrchestrator.preset_content_pipeline()
        names = [r.name for r in orch.roles]
        assert "researcher" in names
        assert "writer" in names
        assert "critic" in names
        assert len(orch.roles) == 4

    @patch(_GET_CP)
    def test_code_pipeline_has_four_roles(self, mock_cp):
        self._skip_if_unavailable()
        from langgraph.checkpoint.memory import MemorySaver

        from app.core.agent.multi_agent import MultiAgentOrchestrator

        mock_cp.return_value = MemorySaver()

        orch = MultiAgentOrchestrator.preset_code_pipeline()
        names = [r.name for r in orch.roles]
        assert "researcher" in names
        assert "coder" in names
        assert "reviewer" in names
        assert len(orch.roles) == 4


# ===================================================================
# 8. _assert_langgraph
# ===================================================================


class TestAssertLanggraph:
    def test_raises_when_unavailable(self):
        from app.core.agent import multi_agent as mod

        original = mod._LG_AVAILABLE
        try:
            mod._LG_AVAILABLE = False
            with pytest.raises(ImportError):
                mod._assert_langgraph()
        finally:
            mod._LG_AVAILABLE = original
