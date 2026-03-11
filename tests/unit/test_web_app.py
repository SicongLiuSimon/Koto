"""Unit tests for web.app.Utils.is_failure_output().

Pure string-logic function — no mocks needed.
"""
from __future__ import annotations
import pytest


@pytest.fixture(scope="module")
def is_failure():
    from web.app import Utils
    return Utils.is_failure_output


# ---------------------------------------------------------------------------
# Empty / None / blank inputs
# ---------------------------------------------------------------------------

class TestEmptyInputs:
    def test_none_returns_true(self, is_failure):
        assert is_failure(None) is True

    def test_empty_string_returns_true(self, is_failure):
        assert is_failure("") is True

    def test_whitespace_only_returns_true(self, is_failure):
        assert is_failure("   ") is True

    def test_newline_only_returns_true(self, is_failure):
        assert is_failure("\n\n") is True


# ---------------------------------------------------------------------------
# Emoji / Chinese failure markers
# ---------------------------------------------------------------------------

class TestFailureMarkers:
    def test_starts_with_x_emoji_returns_true(self, is_failure):
        assert is_failure("❌ Operation failed") is True

    def test_contains_chinese_failure_returns_true(self, is_failure):
        assert is_failure("处理失败了") is True

    def test_contains_chinese_error_returns_true(self, is_failure):
        assert is_failure("发生了一个错误") is True


# ---------------------------------------------------------------------------
# Chinese no-internet phrases
# ---------------------------------------------------------------------------

class TestChineseNoInternetPhrases:
    @pytest.mark.parametrize("phrase", [
        "没有直接联网",
        "无法直接联网",
        "无法联网",
        "没有联网",
        "不能联网",
        "没有实时",
        "无法获取实时",
        "不能获取实时",
        "没有访问互联网",
        "无法访问互联网",
    ])
    def test_phrase_returns_true(self, is_failure, phrase):
        assert is_failure(f"很遗憾，我{phrase}所以无法回答。") is True


# ---------------------------------------------------------------------------
# English no-internet phrases
# ---------------------------------------------------------------------------

class TestEnglishNoInternetPhrases:
    @pytest.mark.parametrize("phrase", [
        "i don't have access to the internet",
        "i cannot access the internet",
        "i'm unable to access the internet",
        "no internet access",
        "i don't have real-time",
        "i cannot browse",
        "i can't browse",
    ])
    def test_phrase_returns_true(self, is_failure, phrase):
        assert is_failure(f"Sorry, {phrase} to answer your question.") is True

    def test_case_insensitive_matching(self, is_failure):
        # Phrases are checked in lowercased text
        assert is_failure("I CANNOT BROWSE the internet for that.") is True

    def test_phrase_embedded_in_longer_text(self, is_failure):
        assert is_failure(
            "As an AI language model, I cannot browse external websites. "
            "Please try a search engine."
        ) is True


# ---------------------------------------------------------------------------
# Valid / normal outputs that should return False
# ---------------------------------------------------------------------------

class TestValidOutputs:
    def test_normal_text_returns_false(self, is_failure):
        assert is_failure("The capital of France is Paris.") is False

    def test_code_snippet_returns_false(self, is_failure):
        assert is_failure("def hello():\n    return 'world'") is False

    def test_long_response_returns_false(self, is_failure):
        long_text = "Here is a detailed explanation. " * 50
        assert is_failure(long_text) is False

    def test_chinese_normal_text_returns_false(self, is_failure):
        assert is_failure("法国的首都是巴黎，位于塞纳河畔。") is False

    def test_number_only_returns_false(self, is_failure):
        assert is_failure("42") is False

    def test_apologise_in_middle_is_not_flagged(self, is_failure):
        # Starts with "I apologize" should be True, but buried apology is fine
        assert is_failure("Your code runs well. I apologize for the delay.") is False
