"""Unit tests for precompiled regex patterns across the codebase.

Verifies that module-level and instance-level compiled patterns are
``re.Pattern`` objects and match (or reject) the expected inputs.
"""

from __future__ import annotations

import re

import pytest

# ---------------------------------------------------------------------------
# 1. ConsistencyChecker._compiled_patterns
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConsistencyCheckerPatterns:
    """Verify precompiled variant patterns in ConsistencyChecker.__init__."""

    @pytest.fixture()
    def checker(self):
        from web.consistency_checker import ConsistencyChecker

        return ConsistencyChecker()

    def test_compiled_patterns_are_re_pattern(self, checker):
        for _term, variants in checker._compiled_patterns.items():
            for variant_str, pattern in variants:
                assert isinstance(
                    pattern, re.Pattern
                ), f"Expected re.Pattern for variant '{variant_str}', got {type(pattern)}"

    @pytest.mark.parametrize(
        "term, variant_text",
        [
            ("人工智能", "AI"),
            ("人工智能", "ai"),
            ("人工智能", "A.I."),
            ("人工智能", "artificial intelligence"),
            ("机器学习", "ML"),
            ("机器学习", "machine learning"),
            ("深度学习", "DL"),
            ("深度学习", "deep learning"),
            ("自然语言处理", "NLP"),
            ("自然语言处理", "natural language processing"),
        ],
    )
    def test_patterns_match_expected_variants(self, checker, term, variant_text):
        matched = False
        for _variant_str, pattern in checker._compiled_patterns[term]:
            if pattern.search(variant_text):
                matched = True
                break
        assert matched, f"No pattern for '{term}' matched '{variant_text}'"

    def test_patterns_work_with_cjk_text(self, checker):
        text = "我们使用AI进行自然语言处理NLP研究"
        for term, variants in checker._compiled_patterns.items():
            for _variant_str, pattern in variants:
                # Should not raise; just exercise CJK mixed text
                pattern.search(text)

    def test_patterns_work_with_ascii_text(self, checker):
        text = "We use machine learning and deep learning for NLP tasks"
        matches = {}
        for term, variants in checker._compiled_patterns.items():
            for variant_str, pattern in variants:
                if pattern.search(text):
                    matches.setdefault(term, []).append(variant_str)

        assert "机器学习" in matches
        assert "深度学习" in matches
        assert "自然语言处理" in matches

    def test_patterns_are_case_insensitive(self, checker):
        for _term, variants in checker._compiled_patterns.items():
            for variant_str, pattern in variants:
                assert (
                    pattern.flags & re.IGNORECASE
                ), f"Pattern for '{variant_str}' is not case-insensitive"


# ---------------------------------------------------------------------------
# 2. file_converter.CN_FORMAT_PATTERNS
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCNFormatPatterns:
    """Verify CN_FORMAT_PATTERNS in web.file_converter."""

    @pytest.fixture()
    def patterns(self):
        from web.file_converter import CN_FORMAT_PATTERNS

        return CN_FORMAT_PATTERNS

    def test_all_values_are_re_pattern(self, patterns):
        for pat, ext in patterns:
            assert isinstance(
                pat, re.Pattern
            ), f"Expected re.Pattern for ext '{ext}', got {type(pat)}"

    @pytest.mark.parametrize(
        "text, expected_ext",
        [
            ("word文档", ".docx"),
            ("docx文件", ".docx"),
            ("pdf文件", ".pdf"),
            ("PDF", ".pdf"),
            ("纯文本", ".txt"),
            ("txt文件", ".txt"),
            ("markdown", ".md"),
            ("md文件", ".md"),
            ("pptx", ".pptx"),
            ("幻灯片", ".pptx"),
            ("演示文稿", ".pptx"),
            ("excel", ".xlsx"),
            ("电子表格", ".xlsx"),
            ("csv", ".csv"),
            ("逗号", ".csv"),
            ("png图片", ".png"),
            ("jpeg图片", ".jpg"),
            ("webp", ".webp"),
        ],
    )
    def test_pattern_matches_expected_chinese_format(
        self, patterns, text, expected_ext
    ):
        matched_ext = None
        for pat, ext in patterns:
            if pat.search(text):
                matched_ext = ext
                break
        assert (
            matched_ext == expected_ext
        ), f"Expected '{expected_ext}' for '{text}', got '{matched_ext}'"

    @pytest.mark.parametrize(
        "text",
        [
            "随便聊聊",
            "hello world",
            "12345",
            "这是一段普通中文",
        ],
    )
    def test_patterns_reject_non_matching_strings(self, patterns, text):
        for pat, ext in patterns:
            assert not pat.search(
                text
            ), f"Pattern for '{ext}' should NOT match '{text}'"


# ---------------------------------------------------------------------------
# 3. LocalModelRouter installed_map dict lookup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLocalModelRouterInstalledMap:
    """Verify the dict-based lookup logic used by LocalModelRouter."""

    OLLAMA_MODELS = [
        "koto-router",
        "qwen3:8b",
        "qwen3:4b",
        "qwen3:1.7b",
        "qwen2.5:7b",
        "qwen2.5:3b",
        "qwen2.5:1.5b",
        "llama3.2:3b",
    ]

    @staticmethod
    def _build_installed_map(installed: list[str]) -> dict[str, str]:
        """Replicate the installed_map construction from LocalModelRouter."""
        installed_map: dict[str, str] = {}
        for im in installed:
            base = im.split(":")[0]
            installed_map[base] = im
        return installed_map

    @staticmethod
    def _select_model(
        installed_map: dict[str, str], ollama_models: list[str]
    ) -> str | None:
        """Replicate the priority-based selection from LocalModelRouter."""
        for m in ollama_models:
            base_name = m.split(":")[0]
            if base_name in installed_map:
                return installed_map[base_name]
        return None

    def test_selects_highest_priority_model(self):
        installed = ["llama3.2:3b", "qwen3:8b"]
        imap = self._build_installed_map(installed)
        result = self._select_model(imap, self.OLLAMA_MODELS)
        assert result == "qwen3:8b"

    def test_selects_koto_router_when_available(self):
        installed = ["koto-router", "qwen3:4b"]
        imap = self._build_installed_map(installed)
        result = self._select_model(imap, self.OLLAMA_MODELS)
        assert result == "koto-router"

    def test_falls_back_to_lower_priority(self):
        installed = ["llama3.2:3b"]
        imap = self._build_installed_map(installed)
        result = self._select_model(imap, self.OLLAMA_MODELS)
        assert result == "llama3.2:3b"

    def test_returns_none_for_unknown_models(self):
        installed = ["mistral:7b", "phi3:mini"]
        imap = self._build_installed_map(installed)
        result = self._select_model(imap, self.OLLAMA_MODELS)
        assert result is None

    def test_returns_none_for_empty_installed(self):
        imap = self._build_installed_map([])
        result = self._select_model(imap, self.OLLAMA_MODELS)
        assert result is None

    def test_map_preserves_full_tag(self):
        installed = ["qwen2.5:7b-q4_0"]
        imap = self._build_installed_map(installed)
        assert imap["qwen2.5"] == "qwen2.5:7b-q4_0"
        result = self._select_model(imap, self.OLLAMA_MODELS)
        assert result == "qwen2.5:7b-q4_0"
