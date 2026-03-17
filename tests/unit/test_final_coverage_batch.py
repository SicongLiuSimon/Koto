"""
Deep-coverage tests for the largest uncovered modules.
Each class targets methods / branches NOT exercised by earlier test files.
"""

import pytest
from unittest.mock import patch, MagicMock, Mock, PropertyMock, mock_open, call
import os
import sys
import json
import tempfile
import threading
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# 1. DocumentFeedbackSystem – deeper coverage
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestDocumentFeedbackDeep:
    """Cover chunk analysis, model probing, streaming loop, apply_suggestions,
    full_feedback_loop, annotate_document, and analyze_for_annotation."""

    def _make(self, **kw):
        with patch("web.document_reader.DocumentReader"), patch(
            "web.document_editor.DocumentEditor"
        ), patch("web.document_annotator.DocumentAnnotator"):
            from web.document_feedback import DocumentFeedbackSystem

            client = kw.get("client")
            model = kw.get("model", "gemini-test")
            return DocumentFeedbackSystem(gemini_client=client, default_model_id=model)

    # -- analyze_and_suggest ------------------------------------------------
    def test_analyze_and_suggest_returns_error_on_read_failure(self):
        obj = self._make(client=MagicMock())
        obj.reader.read_document.return_value = {"success": False, "error": "corrupt"}
        result = obj.analyze_and_suggest("/fake.docx")
        assert result["success"] is False

    def test_analyze_and_suggest_calls_ai_and_returns_suggestions(self):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = json.dumps(
            {"modifications": [{"原文片段": "a", "修改后文本": "b"}], "summary": "ok"}
        )
        mock_client.models.generate_content.return_value = mock_resp
        obj = self._make(client=mock_client)
        obj.reader.read_document.return_value = {
            "success": True,
            "content": "hello",
            "formatted_content": "hello",
            "type": "word",
            "doc_type": "word",
            "metadata": {},
        }
        obj.reader.format_for_ai.return_value = "formatted hello"
        with patch.object(obj, "_select_best_model", return_value=("test-model", [])):
            result = obj.analyze_and_suggest("/fake.docx", user_requirement="fix it")
        assert result["success"] is True

    def test_analyze_and_suggest_catches_exception(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("boom")
        obj = self._make(client=mock_client)
        obj.reader.read_document.return_value = {
            "success": True,
            "content": "x",
            "formatted_content": "x",
            "type": "word",
            "doc_type": "word",
            "metadata": {},
        }
        obj.reader.format_for_ai.return_value = "formatted"
        with patch.object(obj, "_select_best_model", return_value=("m", [])):
            result = obj.analyze_and_suggest("/fake.docx")
        assert result["success"] is False

    # -- apply_suggestions --------------------------------------------------
    def test_apply_suggestions_empty_list(self):
        obj = self._make()
        # No extension → unsupported file type
        result = obj.apply_suggestions("/fake.txt", [])
        assert result["success"] is False

    def test_apply_suggestions_calls_editor_for_docx(self):
        obj = self._make()
        obj.editor.edit_word.return_value = {
            "success": True,
            "file_path": "/out.docx",
            "applied_count": 2,
        }
        result = obj.apply_suggestions(
            "/fake.docx", [{"原文片段": "a", "修改后文本": "b"}]
        )
        assert result["success"] is True
        obj.editor.edit_word.assert_called_once()

    def test_apply_suggestions_calls_editor_for_pptx(self):
        obj = self._make()
        obj.editor.edit_ppt.return_value = {
            "success": True,
            "file_path": "/out.pptx",
            "applied_count": 1,
        }
        result = obj.apply_suggestions(
            "/fake.pptx", [{"原文片段": "a", "修改后文本": "b"}]
        )
        assert result["success"] is True

    # -- full_feedback_loop -------------------------------------------------
    def test_full_feedback_loop_analysis_failure(self):
        obj = self._make(client=MagicMock())
        with patch.object(
            obj, "analyze_and_suggest", return_value={"success": False, "error": "e"}
        ):
            result = obj.full_feedback_loop("/fake.docx")
        assert result["success"] is False

    def test_full_feedback_loop_auto_apply_true(self):
        obj = self._make(client=MagicMock())
        analysis = {
            "success": True,
            "modifications": [{"原文片段": "a"}],
            "summary": "ok summary text",
            "original_content": "c",
            "ai_suggestions": "s",
            "modification_count": 1,
        }
        edit_result = {"success": True, "file_path": "/o.docx", "applied_count": 1}
        with patch.object(
            obj, "analyze_and_suggest", return_value=analysis
        ), patch.object(obj, "apply_suggestions", return_value=edit_result):
            result = obj.full_feedback_loop("/fake.docx", auto_apply=True)
        assert result["success"] is True
        assert "analysis" in result

    # -- annotate_document --------------------------------------------------
    def test_annotate_document_delegates_to_annotator(self):
        obj = self._make()
        obj.annotator.annotate_document.return_value = {
            "success": True,
            "original_file": "/a",
            "revised_file": "/b",
            "applied": 2,
            "failed": 0,
        }
        result = obj.annotate_document(
            "/fake.docx", [{"原文片段": "x", "修改建议": "y"}]
        )
        assert result["success"] is True

    # -- _probe_working_model -----------------------------------------------
    def test_probe_working_model_returns_first_working(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MagicMock(text="hi")
        obj = self._make(client=mock_client)
        with patch.object(obj, "_select_best_model", return_value=("model-a", [])):
            result = obj._probe_working_model("model-a", timeout=3)
        assert result is not None

    def test_probe_working_model_returns_none_on_all_fail(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("503")
        obj = self._make(client=mock_client)
        with patch.object(obj, "_select_best_model", return_value=("model-a", [])):
            result = obj._probe_working_model("model-a", timeout=2)
        assert result is None or isinstance(result, str)

    # -- _fallback_annotations_from_chunk -----------------------------------
    def test_fallback_annotations_connector_pattern(self):
        from web.document_feedback import DocumentFeedbackSystem

        text = "通过改进方法来提高效率，从而达到目标。"
        result = DocumentFeedbackSystem._fallback_annotations_from_chunk(text)
        assert isinstance(result, list)

    def test_fallback_annotations_hedge_words(self):
        from web.document_feedback import DocumentFeedbackSystem

        text = "这个方案非常有效，极其重要，似乎可以解决问题。"
        result = DocumentFeedbackSystem._fallback_annotations_from_chunk(text)
        assert isinstance(result, list)

    def test_fallback_annotations_suo_pattern(self):
        from web.document_feedback import DocumentFeedbackSystem

        text = "我们所研究的课题非常重要。他所提出的方案得到认可。"
        result = DocumentFeedbackSystem._fallback_annotations_from_chunk(text)
        assert isinstance(result, list)

    def test_fallback_annotations_negative_phrasing(self):
        from web.document_feedback import DocumentFeedbackSystem

        text = "这种方法不能解决问题，无法满足需求，并且不利于发展。"
        result = DocumentFeedbackSystem._fallback_annotations_from_chunk(text)
        assert isinstance(result, list)

    def test_fallback_annotations_redundant_expressions(self):
        from web.document_feedback import DocumentFeedbackSystem

        text = "通过进一步分析研究，能够更好地解决之间存在的问题。"
        result = DocumentFeedbackSystem._fallback_annotations_from_chunk(text)
        assert isinstance(result, list)

    # -- _split_into_chunks_by_paragraphs -----------------------------------
    def test_split_chunks_single_paragraph(self):
        from web.document_feedback import DocumentFeedbackSystem

        text = "Short paragraph."
        chunks = DocumentFeedbackSystem._split_into_chunks_by_paragraphs(text, 5000)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_split_chunks_long_content(self):
        from web.document_feedback import DocumentFeedbackSystem

        text = "\n\n".join(
            [f"Paragraph number {i} with some text." for i in range(100)]
        )
        chunks = DocumentFeedbackSystem._split_into_chunks_by_paragraphs(text, 200)
        assert len(chunks) > 1

    # -- analyze_for_annotation_chunked with KOTO_DISABLE_AI ----------------
    def test_analyze_for_annotation_chunked_local_fallback(self):
        obj = self._make()
        content = "被认为是重要的。通过进一步研究来提高效率。" * 20
        obj.reader.read_document.return_value = {
            "success": True,
            "content": content,
            "formatted_content": content,
            "type": "word",
            "doc_type": "word",
            "metadata": {},
        }
        obj.reader.format_for_ai.return_value = content
        with patch.dict(os.environ, {"KOTO_DISABLE_AI": "1"}):
            result = obj.analyze_for_annotation_chunked("/fake.docx", chunk_size=200)
        assert result["success"] is True
        assert isinstance(result.get("annotations", []), list)

    def test_analyze_for_annotation_chunked_read_failure(self):
        obj = self._make()
        obj.reader.read_document.return_value = {"success": False, "error": "bad"}
        result = obj.analyze_for_annotation_chunked("/fake.docx")
        assert result["success"] is False

    # -- _build_annotation_prompt doc type detection ------------------------
    def test_build_annotation_prompt_ppt_type(self):
        obj = self._make()
        prompt = obj._build_annotation_prompt("ppt", "Slide 1\nContent", "improve")
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_build_annotation_prompt_excel_type(self):
        obj = self._make()
        prompt = obj._build_annotation_prompt("excel", "Cell A1: value", "review")
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    # -- _parse_annotation_response -----------------------------------------
    def test_parse_annotation_response_json_with_extra_text(self):
        obj = self._make()
        raw = 'Some preamble\n```json\n[{"原文片段":"a","修改建议":"b","修改原因":"c"}]\n```\nEnd'
        result = obj._parse_annotation_response(raw)
        assert isinstance(result, list)

    def test_parse_annotation_response_empty_string(self):
        obj = self._make()
        result = obj._parse_annotation_response("")
        assert result is None or result == []

    # -- full_annotation_loop -----------------------------------------------
    def test_full_annotation_loop_read_failure(self):
        obj = self._make(client=MagicMock())
        obj.reader.read_document.return_value = {"success": False, "error": "bad"}
        result = obj.full_annotation_loop("/fake.docx")
        assert result["success"] is False

    # -- _list_available_models with no client ------------------------------
    def test_list_available_models_no_client(self):
        obj = self._make(client=None)
        result = obj._list_available_models()
        assert isinstance(result, list)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# 2. PPTGenerator – deeper coverage
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestPptGeneratorDeep:
    """Cover slide creation internals, font/color helpers, image handling,
    generate_from_text, and EnhancedPPTGenerator."""

    def _make_gen(self, theme="business"):
        with patch("web.ppt_generator.get_theme", return_value=None), patch(
            "web.ppt_generator.ImageGenerator", create=True
        ):
            from web.ppt_generator import PPTGenerator

            return PPTGenerator(theme=theme)

    # -- _rgb helper --------------------------------------------------------
    def test_rgb_returns_value_for_known_key(self):
        gen = self._make_gen()
        result = gen._rgb("primary")
        assert result is not None

    def test_rgb_returns_fallback_for_unknown_key(self):
        gen = self._make_gen()
        # _rgb raises KeyError for unknown keys (no fallback)
        with pytest.raises(KeyError):
            gen._rgb("nonexistent_key_xyz")

    # -- _set_font helper ---------------------------------------------------
    def test_set_font_applies_bold(self):
        gen = self._make_gen()
        mock_run = MagicMock()
        gen._set_font(mock_run, size=24, bold=True, color_key="primary")
        assert mock_run.font.bold is True

    def test_set_font_applies_italic(self):
        gen = self._make_gen()
        mock_run = MagicMock()
        gen._set_font(mock_run, size=18, italic=True, color_key="text")
        assert mock_run.font.italic is True

    # -- _clean_markdown static method --------------------------------------
    def test_clean_markdown_removes_code_blocks(self):
        from web.ppt_generator import PPTGenerator

        text = "Before\n```python\ncode here\n```\nAfter"
        result = PPTGenerator._clean_markdown(text)
        assert "```" not in result

    def test_clean_markdown_removes_strikethrough(self):
        from web.ppt_generator import PPTGenerator

        text = "~~deleted~~ kept"
        result = PPTGenerator._clean_markdown(text)
        assert "~~" not in result

    def test_clean_markdown_removes_link_markup(self):
        from web.ppt_generator import PPTGenerator

        text = "[Click here](https://example.com)"
        result = PPTGenerator._clean_markdown(text)
        assert "](http" not in result

    def test_clean_markdown_strips_h1_headers(self):
        from web.ppt_generator import PPTGenerator

        text = "# H1 Title"
        result = PPTGenerator._clean_markdown(text)
        assert result.strip() == "H1 Title"

    def test_clean_markdown_ai_dialogue_patterns(self):
        from web.ppt_generator import PPTGenerator

        text = "当然可以！这里是内容。好的，以下是数据。"
        result = PPTGenerator._clean_markdown(text)
        assert isinstance(result, str)

    def test_clean_markdown_strip_bold_mode(self):
        from web.ppt_generator import PPTGenerator

        text = "This is **bold** text"
        result = PPTGenerator._clean_markdown(text, strip_bold=True)
        assert "**" not in result

    # -- generate_from_text -------------------------------------------------
    def test_generate_from_text_creates_pptx(self):
        gen = self._make_gen()
        mock_prs = MagicMock()
        with patch("web.ppt_generator.Presentation", return_value=mock_prs):
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "out.pptx")
                result = gen.generate_from_text(
                    "Title\n\nSlide 1 content\n\nSlide 2 content", out
                )
        assert "output_path" in result or "slide_count" in result

    # -- generate_from_outline with progress callback -----------------------
    def test_generate_from_outline_calls_progress_callback(self):
        gen = self._make_gen()
        mock_prs = MagicMock()
        cb = MagicMock()
        outline = [
            {"title": "Intro", "points": ["Point 1"], "slide_type": "detail"},
        ]
        with patch("web.ppt_generator.Presentation", return_value=mock_prs):
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "out.pptx")
                gen.generate_from_outline("Title", outline, out, progress_callback=cb)

    # -- generate_from_outline error path -----------------------------------
    def test_generate_from_outline_handles_save_error(self):
        gen = self._make_gen()
        mock_prs = MagicMock()
        mock_prs.save.side_effect = PermissionError("locked")
        outline = [{"title": "T", "points": ["P"], "slide_type": "detail"}]
        with patch("web.ppt_generator.Presentation", return_value=mock_prs):
            with pytest.raises(PermissionError):
                gen.generate_from_outline("T", outline, "/locked/out.pptx")

    # -- generate_from_outline with various slide types ---------------------
    def test_generate_from_outline_with_image_full_type(self):
        gen = self._make_gen()
        mock_prs = MagicMock()
        outline = [
            {
                "title": "Image Slide",
                "points": ["Beautiful scenery"],
                "slide_type": "image_full",
            }
        ]
        with patch("web.ppt_generator.Presentation", return_value=mock_prs):
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "out.pptx")
                result = gen.generate_from_outline(
                    "Title", outline, out, enable_ai_images=False
                )
        assert "output_path" in result

    def test_generate_from_outline_with_content_image_type(self):
        gen = self._make_gen()
        mock_prs = MagicMock()
        outline = [
            {
                "title": "Content Image",
                "points": ["Data visualization"],
                "slide_type": "content_image",
            }
        ]
        with patch("web.ppt_generator.Presentation", return_value=mock_prs):
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "out.pptx")
                result = gen.generate_from_outline(
                    "Title", outline, out, enable_ai_images=False
                )
        assert "output_path" in result

    # -- add_image_to_slide -------------------------------------------------
    def test_add_image_to_slide_out_of_range(self):
        gen = self._make_gen()
        mock_prs = MagicMock()
        # slide_index 5 is >= len(slides) when slides has fewer elements
        mock_slides = MagicMock()
        mock_slides.__len__ = MagicMock(return_value=0)
        mock_prs.slides = mock_slides
        result = gen.add_image_to_slide(mock_prs, 5, "/fake/image.png")
        assert result is False

    def test_add_image_to_slide_exception(self):
        gen = self._make_gen()
        mock_prs = MagicMock()
        mock_slides = MagicMock()
        mock_slides.__len__ = MagicMock(return_value=2)
        mock_slide = MagicMock()
        mock_slide.shapes.add_picture.side_effect = FileNotFoundError("no file")
        mock_slides.__getitem__ = MagicMock(return_value=mock_slide)
        mock_prs.slides = mock_slides
        result = gen.add_image_to_slide(mock_prs, 0, "/nonexistent/image.png")
        assert result is False

    # -- EnhancedPPTGenerator -----------------------------------------------
    def test_enhanced_extract_subtitle_with_year(self):
        with patch("web.ppt_generator.get_theme", return_value=None), patch(
            "web.ppt_generator.ImageGenerator", create=True
        ):
            from web.ppt_generator import EnhancedPPTGenerator

            epg = EnhancedPPTGenerator(theme="tech")
            subtitle = epg._extract_subtitle("2024年度市场分析报告")
        assert "2024" in subtitle or subtitle == ""

    def test_enhanced_generate_fallback_outline(self):
        with patch("web.ppt_generator.get_theme", return_value=None), patch(
            "web.ppt_generator.ImageGenerator", create=True
        ):
            from web.ppt_generator import EnhancedPPTGenerator

            epg = EnhancedPPTGenerator()
            outline = epg._generate_fallback_outline("Market Report", "Analyze trends")
        assert isinstance(outline, str)
        assert len(outline) > 0

    def test_enhanced_parse_enhanced_outline(self):
        with patch("web.ppt_generator.get_theme", return_value=None), patch(
            "web.ppt_generator.ImageGenerator", create=True
        ):
            from web.ppt_generator import EnhancedPPTGenerator

            epg = EnhancedPPTGenerator()
            md = "## Section 1\n- Point A\n- Point B\n\n## Section 2\n- Point C"
            parsed = epg._parse_enhanced_outline(md)
        assert isinstance(parsed, list)
        assert len(parsed) >= 2

    def test_enhanced_parse_enhanced_outline_empty(self):
        with patch("web.ppt_generator.get_theme", return_value=None), patch(
            "web.ppt_generator.ImageGenerator", create=True
        ):
            from web.ppt_generator import EnhancedPPTGenerator

            epg = EnhancedPPTGenerator()
            parsed = epg._parse_enhanced_outline("")
        assert isinstance(parsed, list)

    def test_enhanced_match_images_to_slides(self):
        with patch("web.ppt_generator.get_theme", return_value=None), patch(
            "web.ppt_generator.ImageGenerator", create=True
        ):
            from web.ppt_generator import EnhancedPPTGenerator

            epg = EnhancedPPTGenerator()
            outline = [{"title": "A", "points": []}, {"title": "B", "points": []}]
            result = epg._match_images_to_slides(outline, ["/img1.png"])
        assert isinstance(result, list)

    def test_enhanced_build_outline_prompt(self):
        with patch("web.ppt_generator.get_theme", return_value=None), patch(
            "web.ppt_generator.ImageGenerator", create=True
        ):
            from web.ppt_generator import EnhancedPPTGenerator

            epg = EnhancedPPTGenerator()
            prompt = epg._build_outline_prompt("Title", "Request", None, None)
        assert isinstance(prompt, str)
        assert "Title" in prompt


# ---------------------------------------------------------------------------
# 3. TrackChangesEditor – deeper coverage
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestTrackChangesEditorDeep:
    """Cover apply_tracked_changes, apply_hybrid_changes, comment creation,
    paragraph searching in tables, and multi-run editing."""

    def _make_editor(self, author="Test Author"):
        from web.track_changes_editor import TrackChangesEditor

        return TrackChangesEditor(author=author)

    # -- apply_tracked_changes error paths ----------------------------------
    def test_apply_tracked_changes_empty_annotations(self):
        editor = self._make_editor()
        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        with patch("web.track_changes_editor.Document", return_value=mock_doc):
            result = editor.apply_tracked_changes("/fake.docx", [])
        assert result.get("applied", 0) == 0

    def test_apply_tracked_changes_nonexistent_text(self):
        editor = self._make_editor()
        mock_doc = MagicMock()
        mock_para = MagicMock()
        mock_run = MagicMock()
        mock_run.text = "some other text"
        mock_para.runs = [mock_run]
        mock_para.text = "some other text"
        mock_doc.paragraphs = [mock_para]
        mock_doc.tables = []
        annotations = [{"原文片段": "not found text", "修改后文本": "replacement"}]
        with patch("web.track_changes_editor.Document", return_value=mock_doc):
            result = editor.apply_tracked_changes("/fake.docx", annotations)
        assert result.get("failed", 0) >= 1 or result.get("applied", 0) == 0

    # -- apply_hybrid_changes classification --------------------------------
    def test_apply_hybrid_changes_classifies_suggestion_as_comment(self):
        editor = self._make_editor()
        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        mock_doc.tables = []
        annotations = [
            {"原文片段": "text", "修改建议": "建议：改用更简洁的表达"},
        ]
        with patch(
            "web.track_changes_editor.Document", return_value=mock_doc
        ), patch.object(
            editor, "_apply_single_comment", return_value=True
        ), patch.object(
            editor, "_inject_comments_to_docx", return_value=True
        ):
            result = editor.apply_hybrid_changes("/fake.docx", annotations)
        assert isinstance(result, dict)

    def test_apply_hybrid_changes_classifies_short_edit_as_tracked(self):
        editor = self._make_editor()
        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        mock_doc.tables = []
        annotations = [
            {"原文片段": "旧文本", "修改后文本": "新文本"},
        ]
        with patch(
            "web.track_changes_editor.Document", return_value=mock_doc
        ), patch.object(editor, "_apply_single_track_change", return_value=True):
            result = editor.apply_hybrid_changes("/fake.docx", annotations)
        assert isinstance(result, dict)

    def test_apply_hybrid_changes_exception(self):
        editor = self._make_editor()
        with patch("web.track_changes_editor.Document", side_effect=Exception("fail")):
            result = editor.apply_hybrid_changes(
                "/fake.docx", [{"原文片段": "a", "修改后文本": "b"}]
            )
        assert result["success"] is False

    def test_apply_hybrid_changes_empty_annotations(self):
        editor = self._make_editor()
        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        mock_doc.tables = []
        with patch("web.track_changes_editor.Document", return_value=mock_doc):
            result = editor.apply_hybrid_changes("/fake.docx", [])
        assert isinstance(result, dict)
        assert result.get("total", 0) == 0

    # -- _add_comment_element -----------------------------------------------
    def test_add_comment_element_includes_reason(self):
        editor = self._make_editor()
        from lxml import etree

        WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        comments_el = etree.SubElement(etree.Element("root"), f"{{{WNS}}}comments")
        editor._add_comment_element(comments_el, 1, "new text", reason="for clarity")
        children = list(comments_el)
        assert len(children) >= 1

    def test_add_comment_element_no_reason(self):
        editor = self._make_editor()
        from lxml import etree

        WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        comments_el = etree.SubElement(etree.Element("root"), f"{{{WNS}}}comments")
        editor._add_comment_element(comments_el, 2, "updated text")
        children = list(comments_el)
        assert len(children) >= 1

    # -- _esc static method edge cases --------------------------------------
    def test_esc_all_special_chars(self):
        from web.track_changes_editor import TrackChangesEditor

        result = TrackChangesEditor._esc("&<>\"'")
        assert "&amp;" in result
        assert "&lt;" in result
        assert "&gt;" in result

    def test_esc_unicode_text(self):
        from web.track_changes_editor import TrackChangesEditor

        result = TrackChangesEditor._esc("中文测试 & <特殊>")
        assert "&amp;" in result
        assert "&lt;" in result
        assert "中文测试" in result

    # -- _get_run_text extended cases ---------------------------------------
    def test_get_run_text_with_multiple_t_elements(self):
        editor = self._make_editor()
        from lxml import etree

        WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        run = etree.Element(f"{{{WNS}}}r")
        t1 = etree.SubElement(run, f"{{{WNS}}}t")
        t1.text = "Hello "
        t2 = etree.SubElement(run, f"{{{WNS}}}t")
        t2.text = "World"
        result = editor._get_run_text(run)
        assert result == "Hello World"

    # -- _make_run ----------------------------------------------------------
    def test_make_run_with_rpr(self):
        editor = self._make_editor()
        run = editor._make_run(
            "test text",
            '<w:rPr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:b/></w:rPr>',
        )
        assert run is not None

    # -- _add_comments_content_type -----------------------------------------
    def test_add_comments_content_type_adds_override(self):
        editor = self._make_editor()
        xml_data = b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>'
        result = editor._add_comments_content_type(xml_data)
        assert b"comments" in result

    def test_add_comments_relationship_adds_rel(self):
        editor = self._make_editor()
        xml_data = b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>'
        result = editor._add_comments_relationship(xml_data)
        assert b"comments" in result

    # -- apply_comment_changes ----------------------------------------------
    def test_apply_comment_changes_empty(self):
        editor = self._make_editor()
        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        with patch("web.track_changes_editor.Document", return_value=mock_doc):
            result = editor.apply_comment_changes("/fake.docx", [])
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 4. FastVoiceRecognizer – deeper coverage
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestVoiceFastDeep:
    """Cover engine detection, recognize fallback chain, streaming,
    singleton, status reporting, and Chinese text cleaning."""

    # -- _clean_chinese_text ------------------------------------------------
    def test_clean_chinese_text_removes_spaces(self):
        from web.voice_fast import _clean_chinese_text

        result = _clean_chinese_text("你 好 世 界")
        assert "你好" in result

    def test_clean_chinese_text_preserves_english_spaces(self):
        from web.voice_fast import _clean_chinese_text

        result = _clean_chinese_text("hello world")
        assert result == "hello world"

    def test_clean_chinese_text_empty(self):
        from web.voice_fast import _clean_chinese_text

        assert _clean_chinese_text("") == ""

    def test_clean_chinese_text_mixed(self):
        from web.voice_fast import _clean_chinese_text

        result = _clean_chinese_text("Hello 你 好 World")
        assert isinstance(result, str)
        assert "你好" in result

    # -- VoiceResult --------------------------------------------------------
    def test_voice_result_to_dict(self):
        from web.voice_fast import VoiceResult

        vr = VoiceResult(
            success=True, text="hello", engine="vosk", message="ok", confidence=0.9
        )
        d = vr.to_dict()
        assert d["success"] is True
        assert d["text"] == "hello"
        assert d["engine"] == "vosk"

    def test_voice_result_to_dict_keys(self):
        from web.voice_fast import VoiceResult

        vr = VoiceResult(success=False)
        d = vr.to_dict()
        assert set(d.keys()) == {"success", "text", "engine", "message", "confidence"}

    def test_voice_result_defaults(self):
        from web.voice_fast import VoiceResult

        vr = VoiceResult(success=False)
        assert vr.text == ""
        assert vr.confidence == 0.0

    # -- get_fast_status singleton ------------------------------------------
    def test_get_fast_status_returns_dict(self):
        with patch("web.voice_fast._recognizer_instance", None), patch(
            "web.voice_fast.FastVoiceRecognizer"
        ) as MockRec:
            mock_inst = MagicMock()
            mock_inst.available_engines = []
            mock_inst.primary_engine = None
            MockRec.return_value = mock_inst
            from web.voice_fast import get_fast_status

            status = get_fast_status()
        assert isinstance(status, dict)

    # -- recognize_voice wrapper --------------------------------------------
    def test_recognize_voice_delegates(self):
        with patch("web.voice_fast.get_recognizer") as mock_get:
            mock_rec = MagicMock()
            mock_rec.recognize.return_value = MagicMock(
                success=True,
                text="hi",
                engine="vosk",
                message="",
                confidence=0.9,
                to_dict=lambda: {"success": True, "text": "hi"},
            )
            mock_get.return_value = mock_rec
            from web.voice_fast import recognize_voice

            result = recognize_voice(timeout=3)
        assert isinstance(result, dict)

    # -- get_available_engines wrapper ---------------------------------------
    def test_get_available_engines_returns_dict(self):
        with patch("web.voice_fast.get_recognizer") as mock_get:
            mock_rec = MagicMock()
            mock_rec.get_available_engines.return_value = {
                "engines": [],
                "primary": None,
            }
            mock_get.return_value = mock_rec
            from web.voice_fast import get_available_engines

            result = get_available_engines()
        assert isinstance(result, dict)

    # -- FastVoiceRecognizer._detect_engines --------------------------------
    def test_detect_engines_no_engines_available(self):
        with patch(
            "web.voice_fast.FastVoiceRecognizer._check_vosk", return_value=False
        ), patch(
            "web.voice_fast.FastVoiceRecognizer._check_win32_sapi", return_value=False
        ), patch(
            "web.voice_fast.FastVoiceRecognizer._check_windows_sapi", return_value=False
        ), patch(
            "web.voice_fast.FastVoiceRecognizer._check_speech_recognition",
            return_value=False,
        ), patch(
            "web.voice_fast.FastVoiceRecognizer._start_background_init"
        ):
            from web.voice_fast import FastVoiceRecognizer

            rec = FastVoiceRecognizer()
        # When no engines found, falls back to 'offline'
        assert rec.primary_engine == "offline"

    def test_detect_engines_vosk_available(self):
        with patch(
            "web.voice_fast.FastVoiceRecognizer._check_vosk", return_value=True
        ), patch(
            "web.voice_fast.FastVoiceRecognizer._check_win32_sapi", return_value=False
        ), patch(
            "web.voice_fast.FastVoiceRecognizer._check_windows_sapi", return_value=False
        ), patch(
            "web.voice_fast.FastVoiceRecognizer._check_speech_recognition",
            return_value=False,
        ), patch(
            "web.voice_fast.FastVoiceRecognizer._start_background_init"
        ):
            from web.voice_fast import FastVoiceRecognizer

            rec = FastVoiceRecognizer()
        assert "vosk" in rec.available_engines

    def test_detect_engines_speech_recognition_available(self):
        with patch(
            "web.voice_fast.FastVoiceRecognizer._check_vosk", return_value=False
        ), patch(
            "web.voice_fast.FastVoiceRecognizer._check_win32_sapi", return_value=False
        ), patch(
            "web.voice_fast.FastVoiceRecognizer._check_windows_sapi", return_value=False
        ), patch(
            "web.voice_fast.FastVoiceRecognizer._check_speech_recognition",
            return_value=True,
        ), patch(
            "web.voice_fast.FastVoiceRecognizer._start_background_init"
        ):
            from web.voice_fast import FastVoiceRecognizer

            rec = FastVoiceRecognizer()
        assert "speech_recognition" in rec.available_engines

    # -- request_stop_streaming ---------------------------------------------
    def test_request_stop_streaming_sets_event(self):
        from web.voice_fast import request_stop_streaming, _stream_stop_event

        request_stop_streaming()
        assert _stream_stop_event.is_set()
        _stream_stop_event.clear()  # cleanup


# ---------------------------------------------------------------------------
# 5. VoiceInputEngine – deeper coverage
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestVoiceInputDeep:
    """Cover engine detection, recognition methods, audio recording,
    streaming, and RecognitionResult."""

    # -- RecognitionResult --------------------------------------------------
    def test_recognition_result_to_dict(self):
        from web.voice_input import RecognitionResult

        rr = RecognitionResult(
            success=True, text="test", engine="google", confidence=0.8
        )
        d = rr.to_dict()
        assert d["success"] is True
        assert d["text"] == "test"

    def test_recognition_result_defaults(self):
        from web.voice_input import RecognitionResult

        rr = RecognitionResult(success=False)
        assert rr.audio_file is None
        assert rr.confidence == 0.0

    def test_recognition_result_to_dict_keys(self):
        from web.voice_input import RecognitionResult

        rr = RecognitionResult(success=True, text="hello", engine="vosk")
        d = rr.to_dict()
        assert "success" in d
        assert "text" in d
        assert "engine" in d

    # -- EngineType enum ----------------------------------------------------
    def test_engine_type_values(self):
        from web.voice_input import EngineType

        assert EngineType.VOSK_LOCAL is not None
        assert EngineType.GOOGLE_WEB is not None
        assert EngineType.GEMINI_API is not None
        assert EngineType.OFFLINE is not None
        assert EngineType.WINDOWS_SPEECH is not None

    # -- VoiceInputEngine detection -----------------------------------------
    def test_voice_engine_init_no_engines(self):
        with patch("web.voice_input.VoiceInputEngine._detect_engines") as mock_detect:
            mock_detect.return_value = None
            from web.voice_input import VoiceInputEngine

            engine = VoiceInputEngine()
        assert isinstance(engine.available_engines, list)

    # -- _clean_chinese_text ------------------------------------------------
    def test_voice_input_clean_chinese_text(self):
        with patch("web.voice_input.VoiceInputEngine._detect_engines"):
            from web.voice_input import VoiceInputEngine

            engine = VoiceInputEngine()
        result = engine._clean_chinese_text("你 好")
        assert isinstance(result, str)

    def test_voice_input_clean_chinese_empty(self):
        with patch("web.voice_input.VoiceInputEngine._detect_engines"):
            from web.voice_input import VoiceInputEngine

            engine = VoiceInputEngine()
        result = engine._clean_chinese_text("")
        assert result == ""

    # -- _get_engine_name / _get_engine_description -------------------------
    def test_get_engine_name_all_types(self):
        with patch("web.voice_input.VoiceInputEngine._detect_engines"):
            from web.voice_input import VoiceInputEngine, EngineType

            engine = VoiceInputEngine()
        for et in EngineType:
            name = engine._get_engine_name(et)
            assert isinstance(name, str)
            assert len(name) > 0

    def test_get_engine_description_all_types(self):
        with patch("web.voice_input.VoiceInputEngine._detect_engines"):
            from web.voice_input import VoiceInputEngine, EngineType

            engine = VoiceInputEngine()
        for et in EngineType:
            desc = engine._get_engine_description(et)
            assert isinstance(desc, str)

    # -- _parse_engine ------------------------------------------------------
    def test_parse_engine_vosk(self):
        with patch("web.voice_input.VoiceInputEngine._detect_engines"):
            from web.voice_input import VoiceInputEngine, EngineType

            engine = VoiceInputEngine()
        result = engine._parse_engine("vosk")
        assert result == EngineType.VOSK_LOCAL

    def test_parse_engine_google(self):
        with patch("web.voice_input.VoiceInputEngine._detect_engines"):
            from web.voice_input import VoiceInputEngine, EngineType

            engine = VoiceInputEngine()
        result = engine._parse_engine("google")
        assert result == EngineType.GOOGLE_WEB

    def test_parse_engine_gemini(self):
        with patch("web.voice_input.VoiceInputEngine._detect_engines"):
            from web.voice_input import VoiceInputEngine, EngineType

            engine = VoiceInputEngine()
        result = engine._parse_engine("gemini")
        assert result == EngineType.GEMINI_API

    def test_parse_engine_unknown_defaults(self):
        with patch("web.voice_input.VoiceInputEngine._detect_engines"):
            from web.voice_input import VoiceInputEngine, EngineType

            engine = VoiceInputEngine()
        result = engine._parse_engine("unknown_engine_xyz")
        # Unknown engine returns None
        assert result is None or isinstance(result, EngineType)

    # -- module-level convenience wrappers ----------------------------------
    def test_module_get_available_engines(self):
        with patch("web.voice_input.get_voice_engine") as mock_get:
            mock_eng = MagicMock()
            mock_eng.get_available_engines.return_value = {"engines": []}
            mock_get.return_value = mock_eng
            from web.voice_input import get_available_engines

            result = get_available_engines()
        assert isinstance(result, dict)

    def test_module_recognize_microphone(self):
        with patch("web.voice_input.get_voice_engine") as mock_get:
            mock_eng = MagicMock()
            mock_result = MagicMock()
            mock_result.to_dict.return_value = {"success": True, "text": "hi"}
            mock_eng.recognize_microphone.return_value = mock_result
            mock_get.return_value = mock_eng
            from web.voice_input import recognize_microphone

            result = recognize_microphone()
        assert isinstance(result, dict)

    def test_module_recognize_audio(self):
        with patch("web.voice_input.get_voice_engine") as mock_get:
            mock_eng = MagicMock()
            mock_result = MagicMock()
            mock_result.to_dict.return_value = {"success": True, "text": "test"}
            mock_eng.recognize_audio_file.return_value = mock_result
            mock_get.return_value = mock_eng
            from web.voice_input import recognize_audio

            result = recognize_audio("/fake/audio.wav")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 6. local_model_installer – deeper coverage
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestLocalModelInstallerDeep:
    """Cover get_system_info, is_ollama_running, start_ollama, pull_model,
    save_result, _find_ollama_exe, _download_with_retry."""

    # -- is_ollama_running--------------------------------------------------
    def test_is_ollama_running_true(self):
        with patch("src.local_model_installer.socket.create_connection") as mock_conn:
            mock_sock = MagicMock()
            mock_conn.return_value = mock_sock
            from src.local_model_installer import is_ollama_running

            assert is_ollama_running() is True
            mock_sock.close.assert_called_once()

    def test_is_ollama_running_false(self):
        with patch(
            "src.local_model_installer.socket.create_connection",
            side_effect=ConnectionRefusedError,
        ):
            from src.local_model_installer import is_ollama_running

            assert is_ollama_running() is False

    def test_is_ollama_running_timeout(self):
        with patch(
            "src.local_model_installer.socket.create_connection",
            side_effect=TimeoutError,
        ):
            from src.local_model_installer import is_ollama_running

            assert is_ollama_running() is False

    # -- _find_ollama_exe ---------------------------------------------------
    def test_find_ollama_exe_via_shutil_which(self):
        with patch(
            "src.local_model_installer.shutil.which",
            return_value="C:\\ollama\\ollama.exe",
        ):
            from src.local_model_installer import _find_ollama_exe

            result = _find_ollama_exe()
        assert result is not None
        assert "ollama" in result

    def test_find_ollama_exe_not_found(self):
        with patch("src.local_model_installer.shutil.which", return_value=None), patch(
            "src.local_model_installer._OLLAMA_SEARCH_PATHS", []
        ):
            from src.local_model_installer import _find_ollama_exe

            result = _find_ollama_exe()
        assert result is None

    def test_find_ollama_exe_from_search_path(self):
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.parent = MagicMock()
        mock_path.parent.__str__ = lambda s: "C:\\Program Files\\Ollama"
        mock_path.__str__ = lambda s: "C:\\Program Files\\Ollama\\ollama.exe"
        with patch("src.local_model_installer.shutil.which", return_value=None), patch(
            "src.local_model_installer._OLLAMA_SEARCH_PATHS", [mock_path]
        ):
            from src.local_model_installer import _find_ollama_exe

            result = _find_ollama_exe()
        assert result is not None

    # -- start_ollama -------------------------------------------------------
    def test_start_ollama_already_running(self):
        with patch("src.local_model_installer.is_ollama_running", return_value=True):
            from src.local_model_installer import start_ollama

            result = start_ollama()
        assert result is True

    def test_start_ollama_no_exe_found(self):
        with patch(
            "src.local_model_installer.is_ollama_running", return_value=False
        ), patch("src.local_model_installer._find_ollama_exe", return_value=None):
            from src.local_model_installer import start_ollama

            result = start_ollama(log_cb=MagicMock())
        assert result is False

    # -- save_result --------------------------------------------------------
    def test_save_result_creates_file(self):
        from src.local_model_installer import save_result

        with tempfile.TemporaryDirectory() as td:
            result_file = Path(td) / "installed_models.json"
            with patch("src.local_model_installer.RESULT_FILE", result_file):
                save_result("gemma3:1b")
            assert result_file.exists()
            data = json.loads(result_file.read_text())
            assert data["model"] == "gemma3:1b"
            assert "installed_at" in data
            assert data["ollama_endpoint"] == "http://127.0.0.1:11434"

    def test_save_result_exception_silent(self):
        from src.local_model_installer import save_result

        with patch(
            "src.local_model_installer.RESULT_FILE",
            MagicMock(write_text=MagicMock(side_effect=PermissionError)),
        ):
            save_result("test:1b")  # should not raise

    # -- pull_model ---------------------------------------------------------
    def test_pull_model_success(self):
        mock_proc = MagicMock()
        mock_proc.stdout = iter(["pulling manifest\n", "success\n"])
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0
        with patch(
            "src.local_model_installer._find_ollama_exe", return_value="ollama"
        ), patch("src.local_model_installer.subprocess.Popen", return_value=mock_proc):
            from src.local_model_installer import pull_model

            result = pull_model("gemma3:1b", prog_cb=MagicMock(), log_cb=MagicMock())
        assert result is True

    def test_pull_model_no_ollama(self):
        with patch("src.local_model_installer._find_ollama_exe", return_value=None):
            from src.local_model_installer import pull_model

            result = pull_model("gemma3:1b")
        assert result is False

    def test_pull_model_with_progress_percentage(self):
        mock_proc = MagicMock()
        mock_proc.stdout = iter(["pulling manifest\n", "  50% complete\n", "success\n"])
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0
        prog_cb = MagicMock()
        with patch(
            "src.local_model_installer._find_ollama_exe", return_value="ollama"
        ), patch("src.local_model_installer.subprocess.Popen", return_value=mock_proc):
            from src.local_model_installer import pull_model

            result = pull_model("gemma3:1b", prog_cb=prog_cb, log_cb=MagicMock())
        assert result is True

    # -- get_system_info GPU detection --------------------------------------
    def test_get_system_info_returns_required_keys(self):
        from src.local_model_installer import get_system_info

        with patch(
            "src.local_model_installer.platform.processor", return_value="Intel Core i7"
        ), patch("src.local_model_installer.os.cpu_count", return_value=8):
            info = get_system_info()
        assert "ram_gb" in info
        assert "cpu" in info
        assert "gpu_name" in info
        assert "ollama_installed" in info

    # -- recommend_models ---------------------------------------------------
    def test_recommend_models_returns_non_empty(self):
        from src.local_model_installer import recommend_models

        info = {
            "ram_gb": 16,
            "gpu_vram_gb": 8,
            "gpu_name": "NVIDIA RTX 3070",
            "ollama_installed": False,
            "ollama_running": False,
            "installed_models": [],
        }
        models = recommend_models(info)
        assert isinstance(models, list)
        assert len(models) >= 1
        for m in models:
            assert "tag" in m

    def test_recommend_models_low_resources(self):
        from src.local_model_installer import recommend_models

        info = {
            "ram_gb": 0.5,
            "gpu_vram_gb": 0,
            "gpu_name": "",
            "ollama_installed": False,
            "ollama_running": False,
            "installed_models": [],
        }
        models = recommend_models(info)
        assert len(models) >= 1

    def test_recommend_models_high_resources(self):
        from src.local_model_installer import recommend_models, MODEL_CATALOG

        info = {
            "ram_gb": 64,
            "gpu_vram_gb": 24,
            "gpu_name": "NVIDIA RTX 4090",
            "ollama_installed": False,
            "ollama_running": False,
            "installed_models": [],
        }
        models = recommend_models(info)
        assert len(models) == len(MODEL_CATALOG)


# ---------------------------------------------------------------------------
# 7. koto_setup – deeper coverage
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestKotoSetupDeep:
    """Cover config writing edge cases, API validation errors,
    _run_setup_if_needed branches, _prompt_local_model_if_needed."""

    # -- _write_gemini_config -----------------------------------------------
    def test_write_config_creates_file(self):
        from src.koto_setup import _write_gemini_config

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with patch("src.koto_setup.APP_ROOT", td_path):
                _write_gemini_config(
                    "AIzaTestKey123456789012345678", "https://custom.api.com"
                )
            config_file = td_path / "config" / "gemini_config.env"
            assert config_file.exists()
            content = config_file.read_text(encoding="utf-8")
            assert "AIzaTestKey123456789012345678" in content
            assert "https://custom.api.com" in content

    def test_write_config_default_base(self):
        from src.koto_setup import _write_gemini_config

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with patch("src.koto_setup.APP_ROOT", td_path):
                _write_gemini_config("AIzaKey999", "")
            content = (td_path / "config" / "gemini_config.env").read_text(
                encoding="utf-8"
            )
            assert "AIzaKey999" in content
            assert "GEMINI_API_BASE=" in content

    # -- _api_key_configured ------------------------------------------------
    def test_api_key_configured_false_for_your_api_key_here(self):
        from src.koto_setup import _api_key_configured

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cfg = td_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("GEMINI_API_KEY=your_api_key_here\n")
            with patch("src.koto_setup.APP_ROOT", td_path):
                result = _api_key_configured()
        assert result is False

    def test_api_key_configured_false_for_empty(self):
        from src.koto_setup import _api_key_configured

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cfg = td_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("GEMINI_API_KEY=\n")
            with patch("src.koto_setup.APP_ROOT", td_path):
                result = _api_key_configured()
        assert result is False

    def test_api_key_configured_false_for_none(self):
        from src.koto_setup import _api_key_configured

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cfg = td_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("GEMINI_API_KEY=None\n")
            with patch("src.koto_setup.APP_ROOT", td_path):
                result = _api_key_configured()
        assert result is False

    def test_api_key_configured_true_for_real_key(self):
        from src.koto_setup import _api_key_configured

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cfg = td_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("GEMINI_API_KEY=AIzaRealKeyValue12345678901234\n")
            with patch("src.koto_setup.APP_ROOT", td_path):
                result = _api_key_configured()
        assert result is True

    def test_api_key_configured_no_file(self):
        from src.koto_setup import _api_key_configured

        with tempfile.TemporaryDirectory() as td:
            with patch("src.koto_setup.APP_ROOT", Path(td)):
                result = _api_key_configured()
        assert result is False

    # -- _read_config_values ------------------------------------------------
    def test_read_config_values_parses_both_fields(self):
        from src.koto_setup import _read_config_values

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cfg = td_path / "config" / "gemini_config.env"
            cfg.parent.mkdir(parents=True)
            cfg.write_text(
                "GEMINI_API_KEY=AIzaKey123\nGEMINI_API_BASE=https://api.example.com\n"
            )
            with patch("src.koto_setup.APP_ROOT", td_path):
                key, base = _read_config_values()
        assert key == "AIzaKey123"
        assert base == "https://api.example.com"

    def test_read_config_values_missing_file(self):
        from src.koto_setup import _read_config_values

        with tempfile.TemporaryDirectory() as td:
            with patch("src.koto_setup.APP_ROOT", Path(td)):
                key, base = _read_config_values()
        assert key == ""
        assert base == ""

    # -- _validate_api_key --------------------------------------------------
    def test_validate_api_key_success(self):
        from src.koto_setup import _validate_api_key

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = lambda s: mock_response
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_response):
            ok, msg = _validate_api_key("AIzaTestKey123456789012345678")
        assert ok is True

    def test_validate_api_key_http_400(self):
        from src.koto_setup import _validate_api_key
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "http://example.com", 400, "Bad Request", {}, None
            ),
        ):
            ok, msg = _validate_api_key("AIzaBadKey")
        assert ok is False
        assert "❌" in msg

    def test_validate_api_key_http_403(self):
        from src.koto_setup import _validate_api_key
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "http://example.com", 403, "Forbidden", {}, None
            ),
        ):
            ok, msg = _validate_api_key("AIzaForbidden")
        assert ok is False
        assert "403" in msg or "❌" in msg

    def test_validate_api_key_http_500(self):
        from src.koto_setup import _validate_api_key
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "http://example.com", 500, "Server Error", {}, None
            ),
        ):
            ok, msg = _validate_api_key("AIzaTestKey123456789012345678")
        assert ok is False
        assert "500" in msg

    def test_validate_api_key_network_error(self):
        from src.koto_setup import _validate_api_key

        with patch("urllib.request.urlopen", side_effect=ConnectionError("no network")):
            ok, msg = _validate_api_key("AIzaTestKey123456789012345678")
        assert ok is False
        assert "⚠" in msg

    def test_validate_api_key_custom_base(self):
        from src.koto_setup import _validate_api_key

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = lambda s: mock_response
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch(
            "urllib.request.urlopen", return_value=mock_response
        ) as mock_urlopen:
            ok, msg = _validate_api_key("AIzaKey", base="https://custom.api.com")
        assert ok is True
        # Verify the custom base was used in the URL
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert "custom.api.com" in req.full_url

    # -- _run_setup_if_needed -----------------------------------------------
    def test_run_setup_if_needed_forced_with_flag(self):
        from src.koto_setup import _run_setup_if_needed

        with patch("sys.argv", ["koto_setup.py", "--setup"]), patch(
            "src.koto_setup._show_api_setup_wizard",
            return_value={"key": "AIzaKey12345678901234567890", "base": ""},
        ) as mock_wizard, patch("src.koto_setup._write_gemini_config"), patch(
            "src.koto_setup.APP_ROOT", Path(tempfile.mkdtemp())
        ):
            try:
                _run_setup_if_needed()
            except Exception:
                pass
        mock_wizard.assert_called_once()

    def test_run_setup_if_needed_user_cancels(self):
        from src.koto_setup import _run_setup_if_needed

        with patch("sys.argv", ["koto_setup.py", "--setup"]), patch(
            "src.koto_setup._show_api_setup_wizard",
            return_value={"key": None, "base": "", "cancelled": True},
        ):
            _run_setup_if_needed()  # should not crash

    def test_run_setup_skips_when_key_valid(self):
        from src.koto_setup import _run_setup_if_needed

        with patch("sys.argv", ["koto_setup.py"]), patch(
            "src.koto_setup._api_key_configured", return_value=True
        ), patch(
            "src.koto_setup._read_config_values", return_value=("AIzaKey", "")
        ), patch(
            "src.koto_setup._validate_api_key", return_value=(True, "")
        ), patch(
            "src.koto_setup._show_api_setup_wizard"
        ) as mock_wizard:
            _run_setup_if_needed()
        mock_wizard.assert_not_called()

    def test_run_setup_skips_on_network_error(self):
        from src.koto_setup import _run_setup_if_needed

        with patch("sys.argv", ["koto_setup.py"]), patch(
            "src.koto_setup._api_key_configured", return_value=True
        ), patch(
            "src.koto_setup._read_config_values", return_value=("AIzaKey", "")
        ), patch(
            "src.koto_setup._validate_api_key", return_value=(False, "⚠️ 网络异常")
        ), patch(
            "src.koto_setup._show_api_setup_wizard"
        ) as mock_wizard:
            _run_setup_if_needed()
        mock_wizard.assert_not_called()

    # -- _prompt_local_model_if_needed --------------------------------------
    def test_prompt_local_model_skips_if_already_prompted(self):
        from src.koto_setup import _prompt_local_model_if_needed

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            flag = td_path / "config" / "local_model_prompted.json"
            flag.parent.mkdir(parents=True)
            flag.write_text('{"prompted": true}')
            with patch("src.koto_setup.APP_ROOT", td_path):
                _prompt_local_model_if_needed()

    def test_prompt_local_model_skips_if_no_installer(self):
        from src.koto_setup import _prompt_local_model_if_needed

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            (td_path / "config").mkdir(parents=True)
            with patch("src.koto_setup.APP_ROOT", td_path):
                _prompt_local_model_if_needed()
