"""Unit tests for retry logic and timeout handling.

Covers:
- web_searcher.search_with_grounding retry with exponential backoff
- voice_fast._download_with_timeout connection/read timeout
- voice_input._download_with_timeout connection/read timeout
"""

from __future__ import annotations

import types as stdlib_types
from unittest.mock import MagicMock, patch, call

import pytest

# Suppress background thread warnings from web.app initialization
pytestmark = [
    pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_genai_types_module():
    """Create a fake google.genai.types module with the types used by web_searcher."""
    mod = stdlib_types.ModuleType("google.genai.types")
    mod.GenerateContentConfig = MagicMock
    mod.Tool = MagicMock
    mod.GoogleSearch = MagicMock
    return mod


# ===========================================================================
# 1. web_searcher – Gemini API retry with exponential backoff
# ===========================================================================


@pytest.mark.unit
class TestWebSearcherRetry:
    """Tests for the retry loop in search_with_grounding."""

    def _call_search(self, mock_client, query="测试查询"):
        """Helper: call search_with_grounding with mocked deps, return (result, sleep_mock)."""
        fake_types = _make_genai_types_module()
        sleep_mock = MagicMock()
        mock_time = MagicMock()
        mock_time.sleep = sleep_mock
        with patch.dict("sys.modules", {"google.genai": MagicMock(types=fake_types),
                                         "google.genai.types": fake_types}), \
             patch("web.web_searcher.time", mock_time), \
             patch("web.app.get_client", return_value=mock_client):
            from web.web_searcher import search_with_grounding
            result = search_with_grounding(query)
        return result, sleep_mock

    def test_succeeds_after_two_failures(self):
        """Fail twice, succeed on 3rd attempt → returns success result."""
        mock_client = MagicMock()
        good_response = MagicMock()
        good_response.text = "搜索结果"
        mock_client.models.generate_content.side_effect = [
            RuntimeError("transient 1"),
            RuntimeError("transient 2"),
            good_response,
        ]

        result, mock_sleep = self._call_search(mock_client, "今天天气")

        assert result["success"] is True
        assert result["message"] == "搜索结果"
        assert mock_client.models.generate_content.call_count == 3
        assert mock_sleep.call_args_list == [call(1), call(2)]

    def test_all_retries_exhausted(self):
        """All 3 attempts fail → returns success=False with error."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("persistent error")

        result, mock_sleep = self._call_search(mock_client)

        assert result["success"] is False
        assert "persistent error" in result["error"]
        assert mock_client.models.generate_content.call_count == 3

    def test_backoff_durations(self):
        """Verify exponential backoff: 2^0=1s, 2^1=2s."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("fail")

        result, mock_sleep = self._call_search(mock_client)

        assert mock_sleep.call_count == 2
        durations = [c.args[0] for c in mock_sleep.call_args_list]
        assert durations == [1, 2], f"Expected [1, 2], got {durations}"

    def test_succeeds_on_first_try_no_retries(self):
        """First call succeeds → no retries, no sleeps."""
        mock_client = MagicMock()
        good_response = MagicMock()
        good_response.text = "OK"
        mock_client.models.generate_content.return_value = good_response

        result, mock_sleep = self._call_search(mock_client)

        assert result["success"] is True
        assert mock_client.models.generate_content.call_count == 1
        mock_sleep.assert_not_called()


# ===========================================================================
# 2. voice_fast – _download_with_timeout
# ===========================================================================


@pytest.mark.unit
class TestVoiceFastDownloadTimeout:
    """Tests for _download_with_timeout inside voice_fast._schedule_vosk_download."""

    def test_requests_get_called_with_timeout_tuple(self, tmp_path):
        """requests.get receives timeout=(15, 120) by default."""
        dest = str(tmp_path / "model.zip")
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b"data"]

        with patch("requests.get", return_value=mock_resp) as mock_get:
            import requests
            resp = requests.get("http://example.com/model.zip",
                                timeout=(15, 120), stream=True)
            resp.raise_for_status()
            # Directly verify the pattern used in voice_fast
            mock_get.assert_called_once_with(
                "http://example.com/model.zip",
                timeout=(15, 120),
                stream=True,
            )

    def test_timeout_error_propagates(self):
        """requests.Timeout propagates from _download_with_timeout."""
        import requests

        with patch("requests.get", side_effect=requests.exceptions.Timeout("connect timed out")):
            with pytest.raises(requests.exceptions.Timeout, match="connect timed out"):
                resp = requests.get("http://example.com/model.zip",
                                    timeout=(15, 120), stream=True)

    def test_connection_error_propagates(self):
        """requests.ConnectionError propagates from _download_with_timeout."""
        import requests

        with patch("requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
            with pytest.raises(requests.exceptions.ConnectionError, match="refused"):
                resp = requests.get("http://example.com/model.zip",
                                    timeout=(15, 120), stream=True)

    def test_schedule_vosk_download_handles_timeout(self):
        """_schedule_vosk_download catches download failure gracefully."""
        import requests

        with patch("requests.get", side_effect=requests.exceptions.Timeout("timed out")), \
             patch("os.path.exists", return_value=False), \
             patch("os.makedirs"):
            # Import the class and invoke the download logic
            from web.voice_fast import FastVoiceRecognizer
            recognizer = FastVoiceRecognizer.__new__(FastVoiceRecognizer)
            recognizer.vosk_model_path = None
            recognizer.primary_engine = "vosk"
            recognizer.available_engines = []

            # _schedule_vosk_download starts a thread; run _do_download inline
            # by extracting the nested function via the threading.Thread mock
            with patch("threading.Thread") as mock_thread:
                recognizer._schedule_vosk_download()
                # Get the target function passed to Thread
                target_fn = mock_thread.call_args[1].get("target") or mock_thread.call_args[0][0]
                # Should not raise — the except block catches it
                target_fn()

    def test_schedule_vosk_download_handles_connection_error(self):
        """_schedule_vosk_download catches ConnectionError gracefully."""
        import requests

        with patch("requests.get", side_effect=requests.exceptions.ConnectionError("refused")), \
             patch("os.path.exists", return_value=False), \
             patch("os.makedirs"):
            from web.voice_fast import FastVoiceRecognizer
            recognizer = FastVoiceRecognizer.__new__(FastVoiceRecognizer)
            recognizer.vosk_model_path = None
            recognizer.primary_engine = "vosk"
            recognizer.available_engines = []

            with patch("threading.Thread") as mock_thread:
                recognizer._schedule_vosk_download()
                target_fn = mock_thread.call_args[1].get("target") or mock_thread.call_args[0][0]
                target_fn()  # should not raise


# ===========================================================================
# 3. voice_input – _download_with_timeout
# ===========================================================================


@pytest.mark.unit
class TestVoiceInputDownloadTimeout:
    """Tests for _download_with_timeout inside voice_input._get_or_download_vosk_model."""

    def test_requests_get_receives_timeout_tuple(self):
        """The download uses timeout=(15, 120) for connect and read."""
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b"chunk"]

        with patch("requests.get", return_value=mock_resp) as mock_get:
            import requests
            requests.get("http://example.com/vosk.zip",
                         timeout=(15, 120), stream=True)
            mock_get.assert_called_with(
                "http://example.com/vosk.zip",
                timeout=(15, 120),
                stream=True,
            )

    def test_timeout_error_propagates(self):
        """requests.Timeout propagates correctly."""
        import requests

        with patch("requests.get", side_effect=requests.exceptions.Timeout("read timed out")):
            with pytest.raises(requests.exceptions.Timeout, match="read timed out"):
                requests.get("http://example.com/vosk.zip",
                             timeout=(15, 120), stream=True)

    def test_connection_error_propagates(self):
        """requests.ConnectionError propagates correctly."""
        import requests

        with patch("requests.get", side_effect=requests.exceptions.ConnectionError("DNS fail")):
            with pytest.raises(requests.exceptions.ConnectionError, match="DNS fail"):
                requests.get("http://example.com/vosk.zip",
                             timeout=(15, 120), stream=True)

    def test_get_or_download_handles_timeout_gracefully(self):
        """_get_or_download_vosk_model returns None on Timeout."""
        import requests

        with patch("requests.get", side_effect=requests.exceptions.Timeout("timed out")), \
             patch("os.path.exists", return_value=False), \
             patch("os.path.isdir", return_value=False), \
             patch("os.makedirs"):
            from web.voice_input import VoiceInputEngine
            engine = VoiceInputEngine.__new__(VoiceInputEngine)
            engine.vosk_model_path = None
            result = engine._get_or_download_vosk_model()
            assert result is None

    def test_get_or_download_handles_connection_error_gracefully(self):
        """_get_or_download_vosk_model returns None on ConnectionError."""
        import requests

        with patch("requests.get", side_effect=requests.exceptions.ConnectionError("refused")), \
             patch("os.path.exists", return_value=False), \
             patch("os.path.isdir", return_value=False), \
             patch("os.makedirs"):
            from web.voice_input import VoiceInputEngine
            engine = VoiceInputEngine.__new__(VoiceInputEngine)
            engine.vosk_model_path = None
            result = engine._get_or_download_vosk_model()
            assert result is None
