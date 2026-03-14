"""Unit tests for datetime injection in UnifiedAgent.run().

Verifies that the current local time is prepended to the first user
message in the conversation history on every call to run(). This keeps
system_instruction stable for Gemini context caching while still giving
the model temporal context.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.core.agent.types import AgentStep, AgentStepType
from app.core.agent.unified_agent import UnifiedAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DATETIME_PATTERN = re.compile(
    r"当前本地时间：\d{4}年\d{2}月\d{2}日 \d{2}:\d{2}（周[一二三四五六日]）"
)

WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _make_agent(system_instruction: Optional[str] = None) -> UnifiedAgent:
    """Return a UnifiedAgent with a fake LLM provider and no tools."""
    fake_llm = MagicMock()
    # generate_content returns a minimal "ANSWER" response
    fake_llm.generate_content.return_value = {
        "content": "ok",
        "tool_calls": [],
    }
    agent = UnifiedAgent(
        llm_provider=fake_llm,
        system_instruction=system_instruction,
        use_tool_router=False,
        enable_pii_filter=False,
        enable_output_validation=False,
    )
    return agent


def _run_and_capture_user_message(agent: UnifiedAgent, message: str = "hello") -> str:
    """Run agent once and return the system_instruction or user message containing datetime.

    The datetime is injected into system_instruction (not user message) to keep
    Gemini context caching stable. We check system_instruction first.
    """
    for _ in agent.run(input_text=message):
        break
    call_kwargs = agent.llm.generate_content.call_args
    if call_kwargs is None:
        return ""
    # Check system_instruction first (new behavior)
    sys_instr = call_kwargs.kwargs.get("system_instruction", "")
    if sys_instr:
        return sys_instr
    # Fallback: check prompt user message (old behavior)
    prompt = call_kwargs.kwargs.get("prompt") or (
        call_kwargs.args[0] if call_kwargs.args else []
    )
    for turn in prompt:
        if isinstance(turn, dict) and turn.get("role") == "user":
            return turn.get("content", "")
    return ""


# Keep the old name as an alias so test methods below read clearly.
_run_and_capture_instruction = _run_and_capture_user_message


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDatetimeInjection:
    def test_instruction_contains_datetime_prefix(self):
        """User message sent to LLM must start with a datetime line."""
        agent = _make_agent()
        user_msg = _run_and_capture_instruction(agent)
        assert _DATETIME_PATTERN.search(
            user_msg
        ), f"Expected datetime prefix in user message, got: {user_msg[:200]!r}"

    def test_datetime_prefix_is_first_line(self):
        """The datetime prefix must appear at the very beginning of the user message."""
        agent = _make_agent()
        user_msg = _run_and_capture_instruction(agent)
        assert user_msg.startswith(
            "当前本地时间："
        ), f"User message should start with datetime prefix, got: {user_msg[:100]!r}"

    def test_original_instruction_preserved_after_prefix(self):
        """The original user input must still be present in the prompt."""
        custom_input = "You are a specialized assistant for tests."
        agent = _make_agent()
        for _ in agent.run(input_text=custom_input):
            break
        call_kwargs = agent.llm.generate_content.call_args
        prompt = call_kwargs.kwargs.get("prompt") or (
            call_kwargs.args[0] if call_kwargs.args else []
        )
        user_content = ""
        for turn in prompt:
            if isinstance(turn, dict) and turn.get("role") == "user":
                user_content = turn.get("content", "")
                break
        assert (
            custom_input in user_content
        ), "Original user input was lost in prompt."

    def test_weekday_label_is_correct(self):
        """The injected weekday label must match the frozen datetime's weekday."""
        fixed_dt = datetime(2026, 3, 11, 14, 0, 0)  # Wednesday → 周三
        expected_weekday = WEEKDAY_LABELS[fixed_dt.weekday()]

        agent = _make_agent()
        with patch("app.core.agent.unified_agent.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_dt
            user_msg = _run_and_capture_instruction(agent)

        assert (
            expected_weekday in user_msg
        ), f"Expected weekday '{expected_weekday}' in user message, got: {user_msg[:200]!r}"

    def test_date_and_time_values_are_correct(self):
        """The injected date/time string must match the frozen datetime."""
        fixed_dt = datetime(2026, 3, 11, 9, 5, 0)
        expected_str = "2026年03月11日 09:05"

        agent = _make_agent()
        with patch("app.core.agent.unified_agent.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_dt
            user_msg = _run_and_capture_instruction(agent)

        assert (
            expected_str in user_msg
        ), f"Expected '{expected_str}' in user message, got: {user_msg[:200]!r}"

    def test_datetime_refreshed_on_each_call(self):
        """Each call to run() must inject a fresh datetime, not a cached one."""
        dt1 = datetime(2026, 3, 11, 10, 0, 0)
        dt2 = datetime(2026, 3, 11, 11, 30, 0)

        agent = _make_agent()
        agent.llm.generate_content.return_value = {"content": "ok", "tool_calls": []}

        with patch("app.core.agent.unified_agent.datetime") as mock_dt:
            mock_dt.now.return_value = dt1
            for _ in agent.run(input_text="first call"):
                break
            call1 = agent.llm.generate_content.call_args
            instr1 = call1.kwargs.get("system_instruction", "")
            if not instr1:
                prompt1 = call1.kwargs.get("prompt", [])
                instr1 = next(
                    (t["content"] for t in prompt1 if t.get("role") == "user"), ""
                )

        with patch("app.core.agent.unified_agent.datetime") as mock_dt:
            mock_dt.now.return_value = dt2
            for _ in agent.run(input_text="second call"):
                break
            call2 = agent.llm.generate_content.call_args
            instr2 = call2.kwargs.get("system_instruction", "")
            if not instr2:
                prompt2 = call2.kwargs.get("prompt", [])
                instr2 = next(
                    (t["content"] for t in prompt2 if t.get("role") == "user"), ""
                )

        assert "10:00" in instr1, f"First call: expected 10:00 in {instr1[:100]!r}"
        assert "11:30" in instr2, f"Second call: expected 11:30 in {instr2[:100]!r}"
        assert instr1 != instr2, "User messages for different times should differ"
