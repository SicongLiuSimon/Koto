"""
Batch 10 – unit tests for 12 web modules at 0 % or low coverage.
Each class contains 5-8 focused tests covering __init__, key public methods,
and error paths.  All external deps are mocked.
"""
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, mock_open, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# 1. BatchFileProcessor  (web/batch_processor.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestBatchFileProcessor:
    """Tests for web.batch_processor.BatchFileProcessor"""

    def _make(self, **kw):
        from web.batch_processor import BatchFileProcessor
        return BatchFileProcessor(**kw)

    def test_init_default_workspace(self):
        proc = self._make()
        assert proc.workspace_dir.endswith("workspace")

    def test_init_custom_workspace(self, tmp_path):
        proc = self._make(workspace_dir=str(tmp_path))
        assert proc.workspace_dir == str(tmp_path)

    def test_batch_rename_missing_dir(self):
        proc = self._make()
        result = proc.batch_rename("/nonexistent_dir_xyz")
        assert result["success"] is False

    def test_batch_rename_dry_run(self, tmp_path):
        (tmp_path / "hello.txt").write_text("a")
        (tmp_path / "world.txt").write_text("b")
        proc = self._make()
        result = proc.batch_rename(str(tmp_path), prefix="PRE_", dry_run=True)
        assert result["success"] is True
        assert result["dry_run"] is True
        assert result["renamed_count"] == 2

    def test_batch_rename_with_regex(self, tmp_path):
        (tmp_path / "file_old.txt").write_text("x")
        proc = self._make()
        result = proc.batch_rename(
            str(tmp_path), pattern="old", replacement="new", dry_run=True
        )
        assert result["success"] is True
        assert any(r["new"] == "file_new.txt" for r in result["renamed"])

    def test_batch_rename_numbering(self, tmp_path):
        (tmp_path / "a.txt").write_text("")
        proc = self._make()
        result = proc.batch_rename(str(tmp_path), numbering=True, dry_run=True)
        assert result["renamed_count"] >= 1
        assert "_001" in result["renamed"][0]["new"]

    def test_batch_convert_missing_dir(self):
        proc = self._make()
        result = proc.batch_convert("/nonexistent_dir_xyz", ".txt", ".md")
        assert result["success"] is False

    def test_batch_convert_dry_run(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hello")
        proc = self._make()
        result = proc.batch_convert(str(tmp_path), ".txt", ".md", dry_run=True)
        assert result["success"] is True
        assert result["converted_count"] == 1

    def test_clean_duplicates_missing_dir(self):
        proc = self._make()
        result = proc.clean_duplicates("/nonexistent_dir_xyz")
        assert result["success"] is False

    def test_clean_duplicates_by_content(self, tmp_path):
        (tmp_path / "a.txt").write_text("same")
        (tmp_path / "b.txt").write_text("same")
        (tmp_path / "c.txt").write_text("diff")
        proc = self._make()
        result = proc.clean_duplicates(str(tmp_path), by_content=True, dry_run=True)
        assert result["success"] is True
        assert result["duplicates_found"] == 1

    def test_clean_text_content_missing_file(self):
        proc = self._make()
        result = proc.clean_text_content("/no/such/file.txt")
        assert result["success"] is False

    def test_clean_text_content_unsupported_ext(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_text("x")
        proc = self._make()
        result = proc.clean_text_content(str(f))
        assert result["success"] is False

    def test_clean_text_content_removes_blanks(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\n\n\n\nline2", encoding="utf-8")
        proc = self._make()
        result = proc.clean_text_content(str(f), dry_run=True)
        assert result["success"] is True
        assert result["lines_removed"] > 0


# ---------------------------------------------------------------------------
# 2. CodeGenerator  (web/code_generator.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestCodeGenerator:
    """Tests for web.code_generator.CodeGenerator"""

    def _make(self):
        from web.code_generator import CodeGenerator
        return CodeGenerator()

    def test_init_has_templates(self):
        gen = self._make()
        assert len(gen.templates) > 0
        assert "c_hello" in gen.templates

    def test_list_templates_all(self):
        gen = self._make()
        all_t = gen.list_templates()
        assert len(all_t) >= 3
        assert all("name" in t for t in all_t)

    def test_list_templates_by_language(self):
        gen = self._make()
        c_templates = gen.list_templates(language="c")
        assert len(c_templates) >= 1
        assert all(t["language"] == "c" for t in c_templates)

    def test_generate_known_template(self):
        gen = self._make()
        result = gen.generate("c_hello")
        assert result["success"] is True
        assert "Hello, World!" in result["code"]
        assert result["language"] == "c"

    def test_generate_unknown_template(self):
        gen = self._make()
        result = gen.generate("nonexistent_template")
        assert result["success"] is False

    def test_generate_with_output_path(self, tmp_path):
        gen = self._make()
        out = str(tmp_path / "out.c")
        result = gen.generate("c_hello", output_path=out)
        assert result["success"] is True
        assert os.path.exists(out)

    def test_generate_template_with_params(self):
        gen = self._make()
        result = gen.generate("c_file_io", filename="test.txt", content="data")
        assert result["success"] is True
        assert "test.txt" in result["code"]

    def test_generate_from_description_no_model(self):
        gen = self._make()
        result = gen.generate_from_description("sort a list", ai_model=None)
        assert result["success"] is False


# ---------------------------------------------------------------------------
# 3. CodeTemplate  (web/code_generator.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestCodeTemplate:
    """Tests for web.code_generator.CodeTemplate"""

    def test_render_substitutes(self):
        from web.code_generator import CodeTemplate
        t = CodeTemplate("test", "python", "Hello {name}!", "desc")
        assert t.render(name="World") == "Hello World!"

    def test_attributes(self):
        from web.code_generator import CodeTemplate
        t = CodeTemplate("n", "js", "tpl", "d")
        assert t.name == "n"
        assert t.language == "js"
        assert t.description == "d"


# ---------------------------------------------------------------------------
# 4. ClipboardOCRAssistant  (web/clipboard_ocr_assistant.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestClipboardOCRAssistant:
    """Tests for web.clipboard_ocr_assistant.ClipboardOCRAssistant"""

    @patch("web.clipboard_ocr_assistant.os.makedirs")
    @patch("web.clipboard_ocr_assistant.ClipboardOCRAssistant._init_ocr_engine", return_value=None)
    def _make(self, mock_engine, mock_mkdirs, **kw):
        from web.clipboard_ocr_assistant import ClipboardOCRAssistant
        return ClipboardOCRAssistant(**kw)

    def test_init_sets_output_dir(self):
        a = self._make()
        assert a.output_dir == "workspace/clipboard"

    def test_init_custom_dir(self):
        a = self._make(output_dir="/tmp/clip")
        assert a.output_dir == "/tmp/clip"

    def test_ocr_image_no_engine(self):
        a = self._make()
        a.ocr_engine = None
        result = a.ocr_image("fake.png")
        assert result["success"] is False
        assert "未初始化" in result["error"]

    def test_ocr_image_file_not_found(self):
        a = self._make()
        a.ocr_engine = "tesseract"
        result = a.ocr_image("/nonexistent/img.png")
        assert result["success"] is False

    @patch("web.clipboard_ocr_assistant.ImageGrab")
    def test_capture_screenshot_success(self, mock_grab):
        mock_img = MagicMock()
        mock_img.size = (1920, 1080)
        mock_grab.grab.return_value = mock_img
        a = self._make()
        result = a.capture_screenshot(save_image=False)
        assert result["success"] is True
        assert result["size"] == (1920, 1080)

    @patch("web.clipboard_ocr_assistant.ImageGrab")
    def test_capture_clipboard_no_image(self, mock_grab):
        mock_grab.grabclipboard.return_value = None
        a = self._make()
        result = a.capture_clipboard_image()
        assert result["success"] is False

    def test_auto_index_ocr_not_success(self):
        a = self._make()
        result = a.auto_index_to_knowledge_base({"ocr_success": False})
        assert result["success"] is False

    @patch("web.clipboard_ocr_assistant.ImageGrab")
    def test_capture_and_ocr_screenshot_fail(self, mock_grab):
        mock_grab.grab.side_effect = RuntimeError("no display")
        a = self._make()
        result = a.capture_and_ocr(source="screenshot")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# 5. file_fields_extractor  (web/file_fields_extractor.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestFileFieldsExtractor:
    """Tests for web.file_fields_extractor functions"""

    def test_normalize_date_standard(self):
        from web.file_fields_extractor import _normalize_date
        assert _normalize_date("2026-03-01") == "2026-03-01"

    def test_normalize_date_chinese(self):
        from web.file_fields_extractor import _normalize_date
        assert _normalize_date("2026年3月1日") == "2026-03-01"

    def test_normalize_date_empty(self):
        from web.file_fields_extractor import _normalize_date
        assert _normalize_date("") == ""

    def test_normalize_date_invalid_returns_stripped(self):
        from web.file_fields_extractor import _normalize_date
        assert _normalize_date("  unknown  ") == "unknown"

    @patch("web.file_fields_extractor._ollama_available", return_value=False)
    def test_extract_fields_ollama_unavailable(self, _):
        from web.file_fields_extractor import extract_fields
        assert extract_fields("test.docx", "some content") is None

    def test_extract_fields_empty_content(self):
        from web.file_fields_extractor import extract_fields
        assert extract_fields("test.docx", "") is None
        assert extract_fields("test.docx", "   ") is None

    def test_fields_to_markdown_basic(self):
        from web.file_fields_extractor import fields_to_markdown
        fields = {
            "summary": "A test summary",
            "parties": ["A Corp", "B Corp"],
            "amounts": [{"label": "总额", "value": "100万"}],
            "dates": [{"label": "签署日", "value": "2025-01-01"}],
            "contacts": [{"name": "张三", "phone": "138xxx", "email": ""}],
            "key_terms": ["条款A", "条款B"],
        }
        md = fields_to_markdown(fields, file_name="contract.docx")
        assert "contract.docx" in md
        assert "A Corp" in md
        assert "100万" in md
        assert "2025-01-01" in md
        assert "张三" in md

    def test_fields_to_markdown_empty(self):
        from web.file_fields_extractor import fields_to_markdown
        md = fields_to_markdown({})
        assert md == ""


# ---------------------------------------------------------------------------
# 6. FileParser  (web/file_parser.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestFileParser:
    """Tests for web.file_parser.FileParser"""

    def test_parse_file_not_found(self):
        from web.file_parser import FileParser
        result = FileParser.parse_file("/nonexistent_abc.txt")
        assert result["success"] is False

    def test_parse_file_unsupported_format(self, tmp_path):
        from web.file_parser import FileParser
        f = tmp_path / "test.xyz"
        f.write_text("data")
        result = FileParser.parse_file(str(f))
        assert result["success"] is False

    def test_parse_text_file(self, tmp_path):
        from web.file_parser import FileParser
        f = tmp_path / "readme.txt"
        f.write_text("Hello World", encoding="utf-8")
        result = FileParser.parse_file(str(f))
        assert result["success"] is True
        assert result["content"] == "Hello World"
        assert result["format"] == "txt"

    def test_parse_markdown_file(self, tmp_path):
        from web.file_parser import FileParser
        f = tmp_path / "doc.md"
        f.write_text("# Title\nBody", encoding="utf-8")
        result = FileParser.parse_file(str(f))
        assert result["success"] is True
        assert "# Title" in result["content"]

    def test_parse_file_too_large(self, tmp_path):
        from web.file_parser import FileParser
        f = tmp_path / "big.txt"
        f.write_text("x")
        with patch("os.path.getsize", return_value=60 * 1024 * 1024):
            result = FileParser.parse_file(str(f))
        assert result["success"] is False

    def test_batch_parse(self, tmp_path):
        from web.file_parser import FileParser
        f1 = tmp_path / "a.txt"
        f1.write_text("aaa", encoding="utf-8")
        f2 = tmp_path / "b.txt"
        f2.write_text("bbb", encoding="utf-8")
        results = FileParser.batch_parse([str(f1), str(f2)])
        assert len(results) == 2
        assert all(r["success"] for r in results)

    def test_merge_contents(self):
        from web.file_parser import FileParser
        results = [
            {"success": True, "filename": "a.txt", "format": "txt", "content": "AAA"},
            {"success": False, "error": "fail"},
        ]
        merged = FileParser.merge_contents(results)
        assert "a.txt" in merged
        assert "AAA" in merged

    def test_sanitize_file_path_relative(self):
        from web.file_parser import FileParser
        result = FileParser.sanitize_file_path("web/file_parser.py")
        # Should return an absolute path or None depending on safety check
        assert result is None or os.path.isabs(result)


# ---------------------------------------------------------------------------
# 7. file_qa  (web/file_qa.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestFileQA:
    """Tests for web.file_qa module functions"""

    @patch("web.file_qa._ollama_available", return_value=False)
    def test_answer_question_ollama_unavailable(self, _):
        from web.file_qa import answer_file_question
        result = answer_file_question("What is in these files?")
        assert result["success"] is False
        assert "Ollama" in result["error"]

    @patch("web.file_qa._ollama_available", return_value=True)
    def test_answer_question_no_files_found(self, _):
        from web.file_qa import answer_file_question
        result = answer_file_question("question", file_paths=[])
        assert result["success"] is False

    @patch("web.file_qa._ollama_available", return_value=True)
    @patch("web.file_qa._extract_content_local", return_value="some content")
    def test_answer_question_with_files(self, mock_extract, mock_oll, tmp_path):
        from web.file_qa import answer_file_question
        f = tmp_path / "test.txt"
        f.write_text("hello")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "The answer is 42"}
        with patch("requests.post", return_value=mock_resp):
            result = answer_file_question("what?", file_paths=[str(f)])
        assert result["success"] is True
        assert "42" in result["answer"]

    @patch("web.file_qa._ollama_available", return_value=True)
    @patch("web.file_qa._extract_content_local", return_value="")
    def test_answer_question_empty_content(self, mock_extract, mock_oll, tmp_path):
        from web.file_qa import answer_file_question
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = answer_file_question("what?", file_paths=[str(f)])
        assert result["success"] is False

    @patch("web.file_qa._ollama_available", return_value=False)
    def test_filter_files_ollama_unavailable(self, _):
        from web.file_qa import filter_files_by_criterion
        result = filter_files_by_criterion("合同", "/tmp")
        assert result["success"] is False

    def test_search_files_in_dirs_nonexistent(self):
        from web.file_qa import _search_files_in_dirs
        result = _search_files_in_dirs(["test"], ["/nonexistent_xyz"])
        assert result == []


# ---------------------------------------------------------------------------
# 8. FileWatcher  (web/file_watcher.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestFileWatcher:
    """Tests for web.file_watcher.FileWatcher"""

    def _make(self):
        from web.file_watcher import FileWatcher
        return FileWatcher()

    def test_init(self):
        fw = self._make()
        assert fw._watches == {}
        assert fw._analyzer is None

    def test_configure(self):
        fw = self._make()
        analyzer = MagicMock()
        organizer = MagicMock()
        fw.configure(analyzer, organizer, "/root")
        assert fw._analyzer is analyzer
        assert fw._organizer is organizer
        assert fw._organize_root == "/root"

    @patch("web.file_watcher._WATCHDOG_OK", False)
    def test_start_watch_no_watchdog(self):
        fw = self._make()
        result = fw.start_watch("/some/dir")
        assert result["success"] is False
        assert "watchdog" in result["error"]

    def test_stop_watch_not_watching(self):
        fw = self._make()
        result = fw.stop_watch("/not/watched")
        assert result["success"] is False

    def test_stop_all_empty(self):
        fw = self._make()
        fw.stop_all()  # should not raise

    def test_list_watches_empty(self):
        fw = self._make()
        assert fw.list_watches() == []

    def test_handle_new_file_no_configure(self):
        fw = self._make()
        fw._handle_new_file("/some/file.txt")  # should not raise

    def test_get_file_watcher_singleton(self):
        import web.file_watcher as mod
        old = mod._watcher_instance
        mod._watcher_instance = None
        try:
            w1 = mod.get_file_watcher()
            w2 = mod.get_file_watcher()
            assert w1 is w2
        finally:
            mod._watcher_instance = old


# ---------------------------------------------------------------------------
# 9. CatalogEventHandler  (web/file_watcher.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestCatalogEventHandler:
    """Tests for web.file_watcher._CatalogEventHandler"""

    def _make(self, callback=None):
        from web.file_watcher import _CatalogEventHandler
        cb = callback or MagicMock()
        return _CatalogEventHandler("/tmp/watch", cb), cb

    def test_on_created_ignores_directories(self):
        handler, cb = self._make()
        event = MagicMock()
        event.is_directory = True
        handler.on_created(event)
        cb.assert_not_called()

    def test_on_created_ignores_unsupported_ext(self):
        handler, cb = self._make()
        event = MagicMock()
        event.is_directory = False
        event.src_path = "/tmp/watch/test.exe"
        handler.on_created(event)
        cb.assert_not_called()

    def test_on_created_ignores_temp_files(self):
        handler, cb = self._make()
        event = MagicMock()
        event.is_directory = False
        event.src_path = "/tmp/watch/~$temp.docx"
        handler.on_created(event)
        cb.assert_not_called()

    def test_on_created_schedules_callback(self):
        handler, cb = self._make()
        event = MagicMock()
        event.is_directory = False
        event.src_path = "/tmp/watch/report.pdf"
        handler.on_created(event)
        assert len(handler._pending) == 1

    def test_process_calls_callback(self):
        cb = MagicMock()
        handler, _ = self._make(callback=cb)
        handler._pending["test.txt"] = MagicMock()
        handler._process("test.txt")
        cb.assert_called_once_with("test.txt")


# ---------------------------------------------------------------------------
# 10. FileEditor  (web/file_editor.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestFileEditor:
    """Tests for web.file_editor.FileEditor"""

    def _make(self, tmp_path, backup=False):
        from web.file_editor import FileEditor
        return FileEditor(workspace_dir=str(tmp_path), backup_enabled=backup)

    def test_init_defaults(self, tmp_path):
        editor = self._make(tmp_path)
        assert editor.workspace_dir == Path(str(tmp_path))

    def test_read_file_not_found(self, tmp_path):
        editor = self._make(tmp_path)
        result = editor.read_file(str(tmp_path / "nope.txt"))
        assert result["success"] is False

    def test_read_and_write_file(self, tmp_path):
        editor = self._make(tmp_path, backup=False)
        f = tmp_path / "test.txt"
        f.write_text("hello", encoding="utf-8")
        read = editor.read_file(str(f))
        assert read["success"] is True
        assert read["content"] == "hello"

        w = editor.write_file(str(f), "world", create_backup=False)
        assert w["success"] is True
        assert f.read_text(encoding="utf-8") == "world"

    def test_replace_text(self, tmp_path):
        editor = self._make(tmp_path, backup=False)
        f = tmp_path / "test.txt"
        f.write_text("foo bar foo", encoding="utf-8")
        result = editor.replace_text(str(f), "foo", "baz")
        assert result["success"] is True
        assert result["replacements"] == 2

    def test_replace_text_not_found(self, tmp_path):
        editor = self._make(tmp_path, backup=False)
        f = tmp_path / "test.txt"
        f.write_text("hello", encoding="utf-8")
        result = editor.replace_text(str(f), "xyz", "abc")
        assert result["success"] is False
        assert result["replacements"] == 0

    def test_insert_line(self, tmp_path):
        editor = self._make(tmp_path, backup=False)
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3", encoding="utf-8")
        result = editor.insert_line(str(f), 2, "inserted", mode="after")
        assert result["success"] is True
        content = f.read_text(encoding="utf-8")
        assert "inserted" in content

    def test_delete_lines(self, tmp_path):
        editor = self._make(tmp_path, backup=False)
        f = tmp_path / "test.txt"
        f.write_text("l1\nl2\nl3\nl4", encoding="utf-8")
        result = editor.delete_lines(str(f), 2, 3)
        assert result["success"] is True
        content = f.read_text(encoding="utf-8")
        assert "l2" not in content
        assert "l3" not in content

    def test_smart_edit_replace(self, tmp_path):
        editor = self._make(tmp_path, backup=False)
        f = tmp_path / "test.txt"
        f.write_text("TODO: fix bug", encoding="utf-8")
        result = editor.smart_edit(str(f), "把 'TODO' 改成 'DONE'")
        assert result["success"] is True
        assert result["operation"] == "replace"

    def test_smart_edit_unknown_instruction(self, tmp_path):
        editor = self._make(tmp_path, backup=False)
        f = tmp_path / "test.txt"
        f.write_text("data", encoding="utf-8")
        result = editor.smart_edit(str(f), "do something weird and undefined")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# 11. IntelligentDocumentAnalyzer  (web/intelligent_document_analyzer.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestIntelligentDocumentAnalyzer:
    """Tests for web.intelligent_document_analyzer.IntelligentDocumentAnalyzer"""

    def _make(self):
        with patch.dict("sys.modules", {"docx": MagicMock(), "docx.shared": MagicMock()}):
            from web.intelligent_document_analyzer import IntelligentDocumentAnalyzer
            return IntelligentDocumentAnalyzer(llm_client=MagicMock())

    def test_init(self):
        analyzer = self._make()
        assert analyzer.llm_client is not None

    def test_detect_academic_doc(self):
        analyzer = self._make()
        content = "摘要 引言 结论 参考文献 关键词"
        assert analyzer._detect_document_type(content) == "academic"

    def test_detect_report_doc(self):
        analyzer = self._make()
        content = "报告 分析 结论 总结"
        assert analyzer._detect_document_type(content) == "report"

    def test_detect_article_doc(self):
        analyzer = self._make()
        content = "something generic text here"
        assert analyzer._detect_document_type(content) == "article"

    def test_analyze_request_write_abstract(self):
        analyzer = self._make()
        doc_structure = {
            "paragraphs": [
                {"text": "摘要 引言 结论 参考文献 关键词", "type": "body"}
            ]
        }
        result = analyzer.analyze_request("请帮我写摘要", doc_structure)
        assert len(result["tasks"]) >= 1
        assert result["tasks"][0]["type"] == "write_abstract"

    def test_analyze_request_default_task(self):
        analyzer = self._make()
        doc_structure = {"paragraphs": [{"text": "hello", "type": "body"}]}
        result = analyzer.analyze_request("xyzzy unrelated", doc_structure)
        assert result["tasks"][0]["type"] == "analysis"

    def test_get_structure_overview(self):
        analyzer = self._make()
        doc = {
            "paragraphs": [
                {"text": "Chapter 1", "type": "heading", "level": 1},
                {"text": "Body text", "type": "body"},
            ]
        }
        overview = analyzer._get_structure_overview(doc)
        assert "Chapter 1" in overview

    def test_generate_specialized_prompt_abstract(self):
        analyzer = self._make()
        task = {"type": "write_abstract"}
        doc = {"paragraphs": [{"text": "content here", "type": "body"}]}
        prompt = analyzer.generate_specialized_prompt(task, doc, "写摘要")
        assert "摘要" in prompt

    def test_identify_target_sections_abstract(self):
        analyzer = self._make()
        doc = {
            "paragraphs": [
                {"text": "Title", "type": "heading", "level": 1},
                {"text": "摘要", "type": "heading", "level": 1},
                {"text": "Body", "type": "body"},
            ]
        }
        sections = analyzer._identify_target_sections("write_abstract", doc)
        assert len(sections) >= 1


# ---------------------------------------------------------------------------
# 12. docx_translator_module  (web/docx_translator_module.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestDocxTranslatorModule:
    """Tests for web.docx_translator_module functions"""

    def test_detect_target_language_english(self):
        from web.docx_translator_module import detect_target_language
        assert detect_target_language("translate to english") == "English"

    def test_detect_target_language_japanese(self):
        from web.docx_translator_module import detect_target_language
        assert detect_target_language("翻译成日语") == "Japanese"

    def test_detect_target_language_default(self):
        from web.docx_translator_module import detect_target_language
        assert detect_target_language("random text") == "English"

    def test_lang_map_coverage(self):
        from web.docx_translator_module import LANG_MAP
        assert "en" in LANG_MAP
        assert "ja" in LANG_MAP
        assert "zh-cn" in LANG_MAP

    def test_lang_suffix_coverage(self):
        from web.docx_translator_module import LANG_SUFFIX
        assert LANG_SUFFIX["English"] == "en"
        assert LANG_SUFFIX["Japanese"] == "ja"

    def test_translate_docx_streaming_no_docx(self):
        from web.docx_translator_module import translate_docx_streaming
        with patch.dict("sys.modules", {"docx": None}):
            # Force re-import failure inside generator by patching builtins
            import builtins
            real_import = builtins.__import__
            def fake_import(name, *a, **kw):
                if name == "docx":
                    raise ImportError("no docx")
                return real_import(name, *a, **kw)
            with patch("builtins.__import__", side_effect=fake_import):
                events = list(translate_docx_streaming("fake.docx", "English", MagicMock()))
        assert events[0]["stage"] == "error"

    def test_translate_batch_llm_empty(self):
        from web.docx_translator_module import _translate_batch_llm
        result = _translate_batch_llm([], "English", MagicMock())
        assert result == []

    @patch("web.docx_translator_module._translate_one_by_one")
    def test_translate_batch_llm_mismatch_fallback(self, mock_fallback):
        from web.docx_translator_module import _translate_batch_llm
        mock_fallback.return_value = ["翻译A", "翻译B"]
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "only one segment"
        mock_client.models.generate_content.return_value = mock_resp
        with patch.dict("sys.modules", {"google.genai": MagicMock(), "google.genai.types": MagicMock()}):
            _translate_batch_llm(["text1", "text2"], "English", mock_client)
        mock_fallback.assert_called_once()


# ---------------------------------------------------------------------------
# 13. VoiceResult dataclass  (web/voice_fast.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestVoiceResult:
    """Tests for web.voice_fast.VoiceResult"""

    def test_to_dict(self):
        from web.voice_fast import VoiceResult
        vr = VoiceResult(success=True, text="hello", engine="vosk", confidence=0.95)
        d = vr.to_dict()
        assert d["success"] is True
        assert d["text"] == "hello"
        assert d["engine"] == "vosk"
        assert d["confidence"] == 0.95

    def test_defaults(self):
        from web.voice_fast import VoiceResult
        vr = VoiceResult(success=False)
        assert vr.text == ""
        assert vr.engine == ""
        assert vr.confidence == 0.0


# ---------------------------------------------------------------------------
# 14. _clean_chinese_text  (web/voice_fast.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestCleanChineseText:
    """Tests for web.voice_fast._clean_chinese_text"""

    def test_removes_spaces_between_chinese(self):
        from web.voice_fast import _clean_chinese_text
        assert _clean_chinese_text("你 好 世 界") == "你好世界"

    def test_empty_string(self):
        from web.voice_fast import _clean_chinese_text
        assert _clean_chinese_text("") == ""

    def test_english_unchanged(self):
        from web.voice_fast import _clean_chinese_text
        assert _clean_chinese_text("hello world") == "hello world"

    def test_mixed_text(self):
        from web.voice_fast import _clean_chinese_text
        result = _clean_chinese_text("你好 world 世界")
        assert "你好" in result
        assert "world" in result


# ---------------------------------------------------------------------------
# 15. FastVoiceRecognizer init  (web/voice_fast.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestFastVoiceRecognizer:
    """Tests for web.voice_fast.FastVoiceRecognizer"""

    @patch("web.voice_fast.FastVoiceRecognizer._start_background_init")
    @patch("web.voice_fast.FastVoiceRecognizer._detect_engines")
    def test_init(self, mock_detect, mock_bg):
        from web.voice_fast import FastVoiceRecognizer
        rec = FastVoiceRecognizer()
        mock_detect.assert_called_once()
        assert rec.vosk_model is None

    @patch("web.voice_fast.FastVoiceRecognizer._start_background_init")
    @patch("web.voice_fast.FastVoiceRecognizer._detect_engines")
    def test_available_engines_starts_empty(self, mock_detect, mock_bg):
        from web.voice_fast import FastVoiceRecognizer
        rec = FastVoiceRecognizer()
        assert rec.available_engines == []

    @patch("web.voice_fast.FastVoiceRecognizer._start_background_init")
    @patch("web.voice_fast.FastVoiceRecognizer._detect_engines")
    def test_primary_engine_default_none(self, mock_detect, mock_bg):
        from web.voice_fast import FastVoiceRecognizer
        rec = FastVoiceRecognizer()
        assert rec.primary_engine is None


# ---------------------------------------------------------------------------
# 16. RecognitionResult  (web/voice_input.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestRecognitionResult:
    """Tests for web.voice_input.RecognitionResult"""

    def test_to_dict(self):
        from web.voice_input import RecognitionResult
        rr = RecognitionResult(success=True, text="test", engine="google", confidence=0.8)
        d = rr.to_dict()
        assert d["success"] is True
        assert d["text"] == "test"
        assert d["confidence"] == 0.8

    def test_defaults(self):
        from web.voice_input import RecognitionResult
        rr = RecognitionResult(success=False)
        assert rr.text == ""
        assert rr.audio_file is None


# ---------------------------------------------------------------------------
# 17. VoiceInputEngine  (web/voice_input.py)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestVoiceInputEngine:
    """Tests for web.voice_input.VoiceInputEngine"""

    @patch("web.voice_input.VoiceInputEngine._detect_engines")
    def _make(self, mock_detect):
        from web.voice_input import VoiceInputEngine, EngineType
        engine = VoiceInputEngine()
        engine.available_engines = [EngineType.OFFLINE]
        engine.primary_engine = EngineType.OFFLINE
        return engine

    def test_init(self):
        eng = self._make()
        assert eng.vosk_model is None

    def test_get_available_engines(self):
        eng = self._make()
        result = eng.get_available_engines()
        assert result["success"] is True
        assert len(result["engines"]) >= 1

    def test_engine_type_enum(self):
        from web.voice_input import EngineType
        assert EngineType.VOSK_LOCAL.value == "vosk"
        assert EngineType.OFFLINE.value == "offline"

    def test_get_engine_name(self):
        from web.voice_input import EngineType
        eng = self._make()
        name = eng._get_engine_name(EngineType.VOSK_LOCAL)
        assert "Vosk" in name

    def test_get_engine_description(self):
        from web.voice_input import EngineType
        eng = self._make()
        desc = eng._get_engine_description(EngineType.OFFLINE)
        assert "录音" in desc

    def test_clean_chinese_text(self):
        eng = self._make()
        result = eng._clean_chinese_text("你 好")
        assert result == "你好"

    def test_clean_chinese_text_empty(self):
        eng = self._make()
        result = eng._clean_chinese_text("")
        assert result == ""
