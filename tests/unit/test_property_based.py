# -*- coding: utf-8 -*-
"""Property-based tests using Hypothesis for Koto core modules."""

import json
import uuid

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.core.config.configuration_manager import ConfigurationManager
from app.core.routing.ai_router import AIRouter
from app.core.security.output_validator import OutputValidator, ValidationResult
from app.core.security.pii_filter import MaskResult, PIIConfig, PIIFilter

# ════════════════════════════════════════════════════════════════════
# Strategies
# ════════════════════════════════════════════════════════════════════

email_st = st.from_regex(r"[a-z]{3,8}@[a-z]{3,6}\.(com|org|net)", fullmatch=True)
phone_cn_st = st.from_regex(r"1[3-9][0-9]{9}", fullmatch=True)
safe_text_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=0,
    max_size=200,
)


# ════════════════════════════════════════════════════════════════════
# 1. PII Filtering
# ════════════════════════════════════════════════════════════════════


class TestPIIFiltering:
    """Property-based tests for PIIFilter."""

    @given(text=st.text(min_size=0, max_size=500))
    @settings(max_examples=50)
    def test_mask_never_crashes(self, text):
        """PIIFilter.mask handles arbitrary input without crashing."""
        result = PIIFilter.mask(text)
        assert isinstance(result, MaskResult)
        assert isinstance(result.masked_text, str)

    @given(text=st.text(min_size=0, max_size=500))
    @settings(max_examples=50)
    def test_mask_is_idempotent(self, text):
        """Masking already-masked text produces the same output."""
        first = PIIFilter.mask(text)
        second = PIIFilter.mask(first.masked_text)
        assert first.masked_text == second.masked_text

    @given(email=email_st, prefix=st.text(min_size=0, max_size=30))
    @settings(max_examples=30)
    def test_email_always_masked(self, email, prefix):
        """Any string containing an email gets it masked."""
        text = f"{prefix} {email}"
        result = PIIFilter.mask(text, config=PIIConfig(mask_email=True))
        assert email not in result.masked_text

    @given(phone=phone_cn_st, prefix=st.text(min_size=0, max_size=30))
    @settings(max_examples=30)
    def test_phone_always_masked(self, phone, prefix):
        """Any string containing a CN mobile phone gets it masked."""
        # Ensure the phone isn't adjacent to other digits
        clean_prefix = prefix.rstrip("0123456789")
        text = f"{clean_prefix} {phone}"
        result = PIIFilter.mask(text, config=PIIConfig(mask_phone=True))
        assert phone not in result.masked_text

    @given(text=st.text(min_size=0, max_size=300))
    @settings(max_examples=50)
    def test_restore_roundtrip(self, text):
        """mask → restore recovers the original text."""
        result = PIIFilter.mask(text)
        restored = PIIFilter.restore(result.masked_text, result.mask_map)
        assert restored == text

    @given(text=st.text(min_size=0, max_size=200))
    @settings(max_examples=30)
    def test_has_pii_consistent_with_mask(self, text):
        """has_pii returns True iff mask_map is non-empty."""
        result = PIIFilter.mask(text)
        assert PIIFilter.has_pii(text) == result.has_pii


# ════════════════════════════════════════════════════════════════════
# 2. Session/ID Generation (uuid4)
# ════════════════════════════════════════════════════════════════════


class TestIDGeneration:
    """Property-based tests for UUID generation (used by FileRegistry)."""

    @given(n=st.integers(min_value=2, max_value=200))
    @settings(max_examples=20)
    def test_uuid4_always_unique(self, n):
        """N generated UUIDs are all distinct."""
        ids = [str(uuid.uuid4()) for _ in range(n)]
        assert len(set(ids)) == n

    @given(data=st.data())
    @settings(max_examples=50)
    def test_uuid4_always_valid_format(self, data):
        """Generated UUID strings are valid v4 UUIDs."""
        uid = str(uuid.uuid4())
        assert len(uid) == 36
        parsed = uuid.UUID(uid)
        assert parsed.version == 4
        assert str(parsed) == uid


# ════════════════════════════════════════════════════════════════════
# 3. Configuration Manager
# ════════════════════════════════════════════════════════════════════


class TestConfigurationManager:
    """Property-based tests for ConfigurationManager."""

    @given(
        value=st.floats(
            min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False
        )
    )
    @settings(max_examples=50)
    def test_set_get_threshold_roundtrip(self, value):
        """set_threshold → get_threshold returns the value."""
        cm = ConfigurationManager()
        cm.set_threshold("cpu", "warning", value)
        assert cm.get_threshold("cpu", "warning") == value

    @given(metric=st.sampled_from(list(ConfigurationManager.DEFAULT_THRESHOLDS.keys())))
    @settings(max_examples=20)
    def test_reset_restores_defaults(self, metric):
        """After set + reset, threshold equals the default."""
        cm = ConfigurationManager()
        cm.set_threshold(metric, "warning", 999.0)
        cm.reset_threshold(metric)
        expected = ConfigurationManager.DEFAULT_THRESHOLDS[metric]["warning"]
        assert cm.get_threshold(metric, "warning") == expected

    @given(metric=st.text(min_size=1, max_size=20))
    @settings(max_examples=30)
    def test_unknown_metric_rejected(self, metric):
        """set_threshold returns False for unknown metrics."""
        assume(metric not in ConfigurationManager.DEFAULT_THRESHOLDS)
        cm = ConfigurationManager()
        assert cm.set_threshold(metric, "warning", 50) is False

    @given(level=st.text(min_size=1, max_size=20))
    @settings(max_examples=30)
    def test_invalid_level_rejected(self, level):
        """set_threshold returns False for invalid levels."""
        assume(level not in ("warning", "critical"))
        cm = ConfigurationManager()
        assert cm.set_threshold("cpu", level, 50) is False

    @given(
        metric=st.sampled_from(list(ConfigurationManager.DEFAULT_THRESHOLDS.keys())),
        value=st.floats(
            min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=30)
    def test_validate_returns_valid_status(self, metric, value):
        """validate_metric_value always returns a dict with expected keys."""
        cm = ConfigurationManager()
        result = cm.validate_metric_value(metric, value)
        assert "valid" in result
        assert "status" in result
        assert result["status"] in ("normal", "warning", "critical")

    @given(data=st.data())
    @settings(max_examples=20)
    def test_export_import_roundtrip(self, data):
        """export → import preserves thresholds."""
        cm = ConfigurationManager()
        metric = data.draw(st.sampled_from(list(cm.DEFAULT_THRESHOLDS.keys())))
        val = data.draw(
            st.floats(min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False)
        )
        cm.set_threshold(metric, "warning", val)
        exported = cm.export_config()

        cm2 = ConfigurationManager()
        assert cm2.import_config(exported) is True
        assert cm2.get_threshold(metric, "warning") == val

    @given(bad_json=st.text(min_size=1, max_size=100))
    @settings(max_examples=20)
    def test_import_invalid_json_returns_false(self, bad_json):
        """import_config gracefully rejects invalid JSON."""
        assume(not _is_valid_json(bad_json))
        cm = ConfigurationManager()
        assert cm.import_config(bad_json) is False


# ════════════════════════════════════════════════════════════════════
# 4. Cache Operations (AIRouter half-eviction cache)
# ════════════════════════════════════════════════════════════════════


class TestCacheOperations:
    """Property-based tests for AIRouter's bounded cache."""

    @given(n=st.integers(min_value=1, max_value=800))
    @settings(max_examples=15)
    def test_cache_never_exceeds_max_size(self, n):
        """After N inserts the cache size never exceeds _CACHE_MAX_SIZE."""
        AIRouter._cache = {}
        for i in range(n):
            AIRouter._cache_set(f"key_{i}", f"val_{i}")
        assert len(AIRouter._cache) <= AIRouter._CACHE_MAX_SIZE
        AIRouter._cache = {}

    @given(
        key=st.text(min_size=1, max_size=50), value=st.text(min_size=0, max_size=100)
    )
    @settings(max_examples=30)
    def test_set_then_get(self, key, value):
        """Value is retrievable immediately after _cache_set."""
        AIRouter._cache = {}
        AIRouter._cache_set(key, value)
        assert AIRouter._cache[key] == value
        AIRouter._cache = {}

    @given(
        keys=st.lists(
            st.text(min_size=1, max_size=30), min_size=1, max_size=600, unique=True
        )
    )
    @settings(max_examples=10)
    def test_eviction_preserves_recent_entries(self, keys):
        """After bulk inserts, the most recently inserted key is still present."""
        AIRouter._cache = {}
        for k in keys:
            AIRouter._cache_set(k, "v")
        assert keys[-1] in AIRouter._cache
        AIRouter._cache = {}


# ════════════════════════════════════════════════════════════════════
# 5. Output Validator (input sanitization / security)
# ════════════════════════════════════════════════════════════════════


class TestOutputValidator:
    """Property-based tests for OutputValidator security checks."""

    @given(text=st.text(min_size=0, max_size=500))
    @settings(max_examples=50)
    def test_validate_never_crashes(self, text):
        """OutputValidator.validate handles arbitrary text without crashing."""
        result = OutputValidator.validate(text)
        assert isinstance(result, ValidationResult)
        assert result.action in ("PASS", "WARN", "REFORMAT", "RETRY", "BLOCK")

    @given(text=safe_text_st)
    @settings(max_examples=30)
    def test_safe_text_not_blocked(self, text):
        """Ordinary text (no PII placeholders or system markers) is never BLOCK."""
        assume("<<" not in text and ">>" not in text)
        assume("[SYSTEM]" not in text.upper())
        assume("<|" not in text and "|>" not in text)
        result = OutputValidator.validate(text)
        assert result.action != "BLOCK"

    @given(
        placeholder=st.from_regex(r"<<[a-z]{2,6}-[0-9]{1,3}>>", fullmatch=True),
        padding=st.text(min_size=5, max_size=100),
    )
    @settings(max_examples=20)
    def test_pii_placeholder_detected(self, placeholder, padding):
        """Text with <<label-N>> PII placeholders triggers BLOCK or WARN."""
        text = f"{padding} {placeholder} {padding}"
        result = OutputValidator.validate(text)
        assert result.action in ("BLOCK", "WARN", "RETRY")

    @given(text=st.text(min_size=0, max_size=300))
    @settings(max_examples=30)
    def test_action_always_valid_enum(self, text):
        """The action field is always one of the five allowed values."""
        result = OutputValidator.validate(text)
        assert result.action in {"PASS", "WARN", "REFORMAT", "RETRY", "BLOCK"}

    @given(text=st.text(min_size=0, max_size=300))
    @settings(max_examples=30)
    def test_original_text_preserved(self, text):
        """ValidationResult always stores the original text."""
        result = OutputValidator.validate(text)
        assert result.original_text == text


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════


def _is_valid_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except (json.JSONDecodeError, ValueError):
        return False
