"""Unit tests for ToolRegistry.

Tests cover: tool registration, deduplication, schema generation,
execution with timeout enforcement, plugin registration, and error cases.
"""

from __future__ import annotations

import time

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_registry():
    from app.core.agent.tool_registry import ToolRegistry

    return ToolRegistry()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_simple_tool(self):
        reg = _get_registry()

        def my_tool(x: int) -> int:
            return x * 2

        reg.register_tool("my_tool", my_tool)
        defs = reg.get_definitions()
        names = [d["name"] for d in defs]
        assert "my_tool" in names

    def test_register_with_explicit_description(self):
        reg = _get_registry()
        reg.register_tool("t", lambda: None, description="explicit desc")
        defs = reg.get_definitions()
        assert any(d["description"] == "explicit desc" for d in defs)

    def test_register_with_explicit_parameters(self):
        reg = _get_registry()
        params = {"type": "object", "properties": {"x": {"type": "integer"}}}
        reg.register_tool("t", lambda x: x, parameters=params)
        defs = reg.get_definitions()
        d = next(d for d in defs if d["name"] == "t")
        assert d["parameters"] == params

    def test_duplicate_registration_updates_definition(self):
        reg = _get_registry()
        reg.register_tool("dup", lambda: "first", description="first")
        reg.register_tool("dup", lambda: "second", description="second")
        defs = reg.get_definitions()
        # Should only have one entry
        dup_defs = [d for d in defs if d["name"] == "dup"]
        assert len(dup_defs) == 1
        assert dup_defs[0]["description"] == "second"

    def test_multiple_tools_registered(self):
        reg = _get_registry()
        reg.register_tool("a", lambda: None)
        reg.register_tool("b", lambda: None)
        reg.register_tool("c", lambda: None)
        assert len(reg.get_definitions()) == 3


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class TestExecution:
    def test_execute_simple_tool(self):
        reg = _get_registry()
        reg.register_tool("add", lambda x, y: x + y)
        result = reg.execute("add", {"x": 3, "y": 4})
        assert result == 7

    def test_execute_unknown_tool_raises_value_error(self):
        reg = _get_registry()
        with pytest.raises(ValueError, match="not found"):
            reg.execute("nonexistent_tool", {})

    def test_execute_bad_args_raises_value_error(self):
        reg = _get_registry()
        reg.register_tool("strict", lambda x: x)
        with pytest.raises((ValueError, TypeError)):
            reg.execute("strict", {"unexpected_kwarg": 1})

    def test_execute_returns_tool_return_value(self):
        reg = _get_registry()
        reg.register_tool("greet", lambda name: f"hello {name}")
        assert reg.execute("greet", {"name": "world"}) == "hello world"


# ---------------------------------------------------------------------------
# Timeout enforcement
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_tool_timeout_raises_runtime_error(self, monkeypatch):
        import app.core.agent.tool_registry as tr_mod

        orig = tr_mod._TOOL_TIMEOUT
        monkeypatch.setattr(tr_mod, "_TOOL_TIMEOUT", 1)

        reg = _get_registry()

        def hang():
            time.sleep(30)

        reg.register_tool("hang", hang, parameters={"type": "object", "properties": {}})
        with pytest.raises(RuntimeError, match="timed out"):
            reg.execute("hang", {})

        monkeypatch.setattr(tr_mod, "_TOOL_TIMEOUT", orig)

    def test_fast_tool_completes_within_timeout(self):
        reg = _get_registry()
        reg.register_tool(
            "fast", lambda: 42, parameters={"type": "object", "properties": {}}
        )
        result = reg.execute("fast", {})
        assert result == 42


# ---------------------------------------------------------------------------
# Schema generation
# ---------------------------------------------------------------------------


class TestSchemaGeneration:
    def test_generates_schema_from_type_hints(self):
        reg = _get_registry()

        def typed(name: str, count: int) -> str:
            return name * count

        reg.register_tool("typed", typed)
        defs = reg.get_definitions()
        d = next(d for d in defs if d["name"] == "typed")
        assert "parameters" in d

    def test_fallback_description_from_docstring(self):
        reg = _get_registry()

        def documented() -> None:
            """This tool does something useful."""
            pass

        reg.register_tool("documented", documented)
        defs = reg.get_definitions()
        d = next(d for d in defs if d["name"] == "documented")
        assert "something useful" in d["description"]


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


class TestPluginRegistration:
    def test_register_plugin_registers_its_tools(self):
        from app.core.agent.base import AgentPlugin

        class FakePlugin(AgentPlugin):
            @property
            def name(self):
                return "fake"

            @property
            def description(self):
                return "fake plugin for testing"

            def get_tools(self):
                return [
                    {
                        "name": "plugin_tool",
                        "func": lambda: "ok",
                        "description": "from plugin",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ]

        reg = _get_registry()
        reg.register_plugin(FakePlugin())
        names = [d["name"] for d in reg.get_definitions()]
        assert "plugin_tool" in names

    def test_plugin_with_missing_func_skipped(self, caplog):
        from app.core.agent.base import AgentPlugin

        class BadPlugin(AgentPlugin):
            @property
            def name(self):
                return "bad"

            @property
            def description(self):
                return "bad plugin"

            def get_tools(self):
                return [{"name": "broken_tool", "description": "no func"}]

        reg = _get_registry()
        reg.register_plugin(BadPlugin())
        names = [d["name"] for d in reg.get_definitions()]
        assert "broken_tool" not in names
