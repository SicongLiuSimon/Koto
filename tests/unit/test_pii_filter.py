# -*- coding: utf-8 -*-
"""Unit tests for app.core.security.pii_filter.PIIFilter."""
import logging
import pytest
from app.core.security.pii_filter import PIIConfig, PIIFilter, MaskResult

_LOGGER = "app.core.security.pii_filter"


@pytest.mark.unit
class TestPIIFilterBasics:
    def test_empty_string_returns_unchanged(self):
        result = PIIFilter.mask("")
        assert result.masked_text == ""
        assert not result.has_pii

    def test_whitespace_only_returns_unchanged(self):
        result = PIIFilter.mask("   ")
        assert result.masked_text == "   "
        assert not result.has_pii

    def test_clean_text_no_masking(self):
        result = PIIFilter.mask("今天天气不错，我们去公园吧")
        assert result.masked_text == "今天天气不错，我们去公园吧"
        assert not result.has_pii

    def test_mask_result_has_original_text(self):
        original = "请联系13812345678"
        result = PIIFilter.mask(original)
        assert result.original_text == original


@pytest.mark.unit
class TestPIIPhoneNumber:
    def test_masks_mobile_phone(self):
        result = PIIFilter.mask("手机号是13812345678，请联系")
        assert "13812345678" not in result.masked_text
        assert result.has_pii
        assert "手机号" in result.stats

    def test_multiple_phones_get_unique_placeholders(self):
        result = PIIFilter.mask("A: 13812345678 B: 13987654321")
        assert "13812345678" not in result.masked_text
        assert "13987654321" not in result.masked_text
        assert len([k for k in result.mask_map if "手机号" in k]) == 2

    def test_restore_phone_number(self):
        original = "电话：13812345678"
        result = PIIFilter.mask(original)
        restored = PIIFilter.restore(result.masked_text, result.mask_map)
        assert restored == original


@pytest.mark.unit
class TestPIIEmail:
    def test_masks_email(self):
        result = PIIFilter.mask("发邮件到 user@example.com 即可")
        assert "user@example.com" not in result.masked_text
        assert result.has_pii
        assert "邮箱" in result.stats

    def test_masks_complex_email(self):
        result = PIIFilter.mask("联系 first.last+tag@sub.domain.org")
        assert "first.last+tag@sub.domain.org" not in result.masked_text


@pytest.mark.unit
class TestPIIIdCard:
    def test_masks_id_card_number(self):
        result = PIIFilter.mask("身份证：110101199001011234")
        assert "110101199001011234" not in result.masked_text
        assert result.has_pii

    def test_id_card_with_x_suffix(self):
        result = PIIFilter.mask("证件号码：11010119900101123X")
        assert "11010119900101123X" not in result.masked_text


@pytest.mark.unit
class TestPIIBankCard:
    def test_masks_bank_card(self):
        result = PIIFilter.mask("银行卡：6222021001122334455")
        assert "6222021001122334455" not in result.masked_text


@pytest.mark.unit
class TestPIICustomKeywords:
    def test_custom_keyword_masked(self):
        config = PIIConfig(custom_keywords=["SecretProject"])
        result = PIIFilter.mask("我在参与 SecretProject 的开发", config=config)
        assert "SecretProject" not in result.masked_text
        assert result.has_pii

    def test_multiple_custom_keywords(self):
        config = PIIConfig(custom_keywords=["ACME", "TopSecret"])
        result = PIIFilter.mask("ACME 公司的 TopSecret 计划", config=config)
        assert "ACME" not in result.masked_text
        assert "TopSecret" not in result.masked_text

    def test_custom_keyword_restore(self):
        config = PIIConfig(custom_keywords=["InternalCode"])
        text = "项目代码是 InternalCode"
        result = PIIFilter.mask(text, config=config)
        restored = result.restore(result.masked_text)
        assert restored == text


@pytest.mark.unit
class TestPIISelectiveConfig:
    def test_disable_phone_masking(self):
        config = PIIConfig(mask_phone=False)
        result = PIIFilter.mask("手机：13812345678 邮箱：a@b.com", config=config)
        assert "13812345678" in result.masked_text
        assert "a@b.com" not in result.masked_text

    def test_disable_email_masking(self):
        config = PIIConfig(mask_email=False)
        result = PIIFilter.mask("邮箱：a@b.com 手机：13812345678", config=config)
        assert "a@b.com" in result.masked_text
        assert "13812345678" not in result.masked_text

    def test_mask_only_phone_and_email(self):
        config = PIIConfig(
            mask_phone=True, mask_email=True,
            mask_id_card=False, mask_bank_card=False,
            mask_name=False, mask_address=False,
            mask_passport=False, mask_landline=False,
        )
        result = PIIFilter.mask("手机：13812345678 邮箱：x@y.com 证件：110101199001011234", config=config)
        assert "13812345678" not in result.masked_text
        assert "x@y.com" not in result.masked_text
        assert "110101199001011234" in result.masked_text


@pytest.mark.unit
class TestPIIHasPII:
    def test_has_pii_returns_true_when_pii_present(self):
        assert PIIFilter.has_pii("联系我 13812345678") is True

    def test_has_pii_returns_false_when_clean(self):
        # Avoid Chinese text that triggers the heuristic name pattern (是/给/叫 + 2-4 chars)
        assert PIIFilter.has_pii("The weather today is nice.") is False


@pytest.mark.unit
class TestPIIRestore:
    def test_restore_is_inverse_of_mask(self):
        texts = [
            "手机：13812345678",
            "邮箱：test@domain.com",
            "银行卡号 6222021001122334455 请保密",
        ]
        for text in texts:
            result = PIIFilter.mask(text)
            restored = PIIFilter.restore(result.masked_text, result.mask_map)
            assert restored == text

    def test_restore_with_empty_mask_map(self):
        text = "普通文本，无PII"
        assert PIIFilter.restore(text, {}) == text

    def test_mask_result_restore_method(self):
        text = "联系方式：a@b.com"
        result = PIIFilter.mask(text)
        assert result.restore(result.masked_text) == text


@pytest.mark.unit
class TestPIIAddCustomKeyword:
    def test_add_custom_keyword_is_immutable(self):
        config = PIIConfig()
        new_config = PIIFilter.add_custom_keyword(config, "SensitiveWord")
        assert "SensitiveWord" in new_config.custom_keywords
        assert "SensitiveWord" not in config.custom_keywords


@pytest.mark.unit
class TestPIIFilterLogging:
    """Verify that PIIFilter emits the right log records."""

    def test_pii_detected_logs_info_with_stats(self, caplog):
        """When PII is found, an INFO log with category stats must be emitted."""
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            PIIFilter.mask("手机号是13812345678，请联系")
        info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("PIIFilter" in m and "手机号" in m for m in info_msgs), info_msgs

    def test_no_pii_emits_no_info_log(self, caplog):
        """When no PII is present, the stats INFO log must NOT be emitted (negative)."""
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            PIIFilter.mask("The weather today is nice.")
        info_msgs = [r.message for r in caplog.records
                     if r.levelno == logging.INFO and "脱敏统计" in r.message]
        assert info_msgs == [], f"Unexpected INFO log: {info_msgs}"

    def test_log_stats_false_suppresses_info(self, caplog):
        """Setting log_stats=False must suppress the stats INFO log (negative)."""
        cfg = PIIConfig(log_stats=False)
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            PIIFilter.mask("手机号是13812345678", config=cfg)
        suppressed = [r.message for r in caplog.records
                      if r.levelno == logging.INFO and "脱敏统计" in r.message]
        assert suppressed == [], f"log_stats=False should suppress info: {suppressed}"

    def test_empty_text_logs_debug_skip(self, caplog):
        """An empty input must produce a DEBUG 'skipped' log, not an INFO/WARNING."""
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            PIIFilter.mask("")
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("skip" in m.lower() or "empty" in m.lower() for m in debug_msgs), debug_msgs
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == [], f"Unexpected warnings for empty input: {warnings}"

    def test_restore_logs_debug(self, caplog):
        """restore() must emit a DEBUG log showing placeholder count."""
        result = PIIFilter.mask("手机是13812345678，邮箱是a@b.com")
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            PIIFilter.restore(result.masked_text, result.mask_map)
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("restore" in m.lower() or "placeholder" in m.lower() for m in debug_msgs), debug_msgs

    def test_no_pii_emits_debug_not_info(self, caplog):
        """Clean text must produce a DEBUG (not INFO) 'no PII detected' log."""
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            PIIFilter.mask("Hello, this is a clean message.")
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("no PII" in m or "no pii" in m.lower() for m in debug_msgs), debug_msgs

