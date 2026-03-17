#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Unit tests for web modules batch 6:
  - DocumentAnnotator
  - DocumentComparator
  - DocumentReader
  - DocumentValidator
  - EmailManager / EmailAccount / Email
  - FileOrganizer
  - FileScanner
  - NotificationManager
  - OperationHistory
  - QuickNoteManager
"""

import pytest
from unittest.mock import patch, MagicMock, Mock, mock_open, PropertyMock
import os
import json
import tempfile
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# DocumentAnnotator
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDocumentAnnotator:

    def test_init_defaults(self):
        from web.document_annotator import DocumentAnnotator

        ann = DocumentAnnotator()
        assert ann.min_similarity == 0.8
        assert ann.annotation_mode == "comment"

    def test_init_custom(self):
        from web.document_annotator import DocumentAnnotator

        ann = DocumentAnnotator(min_similarity=0.5, annotation_mode="highlight")
        assert ann.min_similarity == 0.5
        assert ann.annotation_mode == "highlight"

    def test_prepare_document_file_not_found(self):
        from web.document_annotator import DocumentAnnotator

        ann = DocumentAnnotator()
        with pytest.raises(FileNotFoundError):
            ann.prepare_document("/no/such/file.docx")

    def test_prepare_document_creates_copy(self, tmp_path):
        from web.document_annotator import DocumentAnnotator

        src = tmp_path / "test.docx"
        src.write_bytes(b"fake-docx-content")
        ann = DocumentAnnotator()
        orig, revised = ann.prepare_document(str(src))
        assert orig == str(src)
        assert revised.endswith("_revised.docx")
        assert os.path.exists(revised)

    def test_extract_text_from_word_exception(self):
        from web.document_annotator import DocumentAnnotator
        import sys

        mock_docx = MagicMock()
        mock_docx.Document.side_effect = Exception("read error")
        with patch.dict(sys.modules, {"docx": mock_docx}):
            result = DocumentAnnotator.extract_text_from_word("some.docx")
        assert result["success"] is False
        assert "error" in result

    def test_locate_text_in_paragraphs_empty_target(self):
        from web.document_annotator import DocumentAnnotator

        ann = DocumentAnnotator()
        assert ann.locate_text_in_paragraphs([], "") is None
        assert ann.locate_text_in_paragraphs([], "   ") is None

    def test_locate_text_exact_match(self):
        from web.document_annotator import DocumentAnnotator

        ann = DocumentAnnotator()
        paras = [{"index": 0, "text": "Hello World", "para_obj": None}]
        result = ann.locate_text_in_paragraphs(paras, "Hello")
        assert result is not None
        assert result["found"] is True
        assert result["match_type"] == "exact"
        assert result["position"] == 0

    def test_locate_text_fuzzy_match(self):
        from web.document_annotator import DocumentAnnotator

        ann = DocumentAnnotator(min_similarity=0.5)
        paras = [{"index": 0, "text": "Hello World Foo Bar", "para_obj": None}]
        # A string that's similar but not identical
        result = ann.locate_text_in_paragraphs(paras, "Hello World Foo Baz")
        assert result is not None
        assert result["found"] is True
        assert result["match_type"] == "fuzzy"

    def test_locate_text_no_match(self):
        from web.document_annotator import DocumentAnnotator

        ann = DocumentAnnotator(min_similarity=0.99)
        paras = [{"index": 0, "text": "AAAA", "para_obj": None}]
        result = ann.locate_text_in_paragraphs(
            paras, "ZZZZZZZZZZZZZZ completely different"
        )
        assert result is None


# ---------------------------------------------------------------------------
# DocumentComparator
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDocumentComparator:

    def test_init(self):
        from web.document_comparator import DocumentComparator

        comp = DocumentComparator()
        assert ".txt" in comp.supported_formats

    def test_compare_documents_file_not_found(self):
        from web.document_comparator import DocumentComparator

        comp = DocumentComparator()
        result = comp.compare_documents("/no/a.txt", "/no/b.txt")
        assert result["success"] is False
        assert "不存在" in result["error"]

    def test_compare_documents_success(self, tmp_path):
        from web.document_comparator import DocumentComparator

        fa = tmp_path / "a.txt"
        fb = tmp_path / "b.txt"
        fa.write_text("line1\nline2\n", encoding="utf-8")
        fb.write_text("line1\nline2 modified\nline3\n", encoding="utf-8")
        comp = DocumentComparator()
        result = comp.compare_documents(str(fa), str(fb))
        assert result["success"] is True
        assert "changes" in result
        assert "summary" in result

    def test_compare_documents_html_format(self, tmp_path):
        from web.document_comparator import DocumentComparator

        fa = tmp_path / "a.txt"
        fb = tmp_path / "b.txt"
        fa.write_text("hello", encoding="utf-8")
        fb.write_text("world", encoding="utf-8")
        comp = DocumentComparator()
        result = comp.compare_documents(str(fa), str(fb), output_format="html")
        assert result["success"] is True
        assert "<" in result["diff"]  # HTML tags

    def test_compare_documents_text_format(self, tmp_path):
        from web.document_comparator import DocumentComparator

        fa = tmp_path / "a.txt"
        fb = tmp_path / "b.txt"
        fa.write_text("aaa", encoding="utf-8")
        fb.write_text("bbb", encoding="utf-8")
        comp = DocumentComparator()
        result = comp.compare_documents(str(fa), str(fb), output_format="text")
        assert result["success"] is True

    def test_compare_versions_less_than_two(self):
        from web.document_comparator import DocumentComparator

        comp = DocumentComparator()
        result = comp.compare_versions(["only_one.txt"])
        assert result["success"] is False

    def test_compare_versions_success(self, tmp_path):
        from web.document_comparator import DocumentComparator

        files = []
        for i in range(3):
            f = tmp_path / f"v{i}.txt"
            f.write_text(f"version {i}\ncommon line\n", encoding="utf-8")
            files.append(str(f))
        comp = DocumentComparator()
        result = comp.compare_versions(files)
        assert result["success"] is True
        assert result["total_versions"] == 3

    def test_generate_change_log(self, tmp_path):
        from web.document_comparator import DocumentComparator

        comp = DocumentComparator()
        comparisons = [
            {
                "file_a": "a.txt",
                "file_b": "b.txt",
                "summary": "test summary",
                "changes": {
                    "additions": {"count": 2, "lines": ["line1", "line2"]},
                    "deletions": {"count": 1, "lines": ["old"]},
                    "modifications": {"count": 0, "details": []},
                },
            }
        ]
        out = tmp_path / "changelog.md"
        comp.generate_change_log(comparisons, str(out))
        assert out.exists()

    def test_generate_summary_levels(self):
        from web.document_comparator import DocumentComparator

        comp = DocumentComparator()
        # Very high similarity
        s = comp._generate_summary(
            {
                "similarity": 96,
                "additions": {"count": 0},
                "deletions": {"count": 0},
                "modifications": {"count": 0},
                "char_diff": 0,
                "line_diff": 0,
            }
        )
        assert "很小" in s
        # Low similarity
        s = comp._generate_summary(
            {
                "similarity": 40,
                "additions": {"count": 1},
                "deletions": {"count": 2},
                "modifications": {"count": 3},
                "char_diff": -5,
                "line_diff": 0,
            }
        )
        assert "改写" in s


# ---------------------------------------------------------------------------
# DocumentReader
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDocumentReader:

    def test_read_document_unsupported_format(self):
        from web.document_reader import DocumentReader

        result = DocumentReader.read_document("test.xyz")
        assert result["success"] is False

    def test_read_document_dispatches_pptx(self):
        from web.document_reader import DocumentReader

        with patch.object(
            DocumentReader, "read_ppt", return_value={"success": True}
        ) as m:
            result = DocumentReader.read_document("slides.pptx")
            m.assert_called_once_with("slides.pptx")
            assert result["success"] is True

    def test_read_document_dispatches_docx(self):
        from web.document_reader import DocumentReader

        with patch.object(
            DocumentReader, "read_word", return_value={"success": True}
        ) as m:
            DocumentReader.read_document("doc.docx")
            m.assert_called_once()

    def test_read_document_dispatches_xlsx(self):
        from web.document_reader import DocumentReader

        with patch.object(
            DocumentReader, "read_excel", return_value={"success": True}
        ) as m:
            DocumentReader.read_document("book.xlsx")
            m.assert_called_once()

    def test_read_ppt_import_error(self):
        from web.document_reader import DocumentReader
        import sys

        with patch.dict(sys.modules, {"pptx": None}):
            result = DocumentReader.read_ppt("test.pptx")
        assert result["success"] is False
        assert "python-pptx" in result["error"]

    def test_read_excel_import_error(self):
        from web.document_reader import DocumentReader
        import sys

        with patch.dict(sys.modules, {"openpyxl": None}):
            result = DocumentReader.read_excel("test.xlsx")
        assert result["success"] is False
        assert "openpyxl" in result["error"]

    def test_format_for_ai_error_data(self):
        from web.document_reader import DocumentReader

        result = DocumentReader.format_for_ai({"success": False, "error": "broken"})
        assert "broken" in result

    def test_format_for_ai_ppt(self):
        from web.document_reader import DocumentReader

        data = {
            "success": True,
            "type": "ppt",
            "file_name": "test.pptx",
            "slide_count": 1,
            "slides": [
                {"index": 0, "title": "Slide 1", "content": ["bullet"], "notes": "note"}
            ],
        }
        text = DocumentReader.format_for_ai(data)
        assert "Slide 1" in text

    def test_format_for_ai_excel(self):
        from web.document_reader import DocumentReader

        data = {
            "success": True,
            "type": "excel",
            "file_name": "test.xlsx",
            "sheet_count": 1,
            "sheets": [
                {"name": "Sheet1", "rows": [["A", "B"]], "row_count": 1, "col_count": 2}
            ],
        }
        text = DocumentReader.format_for_ai(data)
        assert "Sheet1" in text

    def test_extract_text_from_doc_data_empty(self):
        from web.document_reader import DocumentReader

        assert DocumentReader._extract_text_from_doc_data(None) == ""
        assert DocumentReader._extract_text_from_doc_data({"success": False}) == ""


# ---------------------------------------------------------------------------
# DocumentValidator
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDocumentValidator:

    def test_validate_empty_original(self):
        from web.document_validator import DocumentValidator

        result = DocumentValidator.validate_modifications(
            "some text", [{"original": "", "modified": "x"}]
        )
        assert result["valid_count"] == 0
        assert len(result["issues"]) > 0

    def test_validate_original_not_found(self):
        from web.document_validator import DocumentValidator

        result = DocumentValidator.validate_modifications(
            "Hello World",
            [{"original": "ZZZZZZZZZZZZZZ not here at all", "modified": "fixed"}],
        )
        assert result["risk_level"] == "HIGH"

    def test_validate_successful_modification(self):
        from web.document_validator import DocumentValidator

        result = DocumentValidator.validate_modifications(
            "The quick brown fox jumps over the lazy dog",
            [{"original": "quick brown fox", "modified": "slow red fox"}],
        )
        assert result["valid_count"] == 1
        assert result["success"] is True

    def test_validate_no_change(self):
        from web.document_validator import DocumentValidator

        result = DocumentValidator.validate_modifications(
            "same text", [{"original": "same text", "modified": "same text"}]
        )
        # original == modified => issue
        assert result["valid_count"] == 0
        assert any("无变化" in i for i in result["issues"])

    def test_validate_modified_is_none(self):
        from web.document_validator import DocumentValidator

        result = DocumentValidator.validate_modifications(
            "some content", [{"original": "some", "modified": None}]
        )
        assert any("为空" in i for i in result["issues"])

    def test_validate_multiple_occurrences_short(self):
        from web.document_validator import DocumentValidator

        result = DocumentValidator.validate_modifications(
            "ab ab ab", [{"original": "ab", "modified": "cd"}]
        )
        assert result["risk_level"] in ("MEDIUM", "HIGH")

    def test_validate_fuzzy_whitespace_match(self):
        from web.document_validator import DocumentValidator

        result = DocumentValidator.validate_modifications(
            "Hello   World", [{"original": "Hello World", "modified": "Hi World"}]
        )
        # Fuzzy whitespace match should still count as valid
        assert result["valid_count"] >= 1

    def test_verify_track_changes_integrity(self):
        from web.document_validator import DocumentValidator
        import sys

        mock_docx = MagicMock()
        mock_para = MagicMock()
        mock_para._element.xml = "<w:ins><w:t></w:t></w:ins>"
        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para]
        mock_docx.Document.return_value = mock_doc
        with patch.dict(sys.modules, {"docx": mock_docx}):
            issues = DocumentValidator.verify_track_changes_integrity("test.docx")
        assert any("空的内容插入标记" in i for i in issues)


# ---------------------------------------------------------------------------
# EmailManager / EmailAccount / Email
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmailManager:

    def test_email_account_init(self):
        from web.email_manager import EmailAccount

        acc = EmailAccount("test@example.com", "pass", "smtp.example.com")
        assert acc.email_address == "test@example.com"
        assert acc.imap_server == "imap.example.com"
        assert acc.smtp_port == 587

    def test_email_object_init(self):
        from web.email_manager import Email

        e = Email("from@x.com", "to@x.com", "Sub", "Body")
        assert e.to_addrs == ["to@x.com"]
        assert e.cc_addrs == []
        assert e.attachments == []
        assert e.html is False

    def test_email_object_list_to(self):
        from web.email_manager import Email

        e = Email("f@x.com", ["a@x.com", "b@x.com"], "S", "B")
        assert len(e.to_addrs) == 2

    @patch("web.email_manager.os.path.exists", return_value=False)
    @patch("web.email_manager.os.makedirs")
    def test_email_manager_init_no_config(self, mock_mkdirs, mock_exists):
        from web.email_manager import EmailManager

        mgr = EmailManager()
        assert mgr.accounts == {}
        assert mgr.default_account is None

    @patch("web.email_manager.os.path.exists", return_value=False)
    @patch("web.email_manager.os.makedirs")
    def test_add_account(self, mock_mkdirs, mock_exists):
        from web.email_manager import EmailManager

        mgr = EmailManager()
        with patch.object(mgr, "_save_accounts"):
            result = mgr.add_account(
                "user@x.com", "pw", "smtp.x.com", set_as_default=True
            )
        assert result is True
        assert mgr.default_account == "user@x.com"

    @patch("web.email_manager.os.path.exists", return_value=False)
    @patch("web.email_manager.os.makedirs")
    def test_send_email_no_account(self, mock_mkdirs, mock_exists):
        from web.email_manager import EmailManager

        mgr = EmailManager()
        result = mgr.send_email(["to@x.com"], "Hi", "Body")
        assert result is False

    def test_decode_header_empty(self):
        from web.email_manager import EmailManager

        with patch("web.email_manager.os.path.exists", return_value=False), patch(
            "web.email_manager.os.makedirs"
        ):
            mgr = EmailManager()
        assert mgr._decode_header(None) == ""
        assert mgr._decode_header("") == ""

    def test_get_email_body_non_multipart(self):
        from web.email_manager import EmailManager

        with patch("web.email_manager.os.path.exists", return_value=False), patch(
            "web.email_manager.os.makedirs"
        ):
            mgr = EmailManager()
        msg = MagicMock()
        msg.is_multipart.return_value = False
        msg.get_payload.return_value = b"Hello"
        body = mgr._get_email_body(msg)
        assert "Hello" in body


# ---------------------------------------------------------------------------
# FileOrganizer
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFileOrganizer:

    def test_init_creates_dirs(self, tmp_path):
        from web.file_organizer import FileOrganizer

        root = tmp_path / "organize"
        org = FileOrganizer(organize_root=str(root))
        assert root.exists()
        assert (root / "index.json").exists()

    def test_sanitize_path(self, tmp_path):
        from web.file_organizer import FileOrganizer

        org = FileOrganizer(organize_root=str(tmp_path / "org"))
        assert org._sanitize_path("a\\b:c*d") == "a/b_c_d"
        assert "  " not in org._sanitize_path("a  b  c")

    def test_organize_file_source_not_found(self, tmp_path):
        from web.file_organizer import FileOrganizer

        org = FileOrganizer(organize_root=str(tmp_path / "org"))
        result = org.organize_file("/no/such/file.txt", "test_folder")
        assert result["success"] is False

    def test_organize_file_success(self, tmp_path):
        from web.file_organizer import FileOrganizer

        org_root = tmp_path / "org"
        org = FileOrganizer(organize_root=str(org_root))
        src = tmp_path / "myfile.txt"
        src.write_text("content", encoding="utf-8")
        result = org.organize_file(str(src), "category/entity")
        assert result["success"] is True
        assert os.path.exists(result["dest_file"])

    def test_organize_file_duplicate_content(self, tmp_path):
        from web.file_organizer import FileOrganizer

        org_root = tmp_path / "org"
        org = FileOrganizer(organize_root=str(org_root))
        src = tmp_path / "dup.txt"
        src.write_text("same content", encoding="utf-8")
        # Organize once
        org.organize_file(str(src), "dup_test")
        # Organize same content again
        result = org.organize_file(str(src), "dup_test")
        assert result["success"] is True
        assert result.get("skipped_duplicate", False) or result["success"]

    def test_search_files(self, tmp_path):
        from web.file_organizer import FileOrganizer

        org = FileOrganizer(organize_root=str(tmp_path / "org"))
        src = tmp_path / "report.txt"
        src.write_text("data", encoding="utf-8")
        org.organize_file(str(src), "finance")
        results = org.search_files("report")
        assert len(results) >= 1

    def test_get_categories_stats(self, tmp_path):
        from web.file_organizer import FileOrganizer

        org = FileOrganizer(organize_root=str(tmp_path / "org"))
        src = tmp_path / "file.txt"
        src.write_text("x", encoding="utf-8")
        org.organize_file(str(src), "tech/ai")
        stats = org.get_categories_stats()
        assert "tech" in stats

    def test_organize_batch(self, tmp_path):
        from web.file_organizer import FileOrganizer

        org = FileOrganizer(organize_root=str(tmp_path / "org"))
        f1 = tmp_path / "a.txt"
        f1.write_text("a", encoding="utf-8")
        f2 = tmp_path / "b.txt"
        f2.write_text("b", encoding="utf-8")
        results = org.organize_batch(
            [
                {"file": str(f1), "folder": "cat1"},
                {"file": str(f2), "folder": "cat2"},
            ]
        )
        assert len(results) == 2
        assert all(r["success"] for r in results)


# ---------------------------------------------------------------------------
# FileScanner
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFileScanner:

    def _reset_scanner(self):
        """Reset FileScanner singleton state between tests."""
        from web.file_scanner import FileScanner

        with FileScanner._lock:
            FileScanner._index = {}
            FileScanner._status = {
                "running": False,
                "paused": False,
                "finished": False,
                "scanned": 0,
                "indexed": 0,
                "total_estimate": 0,
                "current_dir": "",
                "start_time": None,
                "end_time": None,
                "error": None,
            }
            FileScanner._INDEX_PATH = None
        return FileScanner

    def test_classify(self):
        from web.file_scanner import _classify

        assert _classify(".pdf") == "文档"
        assert _classify(".jpg") == "图片"
        assert _classify(".mp4") == "视频"
        assert _classify(".py") == "代码"
        assert _classify(".xyz") == "其他"

    def test_human_size(self):
        from web.file_scanner import _human_size

        assert "B" in _human_size(100)
        assert "KB" in _human_size(2048)
        assert "MB" in _human_size(5 * 1024 * 1024)

    def test_human_time(self):
        from web.file_scanner import _human_time
        import time

        result = _human_time(time.time())
        assert "-" in result  # date format contains dashes

    def test_search_empty_query(self):
        FS = self._reset_scanner()
        assert FS.search("") == []
        assert FS.search("   ") == []

    def test_search_with_index(self):
        FS = self._reset_scanner()
        from web.file_scanner import FileEntry

        entry = FileEntry(
            path="C:\\docs\\report.pdf",
            name="report.pdf",
            name_lower="report.pdf",
            ext=".pdf",
            size=1000,
            mtime=1700000000.0,
            category="文档",
        )
        with FS._lock:
            FS._index["c:\\docs\\report.pdf"] = entry
        results = FS.search("report")
        assert len(results) >= 1
        assert results[0]["name"] == "report.pdf"

    def test_get_status(self):
        FS = self._reset_scanner()
        status = FS.get_status()
        assert "running" in status
        assert status["running"] is False

    def test_open_file_not_found(self):
        FS = self._reset_scanner()
        result = FS.open_file("/nonexistent/file.txt")
        assert result["success"] is False

    def test_stats_empty(self):
        FS = self._reset_scanner()
        s = FS.stats()
        assert s["total"] == 0

    def test_is_indexed(self):
        FS = self._reset_scanner()
        assert FS.is_indexed() is False

    def test_extract_query_from_input(self):
        from web.file_scanner import extract_query_from_input

        assert "报告" in extract_query_from_input("帮我找一下 报告")
        assert extract_query_from_input("open my_file.docx") == "my_file.docx"

    def test_is_disk_search_intent(self):
        from web.file_scanner import is_disk_search_intent

        assert is_disk_search_intent("帮我找一下报告") is True
        assert is_disk_search_intent("C:\\Users\\file.txt") is True


# ---------------------------------------------------------------------------
# NotificationManager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNotificationManager:

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        self.db_path = str(tmp_path / "notifications.db")

    def _make_manager(self):
        from web.notification_manager import NotificationManager

        return NotificationManager(db_path=self.db_path)

    def test_init_creates_tables(self):
        mgr = self._make_manager()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cursor.fetchall()}
        conn.close()
        assert "notifications" in tables
        assert "user_preferences" in tables

    def test_send_notification_saves(self):
        mgr = self._make_manager()
        nid = mgr.send_notification("user1", "suggestion", "medium", "Test", "body")
        assert nid is not None

    def test_send_notification_force_send(self):
        mgr = self._make_manager()
        # Set prefs that would block low priority
        mgr.update_user_preferences(
            "user1", {"enabled_types": ["suggestion"], "priority_threshold": "high"}
        )
        # Force send overrides prefs
        nid = mgr.send_notification(
            "user1", "suggestion", "low", "Low", force_send=True
        )
        assert nid is not None

    def test_mark_as_read(self):
        mgr = self._make_manager()
        nid = mgr.send_notification("user1", "tip", "low", "Tip")
        mgr.mark_as_read(nid, "user1")
        unread = mgr.get_unread_notifications("user1")
        assert all(n["id"] != nid for n in unread)

    def test_dismiss_notification(self):
        mgr = self._make_manager()
        nid = mgr.send_notification("user1", "alert", "medium", "Alert")
        mgr.dismiss_notification(nid, "user1")
        unread = mgr.get_unread_notifications("user1")
        assert all(n["id"] != nid for n in unread)

    def test_record_action(self):
        mgr = self._make_manager()
        nid = mgr.send_notification("user1", "suggestion", "medium", "Act")
        mgr.record_action(nid, "user1", "accepted")
        # Just verifying no errors

    def test_get_notification_stats(self):
        mgr = self._make_manager()
        mgr.send_notification("user1", "tip", "low", "Stat test")
        stats = mgr.get_notification_stats("user1", days=7)
        assert stats["total_sent"] >= 1

    def test_get_user_preferences_default(self):
        mgr = self._make_manager()
        prefs = mgr.get_user_preferences("new_user")
        assert prefs["max_daily_notifications"] == 20

    def test_register_unregister_connection(self):
        mgr = self._make_manager()
        ws = MagicMock()
        mgr.register_connection("user1", ws)
        assert ws in mgr.connections["user1"]
        mgr.unregister_connection("user1", ws)
        assert "user1" not in mgr.connections


# ---------------------------------------------------------------------------
# OperationHistory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOperationHistory:

    def test_init_creates_dirs(self, tmp_path):
        from web.operation_history import OperationHistory

        hdir = str(tmp_path / "history")
        oh = OperationHistory(history_dir=hdir)
        assert os.path.isdir(hdir)
        assert os.path.isdir(oh.backup_dir)

    def test_record_and_get_operation(self, tmp_path):
        from web.operation_history import OperationHistory

        oh = OperationHistory(history_dir=str(tmp_path / "hist"))
        f = tmp_path / "test.txt"
        f.write_text("hello", encoding="utf-8")
        op_id = oh.record_operation("edit", str(f), {"note": "test"})
        assert op_id is not None
        op = oh.get_operation(op_id)
        assert op is not None
        assert op["type"] == "edit"

    def test_rollback_edit(self, tmp_path):
        from web.operation_history import OperationHistory

        oh = OperationHistory(history_dir=str(tmp_path / "hist"))
        f = tmp_path / "doc.txt"
        f.write_text("original", encoding="utf-8")
        op_id = oh.record_operation("edit", str(f))
        f.write_text("modified", encoding="utf-8")
        result = oh.rollback(op_id)
        assert result["success"] is True
        assert f.read_text(encoding="utf-8") == "original"

    def test_rollback_nonexistent_id(self, tmp_path):
        from web.operation_history import OperationHistory

        oh = OperationHistory(history_dir=str(tmp_path / "hist"))
        result = oh.rollback("no_such_id")
        assert result["success"] is False

    def test_rollback_create_op(self, tmp_path):
        from web.operation_history import OperationHistory

        oh = OperationHistory(history_dir=str(tmp_path / "hist"))
        f = tmp_path / "created.txt"
        f.write_text("new file", encoding="utf-8")
        op_id = oh.record_operation("create", str(f))
        # Create op has no backup => can_rollback is False
        result = oh.rollback(op_id)
        assert result["success"] is False

    def test_get_history_with_filter(self, tmp_path):
        from web.operation_history import OperationHistory

        oh = OperationHistory(history_dir=str(tmp_path / "hist"))
        f1 = tmp_path / "a.txt"
        f1.write_text("a", encoding="utf-8")
        f2 = tmp_path / "b.txt"
        f2.write_text("b", encoding="utf-8")
        oh.record_operation("edit", str(f1))
        oh.record_operation("edit", str(f2))
        filtered = oh.get_history(file_path=str(f1))
        assert all(op["file_path"] == str(f1) for op in filtered)

    def test_get_statistics(self, tmp_path):
        from web.operation_history import OperationHistory

        oh = OperationHistory(history_dir=str(tmp_path / "hist"))
        f = tmp_path / "s.txt"
        f.write_text("x", encoding="utf-8")
        oh.record_operation("edit", str(f))
        stats = oh.get_statistics()
        assert stats["total_operations"] >= 1
        assert "edit" in stats["by_type"]

    def test_cleanup_old_backups(self, tmp_path):
        from web.operation_history import OperationHistory

        oh = OperationHistory(history_dir=str(tmp_path / "hist"))
        f = tmp_path / "old.txt"
        f.write_text("old", encoding="utf-8")
        oh.record_operation("edit", str(f))
        # Force the timestamp to be old
        oh.operations[0]["timestamp"] = "2020-01-01T00:00:00"
        oh._save_history()
        result = oh.cleanup_old_backups(days=1)
        assert result["success"] is True


# ---------------------------------------------------------------------------
# QuickNoteManager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuickNoteManager:

    def test_init_creates_dir(self, tmp_path):
        from web.note_manager import QuickNoteManager

        ndir = str(tmp_path / "notes")
        nm = QuickNoteManager(notes_dir=ndir)
        assert os.path.isdir(ndir)

    def test_add_and_get_note(self, tmp_path):
        from web.note_manager import QuickNoteManager

        nm = QuickNoteManager(notes_dir=str(tmp_path / "notes"))
        note = nm.add_note("Test content", tags=["test"], category="dev")
        assert note is not None
        assert note["content"] == "Test content"
        retrieved = nm.get_note(note["id"])
        assert retrieved["content"] == "Test content"

    def test_search_notes_by_query(self, tmp_path):
        from web.note_manager import QuickNoteManager

        nm = QuickNoteManager(notes_dir=str(tmp_path / "notes"))
        nm.add_note("Python is great", tags=["python"])
        nm.add_note("Java is fine", tags=["java"])
        results = nm.search_notes(query="Python")
        assert len(results) >= 1

    def test_search_notes_by_tag(self, tmp_path):
        from web.note_manager import QuickNoteManager

        nm = QuickNoteManager(notes_dir=str(tmp_path / "notes"))
        nm.add_note("Tagged note", tags=["special"])
        nm.add_note("Other note", tags=["other"])
        results = nm.search_notes(tags=["special"])
        assert len(results) == 1

    def test_search_notes_by_category(self, tmp_path):
        from web.note_manager import QuickNoteManager

        nm = QuickNoteManager(notes_dir=str(tmp_path / "notes"))
        nm.add_note("Cat note", category="work")
        nm.add_note("Other note", category="personal")
        results = nm.search_notes(category="work")
        assert len(results) == 1

    def test_delete_note(self, tmp_path):
        from web.note_manager import QuickNoteManager

        nm = QuickNoteManager(notes_dir=str(tmp_path / "notes"))
        note = nm.add_note("To delete")
        assert nm.delete_note(note["id"]) is True
        assert nm.get_note(note["id"]) is None

    def test_get_recent_notes(self, tmp_path):
        from web.note_manager import QuickNoteManager

        nm = QuickNoteManager(notes_dir=str(tmp_path / "notes"))
        for i in range(5):
            nm.add_note(f"Note {i}")
        recent = nm.get_recent_notes(limit=3)
        assert len(recent) == 3

    def test_get_categories_and_tags(self, tmp_path):
        from web.note_manager import QuickNoteManager

        nm = QuickNoteManager(notes_dir=str(tmp_path / "notes"))
        nm.add_note("A", tags=["alpha", "beta"], category="cat1")
        nm.add_note("B", tags=["gamma"], category="cat2")
        cats = nm.get_categories()
        assert "cat1" in cats
        tags = nm.get_all_tags()
        assert "alpha" in tags
        assert "gamma" in tags
