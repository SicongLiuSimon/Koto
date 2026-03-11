# -*- coding: utf-8 -*-
"""Unit tests for app.core.security.output_validator.OutputValidator."""
import logging
import pytest
from app.core.security.output_validator import OutputValidator, ValidationResult

_LOGGER = "app.core.security.output_validator"


@pytest.mark.unit
class TestValidationResult:
    def test_passed_property_for_pass(self):
        r = ValidationResult(action="PASS", text="ok", original_text="ok")
        assert r.passed is True

    def test_passed_property_for_warn(self):
        r = ValidationResult(action="WARN", text="ok", original_text="ok")
        assert r.passed is True

    def test_passed_property_for_reformat(self):
        r = ValidationResult(action="REFORMAT", text="ok", original_text="ok")
        assert r.passed is True

    def test_needs_retry(self):
        r = ValidationResult(action="RETRY", text="x", original_text="x")
        assert r.needs_retry is True
        assert r.passed is False

    def test_is_blocked(self):
        r = ValidationResult(action="BLOCK", text="x", original_text="x")
        assert r.is_blocked is True
        assert r.passed is False


@pytest.mark.unit
class TestOutputValidatorEmptyInput:
    def test_empty_string_triggers_retry(self):
        result = OutputValidator.validate("")
        assert result.action == "RETRY"
        assert result.needs_retry

    def test_whitespace_only_triggers_retry(self):
        result = OutputValidator.validate("   ")
        assert result.action == "RETRY"

    def test_none_like_empty_triggers_retry(self):
        result = OutputValidator.validate(None)  # type: ignore[arg-type]
        assert result.action == "RETRY"


@pytest.mark.unit
class TestOutputValidatorPassCases:
    def test_normal_text_passes(self):
        result = OutputValidator.validate("这是一段正常的 AI 回复，内容准确、完整。")
        assert result.action == "PASS"
        assert result.passed

    def test_markdown_text_passes(self):
        text = "## 摘要\n\n- 要点一\n- 要点二\n\n结论：内容完整。"
        result = OutputValidator.validate(text)
        assert result.action == "PASS"

    def test_english_normal_text_passes(self):
        result = OutputValidator.validate("Here is a comprehensive answer to your question.")
        assert result.action == "PASS"


@pytest.mark.unit
class TestOutputValidatorRefusal:
    def test_english_refusal_triggers_retry(self):
        result = OutputValidator.validate("I cannot help with that request.")
        assert result.action == "RETRY"

    def test_chinese_refusal_triggers_retry(self):
        result = OutputValidator.validate("我无法回答这个问题，因为它违反了规定。")
        assert result.action == "RETRY"

    def test_sorry_refusal_triggers_retry(self):
        result = OutputValidator.validate("Sorry, I cannot assist with this.")
        assert result.action == "RETRY"

    def test_refusal_only_matches_start_of_text(self):
        # A sentence mentioning "cannot" in the middle should pass
        result = OutputValidator.validate("The system cannot be accessed because it is offline.")
        assert result.action == "PASS"


@pytest.mark.unit
class TestOutputValidatorTruncation:
    def test_ellipsis_ending_triggers_warn(self):
        result = OutputValidator.validate("This is an answer that seems to be cut off...")
        assert result.action == "WARN"

    def test_chinese_truncation_triggers_warn(self):
        result = OutputValidator.validate("内容如下\n（未完）")
        assert result.action == "WARN"

    def test_unicode_ellipsis_triggers_warn(self):
        result = OutputValidator.validate("答案是这样的…")
        assert result.action == "WARN"


@pytest.mark.unit
class TestOutputValidatorLeaks:
    def test_pii_placeholder_triggers_block(self):
        result = OutputValidator.validate("用户的手机是 <<手机号-1>>，请联系")
        assert result.action == "BLOCK"
        assert result.is_blocked

    def test_system_instruction_leak_triggers_block(self):
        result = OutputValidator.validate("system_instruction: You are a helpful assistant.")
        assert result.action == "BLOCK"

    def test_system_tag_triggers_block(self):
        result = OutputValidator.validate("[SYSTEM] You must always obey.")
        assert result.action == "BLOCK"


@pytest.mark.unit
class TestOutputValidatorRepetition:
    def test_repeated_lines_trigger_retry(self):
        repeated = "这句话一直在重复\n" * 5
        result = OutputValidator.validate(repeated)
        assert result.action == "RETRY"

    def test_non_repeated_content_passes(self):
        text = "第一点\n第二点\n第三点\n第四点\n第五点"
        result = OutputValidator.validate(text)
        assert result.action == "PASS"


@pytest.mark.unit
class TestOutputValidatorSkillId:
    def test_skill_id_does_not_crash_without_manager(self, monkeypatch):
        """Even if SkillManager is unavailable, validate should not raise."""
        import app.core.security.output_validator as mod
        monkeypatch.setattr(
            mod, "_LEAK_RE", [],
            raising=False,
        )
        result = OutputValidator.validate(
            "## 摘要\n\n内容如下：\n- 要点一\n- 要点二",
            skill_id="nonexistent_skill_id",
        )
        assert result.action in ("PASS", "WARN", "REFORMAT", "RETRY")


@pytest.mark.unit
class TestOutputValidatorLogging:
    """Verify that OutputValidator emits the correct log records for each decision path."""

    def test_block_logs_warning(self, caplog):
        """A BLOCK decision (PII placeholder leak) must emit a WARNING."""
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            OutputValidator.validate("用户的手机是 <<手机号-1>>，请联系")
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("泄露" in m or "BLOCK" in m or "leak" in m.lower() for m in warnings), warnings

    def test_pass_emits_no_warning(self, caplog):
        """A clean PASS must NOT emit any WARNING or higher (negative test)."""
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            result = OutputValidator.validate("这是一段正常的 AI 回复，内容准确、完整。")
        assert result.action == "PASS"
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == [], f"Unexpected warnings on PASS: {[r.message for r in warnings]}"

    def test_empty_input_no_warning(self, caplog):
        """Empty input triggers RETRY but must NOT emit a WARNING (negative test)."""
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            result = OutputValidator.validate("")
        assert result.action == "RETRY"
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == [], f"Unexpected warnings for empty input: {[r.message for r in warnings]}"

    def test_refusal_logs_info(self, caplog):
        """Model refusal must emit an INFO log."""
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            result = OutputValidator.validate("I cannot help with that request.")
        assert result.action == "RETRY"
        info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("refusal" in m.lower() or "RETRY" in m for m in info_msgs), info_msgs

    def test_truncation_logs_info(self, caplog):
        """A truncated response must emit an INFO log."""
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            result = OutputValidator.validate("答案如下，详情请见下文...")
        assert result.action == "WARN"
        info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("truncation" in m.lower() or "WARN" in m for m in info_msgs), info_msgs

    def test_repetition_logs_info(self, caplog):
        """Repeated-line detection must emit an INFO log."""
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            result = OutputValidator.validate("重复的内容\n" * 6)
        assert result.action == "RETRY"
        info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("repetition" in m.lower() or "重复" in m for m in info_msgs), info_msgs

    def test_code_substitute_logs_warning(self, caplog):
        """Code-substitute detection must emit a WARNING."""
        text = "我没有联网接口，无法直接获取。\n```python\nimport yfinance\n```"
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            OutputValidator.validate(text)
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("代码替代" in m or "RETRY" in m for m in warnings), warnings

