"""Unit tests for SkillAutoBuilder additions.

Covers:
- StyleProfile round-trip: to_dict() → from_dict() produces an equivalent object
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# StyleProfile round-trip
# ---------------------------------------------------------------------------


class TestStyleProfileRoundTrip:
    def test_default_round_trip(self):
        from app.core.skills.skill_auto_builder import StyleProfile

        original = StyleProfile()
        d = original.to_dict()
        restored = StyleProfile.from_dict(d)

        assert restored.formality == pytest.approx(original.formality)
        assert restored.verbosity == pytest.approx(original.verbosity)
        assert restored.empathy == pytest.approx(original.empathy)
        assert restored.domain == original.domain
        assert restored.language == original.language

    def test_custom_values_round_trip(self):
        from app.core.skills.skill_auto_builder import StyleProfile

        original = StyleProfile(
            formality=0.9,
            verbosity=0.1,
            empathy=0.8,
            structure=0.7,
            creativity=0.6,
            technicality=0.95,
            positivity=0.3,
            proactivity=0.2,
            humor=0.05,
            conciseness=0.85,
            domain="coding",
            language="en",
        )
        d = original.to_dict()
        restored = StyleProfile.from_dict(d)

        for field in (
            "formality",
            "verbosity",
            "empathy",
            "structure",
            "creativity",
            "technicality",
            "positivity",
            "proactivity",
            "humor",
            "conciseness",
        ):
            assert getattr(restored, field) == pytest.approx(
                getattr(original, field)
            ), f"field '{field}' mismatch after round-trip"

        assert restored.domain == "coding"
        assert restored.language == "en"

    def test_to_dict_has_all_keys(self):
        from app.core.skills.skill_auto_builder import StyleProfile

        d = StyleProfile().to_dict()
        expected_keys = {
            "formality",
            "verbosity",
            "empathy",
            "structure",
            "creativity",
            "technicality",
            "positivity",
            "proactivity",
            "humor",
            "conciseness",
            "domain",
            "language",
        }
        assert expected_keys.issubset(d.keys())

    def test_from_dict_ignores_unknown_keys(self):
        from app.core.skills.skill_auto_builder import StyleProfile

        d = StyleProfile().to_dict()
        d["unexpected_key"] = "ignored"
        # Should not raise
        restored = StyleProfile.from_dict(d)
        assert isinstance(restored, StyleProfile)
