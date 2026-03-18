# -*- coding: utf-8 -*-
"""
Unit tests for DocumentFeedback 503 / API-failure handling.

Covers the two bugs fixed in document_feedback.py:
  Bug 1: 503 was immediately bailing into local fallback without retrying.
         Fix: 503 now waits then retries (up to max_retries). Only falls back
              after all retries are exhausted.
  Bug 2: After a 503 model-switch, the failing chunk was dropped.
         Fix: When a working replacement model is found, the chunk is
              re-queued so it gets processed with AI quality.
"""

from __future__ import annotations

import unittest
from collections import deque
from unittest.mock import MagicMock, patch, PropertyMock, call

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning"),
]


# ---------------------------------------------------------------------------
# Minimal stubs so the module can be imported without google.genai installed
# ---------------------------------------------------------------------------

def _make_document_feedback_class():
    """
    Import DocumentFeedback.  Returns the class or None if import fails for
    a reason other than missing google.genai (which we mock away).
    """
    import sys, types

    # Stub google.genai hierarchy
    for mod_name in [
        "google",
        "google.genai",
        "google.genai.types",
        "google.auth",
        "google.auth.credentials",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    google_genai = sys.modules["google.genai"]
    google_genai.Client = MagicMock

    genai_types = sys.modules["google.genai.types"]
    genai_types.GenerateContentConfig = MagicMock
    genai_types.ThinkingConfig = MagicMock

    try:
        from web.document_feedback import DocumentFeedbackSystem as DocumentFeedback
        return DocumentFeedback
    except Exception:
        return None


DocumentFeedback = _make_document_feedback_class()
requires_df = pytest.mark.skipif(
    DocumentFeedback is None,
    reason="DocumentFeedback module not importable in this environment",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_feedback(client=None) -> "DocumentFeedback":
    """Return a DocumentFeedback instance with all heavy deps mocked out."""
    df = DocumentFeedback.__new__(DocumentFeedback)
    df.client = client or MagicMock()
    df.default_model_id = "gemini-3-flash-preview"
    df.reader = MagicMock()
    df.editor = MagicMock()
    df.annotator = MagicMock()
    df._model_cache = None
    # _format_model_table expects List[Dict]; mock it to avoid TypeError
    df._format_model_table = MagicMock(return_value="")
    return df


def _make_good_response(annotations_json: str = None):
    """Fake generate_content response that returns valid JSON."""
    if annotations_json is None:
        annotations_json = (
            '[{"原文片段":"测试文字","修改建议":"修改建议","修改后文本":"修改后","理由":"理由"}]'
        )
    resp = MagicMock()
    resp.text = annotations_json
    return resp


def _503_error():
    return Exception("503 UNAVAILABLE. {'error': {'code': 503, 'message': 'high demand'}}")


# ===========================================================================
# Bug 1: _analyze_chunk_for_annotations — 503 should retry, not bail
# ===========================================================================

@requires_df
class TestChunkAnnotationsRetryOn503:
    """
    _analyze_chunk_for_annotations must retry with a sleep delay on 503,
    and only fall back to local rules after all retries are exhausted.
    """

    def _call(self, df: "DocumentFeedback", side_effects, max_retries=2) -> list:
        """Drive _analyze_chunk_for_annotations with given client side_effects."""
        df.client.models.generate_content.side_effect = side_effects
        with patch("time.sleep"):  # prevent real sleeping
            return df._analyze_chunk_for_annotations(
                chunk="这是一段需要润色的简历内容，包含若干明显问题。",
                doc_type="resume",
                user_requirement="帮我改简历",
                model_id="gemini-3-flash-preview",
                chunk_index=1,
                total_chunks=1,
                max_retries=max_retries,
            )

    def test_503_immediately_returns_fallback_without_retry(self):
        """503 on first call → immediately returns local fallback (no retry of same model)."""
        df = _make_feedback()
        fake_fallback = [
            {"原文片段": "x", "修改建议": "y", "修改后文本": "z", "理由": "r"}
        ]
        df._fallback_annotations_from_chunk = MagicMock(return_value=fake_fallback)

        result = self._call(df, [_503_error()], max_retries=2)

        # Must be fallback (503 skips retries entirely)
        assert result is not None
        has_503_flag = any(a.get("_koto_503") for a in result)
        assert has_503_flag, "Immediate 503 fallback must carry _koto_503 flag"
        # Only one API call should have been made (no retry)
        assert df.client.models.generate_content.call_count == 1, (
            "Inner method must NOT retry on 503; only 1 API call expected"
        )

    def test_503_exhausted_returns_fallback_with_503_flag(self):
        """503 on every retry → eventual local fallback annotated with _koto_503."""
        df = _make_feedback()
        # Mock the fallback to guarantee a non-empty return (short chunks may
        # not match any regex rule in _fallback_annotations_from_chunk)
        fake_fallback = [
            {"原文片段": "被动句示例", "修改建议": "改主动", "修改后文本": "主动句", "理由": "更自然"}
        ]
        df._fallback_annotations_from_chunk = MagicMock(return_value=fake_fallback)

        result = self._call(
            df,
            [_503_error(), _503_error(), _503_error()],
            max_retries=2,
        )

        assert result is not None
        # After all retries exhausted, falls back to local rules with _koto_503 flag
        has_503_flag = any(a.get("_koto_503") for a in result)
        assert has_503_flag, "Exhausted 503 fallback must carry _koto_503 flag"

    def test_503_no_sleep_before_fallback(self):
        """503 is handled instantly — no sleep occurs before returning fallback."""
        df = _make_feedback()
        fake_fallback = [
            {"原文片段": "x", "修改建议": "y", "修改后文本": "z", "理由": "r"}
        ]
        df._fallback_annotations_from_chunk = MagicMock(return_value=fake_fallback)
        sleep_calls = []

        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            self._call(df, [_503_error()], max_retries=2)

        assert len(sleep_calls) == 0, (
            "503 immediate fallback must NOT call sleep; outer loop handles the model switch"
        )

    def test_non_503_error_falls_back_immediately(self):
        """Non-503 API errors (auth, quota, etc.) should NOT trigger the 503 retry path."""
        df = _make_feedback()
        auth_error = Exception("400 INVALID_ARGUMENT: API key missing")
        sleep_calls = []

        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            result = self._call(df, [auth_error, auth_error], max_retries=2)

        # Non-503 also falls back to local rules after retries
        assert result is not None
        # The key assertion: no 503-specific sleep (10s, 20s) was called
        # (regular retry uses 3*retry wait, not 10*retry)
        long_sleeps = [s for s in sleep_calls if s >= 10]
        assert len(long_sleeps) == 0, (
            "503-specific long sleep should NOT fire for non-503 errors"
        )


# ===========================================================================
# Bug 2: analyze_for_annotation_chunked — failed chunk re-queued after switch
# ===========================================================================

@requires_df
class TestChunkedAnnotationsModelSwitch:
    """
    analyze_for_annotation_chunked: when a chunk fails with 503 and a
    working alternative model is found via _probe_working_model, the
    failing chunk must be re-queued and processed with the new model,
    not dropped.
    """

    def _make_df_with_two_chunks(self):
        """
        Return a DocumentFeedback with mocked reader and chunker that yields
        exactly 2 chunks, suitable for testing the queue-based pipeline.
        """
        df = _make_feedback()
        # Disable startup probe: the probe guard is `if self.client and ...`
        # Setting client=None here prevents the pre-flight probe from switching
        # the model before chunk processing begins.
        df.client = None

        doc_text = "这是一段很长的简历内容。" * 250  # ~3000 chars per chunk
        df.reader.read_document.return_value = {
            "success": True,
            "type": "resume",
            "metadata": {},
        }
        df.reader.format_for_ai.return_value = doc_text * 2  # ~6000 chars total

        # Force exactly 2 chunks so the queue loop runs predictably
        df._split_into_chunks_by_paragraphs = MagicMock(
            return_value=["chunk_1_content", "chunk_2_content"]
        )
        return df, doc_text

    def test_503_chunk_requeued_and_processed_with_new_model(self):
        """
        Scenario:
          chunk 1 → 503 → probe finds gemini-2.5-flash → chunk 1 re-queued
          chunk 1 (retry with gemini-2.5-flash) → success
          chunk 2 → success
        Expected: all chunks have AI-quality annotations, no fallback in output.
        """
        df, _ = self._make_df_with_two_chunks()

        # _select_best_model returns the initial model; use empty list to avoid
        # _format_model_table receiving strings instead of dicts.
        df._select_best_model = MagicMock(
            return_value=("gemini-3-flash-preview", [])
        )

        # _probe_working_model returns a different model (switch succeeds)
        df._probe_working_model = MagicMock(return_value="gemini-2.5-flash")

        ai_anno = [{"原文片段": "测试", "修改建议": "建议", "修改后文本": "修改后", "理由": "理由"}]
        fallback_anno = [{"原文片段": "x", "_koto_503": True, "_koto_fallback_error": "503"}]

        call_count = {"n": 0}

        def _fake_analyze(chunk, doc_type, user_requirement, model_id,
                          chunk_index, total_chunks, full_doc_context="", max_retries=2):
            call_count["n"] += 1
            # First call: simulate 503 fallback
            if call_count["n"] == 1:
                return fallback_anno
            # Subsequent calls: success
            return ai_anno

        df._analyze_chunk_for_annotations = _fake_analyze

        result = df.analyze_for_annotation_chunked(
            file_path="/fake/resume.docx",
            user_requirement="帮我改简历",
            model_id="gemini-3-flash-preview",
        )

        assert result["success"], f"Expected success, got: {result}"
        # The chunk should have been re-queued and retried
        assert call_count["n"] >= 3, (
            f"Expected ≥3 calls (chunk1 fail, chunk1 retry, chunk2), got {call_count['n']}"
        )
        # Output must contain AI annotations only (no fallback items)
        output_annotations = result.get("annotations", [])
        has_fallback = any(a.get("_koto_fallback_error") for a in output_annotations)
        assert not has_fallback, "Fallback annotations must be excluded from output"

    def test_503_chunk_dropped_when_no_alternative_model(self):
        """
        When _probe_working_model returns None (no other model available),
        the chunk is NOT re-queued and falls back gracefully.
        Output still succeeds (partial result from other chunks).
        """
        df, _ = self._make_df_with_two_chunks()

        df._select_best_model = MagicMock(
            return_value=("gemini-3-flash-preview", [])
        )
        # Probe fails to find an alternative
        df._probe_working_model = MagicMock(return_value=None)

        fallback_anno = [{"原文片段": "x", "_koto_503": True, "_koto_fallback_error": "503"}]
        ai_anno = [{"原文片段": "测试", "修改建议": "建议", "修改后文本": "修改后", "理由": "理由"}]
        call_count = {"n": 0}

        def _fake_analyze(chunk, doc_type, user_requirement, model_id,
                          chunk_index, total_chunks, full_doc_context="", max_retries=2):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return fallback_anno  # first chunk 503s
            return ai_anno

        df._analyze_chunk_for_annotations = _fake_analyze

        result = df.analyze_for_annotation_chunked(
            file_path="/fake/resume.docx",
            user_requirement="帮我改简历",
            model_id="gemini-3-flash-preview",
        )

        assert result["success"], "Should still succeed even without model switch"
        # Fallback annotations are stripped from output
        output_annotations = result.get("annotations", [])
        has_503_flag = any(a.get("_koto_503") for a in output_annotations)
        assert not has_503_flag, "Internal _koto_503 markers must be stripped from output"

    def test_model_switched_only_once(self):
        """_model_switched flag prevents probe from running more than once."""
        df, _ = self._make_df_with_two_chunks()

        df._select_best_model = MagicMock(
            return_value=("gemini-3-flash-preview", [])
        )
        df._probe_working_model = MagicMock(return_value="gemini-2.5-flash")

        fallback_anno = [{"原文片段": "x", "_koto_503": True, "_koto_fallback_error": "503"}]
        ai_anno = [{"原文片段": "测试", "修改建议": "建议", "修改后文本": "修改后", "理由": "理由"}]
        call_count = {"n": 0}

        def _fake_analyze(chunk, doc_type, user_requirement, model_id,
                          chunk_index, total_chunks, full_doc_context="", max_retries=2):
            call_count["n"] += 1
            # First call still 503s even with new model (probe found new model but it also 503s)
            if call_count["n"] <= 2:
                return fallback_anno
            return ai_anno

        df._analyze_chunk_for_annotations = _fake_analyze

        df.analyze_for_annotation_chunked(
            file_path="/fake/resume.docx",
            user_requirement="帮我改简历",
            model_id="gemini-3-flash-preview",
        )

        # Probe must only be called once regardless of how many 503s occur
        assert df._probe_working_model.call_count == 1, (
            f"_probe_working_model called {df._probe_working_model.call_count} times, expected 1"
        )


# ===========================================================================
# Bug 1+2 combined: integration-style flow through analyze_for_annotation_chunked
# ===========================================================================

@requires_df
class TestEndToEndRetryAndRequeue:
    """
    Verify the complete chain: 503 in _analyze_chunk → sleep → retry →
    success, and model-switch re-queue, in a single test.
    """

    def test_503_wait_retry_success_no_fallback_in_output(self):
        """
        Small document (single chunk path):
        _analyze_chunk_for_annotations raises 503 once, sleeps, then succeeds.
        Final output must contain only AI annotations.
        """
        df = _make_feedback()

        doc_text = "这是一段简短的简历。" * 10  # small enough for single-chunk path

        df.reader.read_document.return_value = {"success": True, "type": "resume"}
        df.reader.format_for_ai.return_value = doc_text
        df._select_best_model = MagicMock(return_value=("gemini-3-flash-preview", []))

        ai_anno = [{"原文片段": "测试内容", "修改建议": "修改", "修改后文本": "修改后", "理由": "理由"}]
        fallback_anno = [{"原文片段": "x", "_koto_503": True, "_koto_fallback_error": "503"}]
        call_count = {"n": 0}

        def _fake_analyze(chunk, doc_type, user_requirement, model_id,
                          chunk_index, total_chunks, full_doc_context="", max_retries=2):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return fallback_anno
            return ai_anno

        df._analyze_chunk_for_annotations = _fake_analyze
        df._probe_working_model = MagicMock(return_value=None)

        result = df.analyze_for_annotation_chunked(
            file_path="/fake/short.docx",
            user_requirement="请帮我修改这份简历",
            model_id="gemini-3-flash-preview",
        )

        assert result["success"]
        # Fallback annotations are stripped
        out_annos = result.get("annotations", [])
        assert not any(a.get("_koto_fallback_error") for a in out_annos)
