# -*- coding: utf-8 -*-
"""Unit tests for the _load_history helper in app.api.agent_routes."""

import json
import os
import shutil
import tempfile
from unittest.mock import patch

import pytest

from app.api.agent_routes import _load_history


@pytest.fixture()
def temp_chat_dir():
    d = tempfile.mkdtemp(prefix="koto_chat_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.unit
class TestLoadHistory:
    def test_empty_session_id_returns_empty(self):
        """_load_history('') returns [] without touching the filesystem."""
        assert _load_history("") == []

    def test_nonexistent_session_returns_empty(self, temp_chat_dir):
        """No matching .json file → returns []."""
        with patch("app.api.agent_routes._get_chats_dir", return_value=temp_chat_dir):
            result = _load_history("nonexistent_abc123")
        assert result == []

    def test_loads_messages_from_file(self, temp_chat_dir):
        """Reads JSON and converts {role, parts} → {role, content}."""
        data = [
            {"role": "user", "parts": ["hello"]},
            {"role": "assistant", "parts": ["hi"]},
        ]
        session_file = os.path.join(temp_chat_dir, "test_session.json")
        with open(session_file, "w") as f:
            json.dump(data, f)

        with patch("app.api.agent_routes._get_chats_dir", return_value=temp_chat_dir):
            result = _load_history("test_session")

        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "hello"}
        assert result[1] == {"role": "assistant", "content": "hi"}

    def test_token_budget_truncates_old_messages(self, temp_chat_dir):
        """Token budget retains newest messages; old ones are dropped once budget
        would be exceeded.

        Each message content is exactly 100 chars → 100 // 4 = 25 estimated tokens.
        token_budget=60:
          newest (msg[4]): 0 + 25 = 25 ≤ 60 → add, budget=25
          msg[3]:          25 + 25 = 50 ≤ 60 → add, budget=50
          msg[2]:          50 + 25 = 75 > 60 → stop
        Expected: 2 newest messages returned.
        """
        # str(i).zfill(4) + "a"*96 → exactly 100 chars per content
        data = [
            {"role": "user", "parts": [str(i).zfill(4) + "a" * 96]} for i in range(5)
        ]
        session_file = os.path.join(temp_chat_dir, "budget_session.json")
        with open(session_file, "w") as f:
            json.dump(data, f)

        with patch("app.api.agent_routes._get_chats_dir", return_value=temp_chat_dir):
            result = _load_history("budget_session", token_budget=60)

        assert len(result) == 2
        # Newest messages are prioritised
        assert result[-1]["content"] == data[4]["parts"][0]
        assert result[0]["content"] == data[3]["parts"][0]

    def test_max_turns_limits_returned_messages(self, temp_chat_dir):
        """max_turns caps the window of messages considered."""
        data = [{"role": "user", "parts": [f"msg {i}"]} for i in range(25)]
        session_file = os.path.join(temp_chat_dir, "long_session.json")
        with open(session_file, "w") as f:
            json.dump(data, f)

        with patch("app.api.agent_routes._get_chats_dir", return_value=temp_chat_dir):
            result = _load_history("long_session", max_turns=5)

        assert len(result) <= 5
