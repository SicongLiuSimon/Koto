"""Tests for app.core.agent.langgraph_agent module."""

import json
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers – lightweight fakes so tests don't need real langgraph installed
# ---------------------------------------------------------------------------


class _FakeAIMessage:
    """Minimal stand-in for langchain_core.messages.AIMessage."""

    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeToolMessage:
    def __init__(self, tool_call_id="", content=""):
        self.tool_call_id = tool_call_id
        self.content = content


class _FakeHumanMessage:
    def __init__(self, content=""):
        self.content = content


class _FakeSystemMessage:
    def __init__(self, content=""):
        self.content = content


# ---------------------------------------------------------------------------
# Patch targets (module-level symbols inside langgraph_agent)
# ---------------------------------------------------------------------------
_MOD = "app.core.agent.langgraph_agent"
_LLM_CLS = "app.core.llm.langchain_adapter.KotoLangChainLLM"
_TOOL_REG = "app.core.agent.tool_registry.ToolRegistry"
_GET_CP = "app.core.agent.checkpoint_manager.get_checkpointer"


# ===================================================================
# 1. Tests for routing functions (_route_after_reason, _route_after_validate)
# ===================================================================


class TestRouteAfterReason:
    """Tests for the _route_after_reason routing function."""

    def _get_route_fn(self):
        from app.core.agent.langgraph_agent import _route_after_reason

        return _route_after_reason

    def test_returns_end_on_error(self):
        """If state has an error, route to END."""
        fn = self._get_route_fn()
        state = {"messages": [], "error": "something broke"}
        # END is the string "__end__"
        result = fn(state)
        assert result == "__end__"

    def test_routes_to_call_tools_when_ai_has_tool_calls(self):
        """If last message is AIMessage with tool_calls, go to call_tools."""
        fn = self._get_route_fn()
        ai_msg = _FakeAIMessage(
            content="thinking...", tool_calls=[{"name": "search", "args": {}}]
        )

        with patch(f"{_MOD}.AIMessage", _FakeAIMessage):
            with patch(
                f"{_MOD}.isinstance",
                side_effect=lambda obj, cls: type(obj).__name__ == cls.__name__,
                create=True,
            ):
                pass

        # Direct approach: patch isinstance at the call site won't work cleanly;
        # instead make the object pass the isinstance check by using the real class
        # if available, or just test the logic directly.
        from app.core.agent import langgraph_agent as mod

        if not mod._LG_AVAILABLE:
            pytest.skip("langgraph not installed")
        from langchain_core.messages import AIMessage

        msg = AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "1"}])
        state = {"messages": [msg], "error": None}
        assert fn(state) == "call_tools"

    def test_routes_to_validate_when_no_tool_calls(self):
        """If last message is AIMessage without tool_calls, go to validate."""
        fn = self._get_route_fn()
        from app.core.agent import langgraph_agent as mod

        if not mod._LG_AVAILABLE:
            pytest.skip("langgraph not installed")
        from langchain_core.messages import AIMessage

        msg = AIMessage(content="final answer")
        state = {"messages": [msg], "error": None}
        assert fn(state) == "validate"

    def test_routes_to_validate_with_empty_messages(self):
        """Empty messages list should route to validate (no tool calls)."""
        fn = self._get_route_fn()
        state = {"messages": [], "error": None}
        assert fn(state) == "validate"


class TestRouteAfterValidate:
    """Tests for _route_after_validate."""

    def _get_route_fn(self):
        from app.core.agent.langgraph_agent import _route_after_validate

        return _route_after_validate

    def test_returns_end_when_final_answer_present(self):
        state = {"validation_retries": 1, "final_answer": "done"}
        assert self._get_route_fn()(state) == "__end__"

    def test_returns_reason_when_retries_and_no_answer(self):
        state = {"validation_retries": 1, "final_answer": None}
        assert self._get_route_fn()(state) == "reason"

    def test_returns_end_when_no_retries(self):
        state = {"validation_retries": 0, "final_answer": None}
        assert self._get_route_fn()(state) == "__end__"


# ===================================================================
# 2. Tests for _make_nodes (node_reason, node_call_tools, node_validate)
# ===================================================================


class TestMakeNodes:
    """Tests for node functions generated by _make_nodes."""

    def _skip_if_unavailable(self):
        from app.core.agent import langgraph_agent as mod

        if not mod._LG_AVAILABLE:
            pytest.skip("langgraph not installed")

    def _make(self, llm=None, registry=None, **kw):
        from app.core.agent.langgraph_agent import _make_nodes

        _llm = llm or Mock()
        _reg = registry or Mock(get_definitions=Mock(return_value=[]))
        defaults = dict(
            system_instruction="You are Koto.",
            enable_pii=False,
            enable_validation=False,
            restore_pii=False,
        )
        defaults.update(kw)
        return _make_nodes(llm=_llm, registry=_reg, **defaults)

    # -- node_reason -------------------------------------------------------

    def test_node_reason_calls_llm_and_increments_steps(self):
        self._skip_if_unavailable()
        from langchain_core.messages import AIMessage, SystemMessage

        ai_resp = AIMessage(content="I'll help you!")
        llm = Mock()
        llm.invoke = Mock(return_value=ai_resp)
        node_reason, _, _ = self._make(llm=llm)

        state = {
            "messages": [_FakeHumanMessage("hello")],
            "steps_taken": 0,
            "error": None,
        }
        result = node_reason(state)
        assert result["steps_taken"] == 1
        assert llm.invoke.called

    def test_node_reason_stops_at_max_steps(self):
        self._skip_if_unavailable()
        node_reason, _, _ = self._make()
        state = {"messages": [], "steps_taken": 15}
        result = node_reason(state)
        assert result.get("error") == "MAX_STEPS_EXCEEDED"
        assert "final_answer" in result

    def test_node_reason_handles_llm_exception(self):
        self._skip_if_unavailable()
        from langchain_core.messages import SystemMessage

        llm = Mock()
        llm.invoke = Mock(side_effect=RuntimeError("timeout"))
        node_reason, _, _ = self._make(llm=llm)

        state = {"messages": [], "steps_taken": 0, "error": None}
        result = node_reason(state)
        assert "error" in result
        assert "timeout" in result["error"]

    # -- node_call_tools ---------------------------------------------------

    def test_node_call_tools_executes_tool_calls(self):
        self._skip_if_unavailable()
        from langchain_core.messages import AIMessage

        registry = Mock()
        registry.get_definitions = Mock(return_value=[{"name": "search"}])
        registry.execute = Mock(return_value="search result text")
        _, node_call_tools, _ = self._make(registry=registry)

        ai_msg = AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {"q": "test"}, "id": "tc1"}],
        )
        state = {"messages": [ai_msg]}
        result = node_call_tools(state)
        msgs = result.get("messages", [])
        assert len(msgs) == 1
        assert msgs[0].content == "search result text"

    def test_node_call_tools_handles_execution_error(self):
        self._skip_if_unavailable()
        from langchain_core.messages import AIMessage

        registry = Mock()
        registry.get_definitions = Mock(return_value=[{"name": "bad_tool"}])
        registry.execute = Mock(side_effect=Exception("tool crash"))
        _, node_call_tools, _ = self._make(registry=registry)

        ai_msg = AIMessage(
            content="",
            tool_calls=[{"name": "bad_tool", "args": {}, "id": "tc2"}],
        )
        state = {"messages": [ai_msg]}
        result = node_call_tools(state)
        assert (
            "工具错误" in result["messages"][0].content
            or "tool crash" in result["messages"][0].content
        )

    # -- node_validate -----------------------------------------------------

    def test_node_validate_returns_final_answer_when_validation_disabled(self):
        self._skip_if_unavailable()
        from langchain_core.messages import AIMessage

        _, _, node_validate = self._make(enable_validation=False, restore_pii=False)

        state = {
            "messages": [AIMessage(content="Great answer")],
            "validation_retries": 0,
            "pii_mask_result": None,
            "original_input": "test",
            "skill_id": None,
        }
        result = node_validate(state)
        assert result["final_answer"] == "Great answer"


# ===================================================================
# 3. Tests for build_graph
# ===================================================================


class TestBuildGraph:
    """Tests for the build_graph factory function."""

    def test_build_graph_returns_compiled_graph(self):
        from app.core.agent import langgraph_agent as mod

        if not mod._LG_AVAILABLE:
            pytest.skip("langgraph not installed")

        registry = Mock()
        registry.get_definitions = Mock(return_value=[])

        with patch(_LLM_CLS, autospec=False) as mock_llm_cls:
            mock_llm = Mock()
            mock_llm.bind_tools = Mock(return_value=mock_llm)
            mock_llm_cls.return_value = mock_llm

            with patch(_GET_CP) as mock_cp:
                from langgraph.checkpoint.memory import MemorySaver

                mock_cp.return_value = MemorySaver()

                graph = mod.build_graph(
                    registry=registry,
                    enable_pii=False,
                    enable_validation=False,
                )
                assert hasattr(graph, "invoke")
                assert hasattr(graph, "stream")


# ===================================================================
# 4. Tests for LangGraphAgent wrapper
# ===================================================================


class TestLangGraphAgent:
    """Tests for the LangGraphAgent high-level wrapper."""

    def _skip_if_unavailable(self):
        from app.core.agent import langgraph_agent as mod

        if not mod._LG_AVAILABLE:
            pytest.skip("langgraph not installed")

    @patch(f"{_MOD}.build_graph")
    @patch(_TOOL_REG, autospec=False)
    def test_init_creates_graph(self, mock_tr_cls, mock_bg):
        self._skip_if_unavailable()
        mock_tr_cls.return_value = Mock()
        mock_bg.return_value = Mock()

        from app.core.agent.langgraph_agent import LangGraphAgent

        agent = LangGraphAgent(registry=Mock())
        assert mock_bg.called

    @patch(f"{_MOD}.build_graph")
    @patch(_TOOL_REG, autospec=False)
    def test_invoke_returns_final_answer(self, mock_tr_cls, mock_bg):
        self._skip_if_unavailable()
        from langchain_core.messages import AIMessage

        fake_graph = Mock()
        fake_graph.invoke = Mock(
            return_value={
                "final_answer": "Hello!",
                "messages": [AIMessage(content="Hello!")],
            }
        )
        mock_bg.return_value = fake_graph

        from app.core.agent.langgraph_agent import LangGraphAgent

        agent = LangGraphAgent(
            registry=Mock(),
            enable_pii_filter=False,
            enable_output_validation=False,
        )
        result = agent.invoke("Hi")
        assert result == "Hello!"

    @patch(f"{_MOD}.build_graph")
    @patch(_TOOL_REG, autospec=False)
    def test_invoke_fallback_to_last_message(self, mock_tr_cls, mock_bg):
        self._skip_if_unavailable()
        from langchain_core.messages import AIMessage

        fake_graph = Mock()
        fake_graph.invoke = Mock(
            return_value={
                "final_answer": None,
                "messages": [AIMessage(content="fallback content")],
            }
        )
        mock_bg.return_value = fake_graph

        from app.core.agent.langgraph_agent import LangGraphAgent

        agent = LangGraphAgent(
            registry=Mock(),
            enable_pii_filter=False,
            enable_output_validation=False,
        )
        result = agent.invoke("Hi")
        assert result == "fallback content"

    @patch(f"{_MOD}.build_graph")
    @patch(_TOOL_REG, autospec=False)
    def test_stream_yields_events(self, mock_tr_cls, mock_bg):
        self._skip_if_unavailable()
        from langchain_core.messages import AIMessage, ToolMessage

        ai_msg = AIMessage(content="thinking...")
        tool_msg = ToolMessage(tool_call_id="t1", content="result")
        final_update = {"final_answer": "done", "messages": []}

        fake_graph = Mock()
        fake_graph.stream = Mock(
            return_value=iter(
                [
                    {"reason": {"messages": [ai_msg]}},
                    {"call_tools": {"messages": [tool_msg]}},
                    {"validate": final_update},
                ]
            )
        )
        mock_bg.return_value = fake_graph

        from app.core.agent.langgraph_agent import LangGraphAgent

        agent = LangGraphAgent(
            registry=Mock(),
            enable_pii_filter=False,
            enable_output_validation=False,
        )
        events = list(agent.stream("Hello"))
        types = [e["type"] for e in events]
        assert "token" in types
        assert "tool_result" in types
        assert "answer" in types

    @patch(f"{_MOD}.build_graph")
    @patch(_TOOL_REG, autospec=False)
    def test_stream_handles_exception(self, mock_tr_cls, mock_bg):
        self._skip_if_unavailable()
        fake_graph = Mock()
        fake_graph.stream = Mock(side_effect=RuntimeError("stream exploded"))
        mock_bg.return_value = fake_graph

        from app.core.agent.langgraph_agent import LangGraphAgent

        agent = LangGraphAgent(
            registry=Mock(),
            enable_pii_filter=False,
            enable_output_validation=False,
        )
        events = list(agent.stream("test"))
        assert any(e["type"] == "error" for e in events)

    @patch(f"{_MOD}.build_graph")
    @patch(_TOOL_REG, autospec=False)
    def test_get_graph_mermaid(self, mock_tr_cls, mock_bg):
        self._skip_if_unavailable()
        fake_graph = Mock()
        fake_inner = Mock()
        fake_inner.draw_mermaid = Mock(return_value="graph TD;")
        fake_graph.get_graph = Mock(return_value=fake_inner)
        mock_bg.return_value = fake_graph

        from app.core.agent.langgraph_agent import LangGraphAgent

        agent = LangGraphAgent(
            registry=Mock(), enable_pii_filter=False, enable_output_validation=False
        )
        assert "graph" in agent.get_graph_mermaid().lower()


# ===================================================================
# 5. Test _assert_langgraph
# ===================================================================


class TestAssertLanggraph:
    def test_assert_succeeds_when_available(self):
        from app.core.agent import langgraph_agent as mod

        if not mod._LG_AVAILABLE:
            pytest.skip("langgraph not installed")
        mod._assert_langgraph()  # should not raise

    def test_assert_raises_when_unavailable(self):
        from app.core.agent import langgraph_agent as mod

        original = mod._LG_AVAILABLE
        try:
            mod._LG_AVAILABLE = False
            with pytest.raises(ImportError, match="langgraph is required"):
                mod._assert_langgraph()
        finally:
            mod._LG_AVAILABLE = original
