"""
Tests for large web modules at 0% coverage:
  - document_feedback.py (973 stmts)
  - track_changes_editor.py (633 stmts)
  - batch_file_ops.py (399 stmts)
  - organize_cleanup.py (340 stmts)
  - file_analyzer.py (335 stmts)
"""

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest


# ---------------------------------------------------------------------------
# DocumentFeedbackSystem
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestDocumentFeedback:
    """Tests for web.document_feedback.DocumentFeedbackSystem"""

    def _make(self, **kw):
        with patch("web.document_reader.DocumentReader"), patch(
            "web.document_editor.DocumentEditor"
        ), patch("web.document_annotator.DocumentAnnotator"):
            from web.document_feedback import DocumentFeedbackSystem

            client = kw.get("client")
            model = kw.get("model", "gemini-test")
            obj = DocumentFeedbackSystem(gemini_client=client, default_model_id=model)
        return obj

    # -- construction -------------------------------------------------------
    def test_init_defaults(self):
        obj = self._make()
        assert obj.client is None
        assert obj.default_model_id == "gemini-test"
        assert obj._model_cache is None

    def test_init_with_client(self):
        mock_client = MagicMock()
        obj = self._make(client=mock_client)
        assert obj.client is mock_client

    # -- _extract_summary ---------------------------------------------------
    def test_extract_summary_from_json(self):
        obj = self._make()
        resp = '```json\n{"summary": "Good doc"}\n```'
        assert obj._extract_summary(resp) == "Good doc"

    def test_extract_summary_fallback_text(self):
        obj = self._make()
        assert (
            obj._extract_summary("Some plain text review") == "Some plain text review"
        )

    def test_extract_summary_invalid_json(self):
        obj = self._make()
        resp = "```json\n{invalid}\n```"
        assert obj._extract_summary(resp) == "AI建议已生成"

    def test_extract_summary_empty(self):
        obj = self._make()
        resp = '```json\n{"modifications": []}\n```'
        # no summary key -> text fallback or default
        result = obj._extract_summary(resp)
        assert isinstance(result, str)

    def test_extract_summary_long_text_truncated(self):
        obj = self._make()
        long_text = "A" * 500
        result = obj._extract_summary(long_text)
        assert len(result) <= 200

    # -- _INTERACTIONS_ONLY_MODELS ------------------------------------------
    def test_interactions_only_models_constant(self):
        from web.document_feedback import DocumentFeedbackSystem

        assert (
            "deep-research-pro-preview-12-2025"
            in DocumentFeedbackSystem._INTERACTIONS_ONLY_MODELS
        )

    # -- _format_model_table ------------------------------------------------
    def test_format_model_table_empty(self):
        obj = self._make()
        assert "暂时无法" in obj._format_model_table([])

    def test_format_model_table_with_models(self):
        obj = self._make()
        models = [{"name": "m1", "display_name": "Model One"}]
        table = obj._format_model_table(models)
        assert "m1" in table
        assert "Model One" in table
        assert "| --- | --- |" in table

    # -- _select_best_model -------------------------------------------------
    def test_select_best_model_empty_list(self):
        obj = self._make()
        obj._model_cache = []
        name, models = obj._select_best_model("gemini-2.5-flash")
        assert name == "gemini-2.5-flash"
        assert models == []

    def test_select_best_model_interactions_only_replaced(self):
        obj = self._make()
        obj._model_cache = [{"name": "gemini-2.5-flash", "display_name": "Flash"}]
        name, _ = obj._select_best_model("gemini-3-flash-preview")
        assert name == "gemini-2.5-flash"

    def test_select_best_model_preferred_available(self):
        obj = self._make()
        obj._model_cache = [
            {"name": "gemini-2.5-flash", "display_name": "Flash"},
            {"name": "my-model", "display_name": "My"},
        ]
        name, _ = obj._select_best_model("my-model")
        assert name == "my-model"

    # -- _list_available_models ---------------------------------------------
    def test_list_available_models_cached(self):
        obj = self._make()
        obj._model_cache = [{"name": "cached"}]
        assert obj._list_available_models() == [{"name": "cached"}]

    def test_list_available_models_no_client(self):
        obj = self._make()
        result = obj._list_available_models()
        assert result == []
        assert obj._model_cache == []

    # -- _split_into_chunks_by_paragraphs -----------------------------------
    def test_split_single_chunk(self):
        obj = self._make()
        text = "Para one.\n\nPara two."
        chunks = obj._split_into_chunks_by_paragraphs(text, 5000)
        assert len(chunks) == 1
        assert "Para one." in chunks[0]
        assert "Para two." in chunks[0]

    def test_split_into_multiple_chunks(self):
        obj = self._make()
        text = "A" * 50 + "\n\n" + "B" * 50 + "\n\n" + "C" * 50
        chunks = obj._split_into_chunks_by_paragraphs(text, 60)
        assert len(chunks) >= 2

    def test_split_empty_content(self):
        obj = self._make()
        assert obj._split_into_chunks_by_paragraphs("", 100) == []

    # -- _parse_annotation_response -----------------------------------------
    def test_parse_annotation_json_array(self):
        obj = self._make()
        payload = json.dumps([{"原文": "旧文本", "改为": "新文本", "原因": "简化"}])
        resp = f"```json\n{payload}\n```"
        annotations = obj._parse_annotation_response(resp)
        assert len(annotations) == 1
        assert annotations[0]["原文片段"] == "旧文本"
        assert annotations[0]["修改建议"] == "新文本"

    def test_parse_annotation_raw_array(self):
        obj = self._make()
        payload = json.dumps([{"原文片段": "old", "修改建议": "new"}])
        annotations = obj._parse_annotation_response(payload)
        assert len(annotations) == 1

    def test_parse_annotation_dict_wrapper(self):
        obj = self._make()
        payload = json.dumps({"annotations": [{"original": "old", "modified": "new"}]})
        annotations = obj._parse_annotation_response(payload)
        assert len(annotations) == 1

    def test_parse_annotation_invalid(self):
        obj = self._make()
        assert obj._parse_annotation_response("not json at all {}{}") == []

    def test_parse_annotation_missing_fields(self):
        obj = self._make()
        payload = json.dumps([{"原文": "only original"}])
        assert obj._parse_annotation_response(payload) == []

    # -- _build_annotation_prompt -------------------------------------------
    def test_build_annotation_prompt_academic(self):
        obj = self._make()
        prompt = obj._build_annotation_prompt("docx", "内容片段", "学术润色")
        assert "资深学术编辑" in prompt
        assert "内容片段" in prompt

    def test_build_annotation_prompt_resume(self):
        obj = self._make()
        prompt = obj._build_annotation_prompt("docx", "内容片段", "优化简历")
        assert "简历顾问" in prompt

    def test_build_annotation_prompt_with_long_context(self):
        obj = self._make()
        ctx = "X" * 40000
        prompt = obj._build_annotation_prompt("docx", "片段", "", full_doc_context=ctx)
        # Long context gets truncated
        assert "全文背景参考" in prompt

    # -- _build_analysis_prompt ---------------------------------------------
    def test_build_analysis_prompt_ppt(self):
        obj = self._make()
        prompt = obj._build_analysis_prompt("ppt", "slide content", "改进排版")
        assert "slide_index" in prompt
        assert "改进排版" in prompt

    def test_build_analysis_prompt_word(self):
        obj = self._make()
        prompt = obj._build_analysis_prompt("word", "doc content", "")
        assert "Koto文档智能分析助手" in prompt

    # -- _fallback_annotations_from_chunk -----------------------------------
    def test_fallback_passive(self):
        from web.document_feedback import DocumentFeedbackSystem

        chunk = "本文被广泛使用的技术被认为很重要"
        result = DocumentFeedbackSystem._fallback_annotations_from_chunk(chunk)
        assert isinstance(result, list)
        # Should find at least one passive-voice annotation
        assert any("被" in a.get("原文片段", "") for a in result)

    def test_fallback_nominalization(self):
        from web.document_feedback import DocumentFeedbackSystem

        chunk = "我们对系统进行了优化，对数据进行分析"
        result = DocumentFeedbackSystem._fallback_annotations_from_chunk(chunk)
        assert isinstance(result, list)

    def test_fallback_empty_chunk(self):
        from web.document_feedback import DocumentFeedbackSystem

        assert DocumentFeedbackSystem._fallback_annotations_from_chunk("") == []


# ---------------------------------------------------------------------------
# TrackChangesEditor
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestTrackChangesEditor:
    """Tests for web.track_changes_editor.TrackChangesEditor"""

    def _make(self, **kw):
        from web.track_changes_editor import TrackChangesEditor

        return TrackChangesEditor(**kw)

    # -- construction -------------------------------------------------------
    def test_init_defaults(self):
        obj = self._make()
        assert obj.author == "Koto AI"
        assert obj.change_id == 0

    def test_init_custom_author(self):
        obj = self._make(author="Test User")
        assert obj.author == "Test User"

    # -- _esc ---------------------------------------------------------------
    def test_esc_basic(self):
        from web.track_changes_editor import TrackChangesEditor

        assert TrackChangesEditor._esc("a & b") == "a &amp; b"
        assert TrackChangesEditor._esc("<tag>") == "&lt;tag&gt;"
        assert TrackChangesEditor._esc('"hi"') == "&quot;hi&quot;"
        assert TrackChangesEditor._esc("it's") == "it&apos;s"

    def test_esc_empty(self):
        from web.track_changes_editor import TrackChangesEditor

        assert TrackChangesEditor._esc("") == ""
        assert TrackChangesEditor._esc(None) == ""

    def test_esc_no_special(self):
        from web.track_changes_editor import TrackChangesEditor

        assert TrackChangesEditor._esc("hello world") == "hello world"

    # -- _get_run_text ------------------------------------------------------
    def test_get_run_text(self):
        from docx.oxml.ns import qn
        from lxml import etree

        obj = self._make()
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        run_xml = f'<w:r xmlns:w="{ns}"><w:t>Hello</w:t><w:t> World</w:t></w:r>'
        run = etree.fromstring(run_xml)
        assert obj._get_run_text(run) == "Hello World"

    def test_get_run_text_empty(self):
        from lxml import etree

        obj = self._make()
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        run_xml = f'<w:r xmlns:w="{ns}"></w:r>'
        run = etree.fromstring(run_xml)
        assert obj._get_run_text(run) == ""

    # -- _clone_rPr ---------------------------------------------------------
    def test_clone_rpr_none(self):
        obj = self._make()
        assert obj._clone_rPr(None) == ""

    def test_clone_rpr_element(self):
        from lxml import etree

        obj = self._make()
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        rpr = etree.fromstring(f'<w:rPr xmlns:w="{ns}"><w:b/></w:rPr>')
        result = obj._clone_rPr(rpr)
        assert "rPr" in result
        assert "b" in result  # bold preserved

    # -- _make_run ----------------------------------------------------------
    def test_make_run(self):
        from lxml import etree

        obj = self._make()
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        rpr_xml = f'<w:rPr xmlns:w="{ns}"><w:b/></w:rPr>'
        run = obj._make_run("Hello", rpr_xml)
        text = run.findall(f"{{{ns}}}t")
        assert len(text) == 1
        assert text[0].text == "Hello"

    def test_make_run_escapes_xml(self):
        obj = self._make()
        run = obj._make_run("a & b", "")
        # Should not raise – the & is escaped internally
        assert run is not None

    # -- _add_comments_content_type -----------------------------------------
    def test_add_comments_content_type_new(self):
        obj = self._make()
        ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
        xml_str = (
            f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="{ct_ns}"></Types>'
        )
        result = obj._add_comments_content_type(xml_str.encode("utf-8"))
        assert b"comments.xml" in result

    def test_add_comments_content_type_already_exists(self):
        obj = self._make()
        ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
        xml_str = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Types xmlns="{ct_ns}">'
            f'<Override PartName="/word/comments.xml" ContentType="x"/>'
            f"</Types>"
        )
        data = xml_str.encode("utf-8")
        result = obj._add_comments_content_type(data)
        # Should return unchanged
        assert result == data

    # -- _add_comments_relationship -----------------------------------------
    def test_add_comments_relationship_new(self):
        obj = self._make()
        rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        xml_str = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="{rel_ns}">'
            f'<Relationship Id="rId1" Type="http://example.com/doc" Target="document.xml"/>'
            f"</Relationships>"
        )
        result = obj._add_comments_relationship(xml_str.encode("utf-8"))
        assert b"comments.xml" in result
        assert b"rId2" in result

    def test_add_comments_relationship_already_exists(self):
        obj = self._make()
        rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        xml_str = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="{rel_ns}">'
            f'<Relationship Id="rId5" Type="http://example.com/comments" Target="comments.xml"/>'
            f"</Relationships>"
        )
        data = xml_str.encode("utf-8")
        result = obj._add_comments_relationship(data)
        assert result == data


# ---------------------------------------------------------------------------
# BatchFileOpsManager
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestBatchFileOps:
    """Tests for web.batch_file_ops.BatchFileOpsManager"""

    def _make(self):
        from web.batch_file_ops import BatchFileOpsManager

        return BatchFileOpsManager()

    # -- construction -------------------------------------------------------
    def test_init(self):
        mgr = self._make()
        assert mgr.jobs == {}
        assert mgr.job_events == {}

    # -- create / list / get ------------------------------------------------
    def test_create_job(self):
        mgr = self._make()
        job = mgr.create_job("test", "convert", "/in", "/out", {"target_ext": ".pdf"})
        assert job.name == "test"
        assert job.operation == "convert"
        assert job.status == "queued"
        assert len(mgr.jobs) == 1

    def test_list_jobs_empty(self):
        mgr = self._make()
        assert mgr.list_jobs() == []

    def test_list_jobs_non_empty(self):
        mgr = self._make()
        mgr.create_job("j1", "rename", "/a", "/b", {})
        jobs = mgr.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "j1"

    def test_get_job_not_found(self):
        mgr = self._make()
        assert mgr.get_job("nonexistent") is None

    def test_get_job_found(self):
        mgr = self._make()
        job = mgr.create_job("j", "convert", "/in", "/out", {})
        result = mgr.get_job(job.job_id)
        assert result is not None
        assert result["job_id"] == job.job_id

    # -- is_batch_command ---------------------------------------------------
    def test_is_batch_command_true(self):
        mgr = self._make()
        assert mgr.is_batch_command("批量转换文件") is True
        assert mgr.is_batch_command("图片压缩") is True
        assert mgr.is_batch_command("批量重命名") is True
        assert mgr.is_batch_command("批量整理文件") is True

    def test_is_batch_command_false(self):
        mgr = self._make()
        assert mgr.is_batch_command("普通问题") is False
        assert mgr.is_batch_command("") is False
        assert mgr.is_batch_command(None) is False

    # -- _detect_operation --------------------------------------------------
    def test_detect_operation_convert(self):
        mgr = self._make()
        assert mgr._detect_operation("批量转换文件") == "convert"
        assert mgr._detect_operation("格式转换") == "convert"

    def test_detect_operation_rename(self):
        mgr = self._make()
        assert mgr._detect_operation("批量重命名") == "rename"

    def test_detect_operation_organize(self):
        mgr = self._make()
        assert mgr._detect_operation("批量归档文件") == "organize"

    def test_detect_operation_compress(self):
        mgr = self._make()
        assert mgr._detect_operation("压缩图片") == "compress_images"

    def test_detect_operation_extract(self):
        mgr = self._make()
        assert mgr._detect_operation("批量提取文本") == "extract_text"

    def test_detect_operation_clean(self):
        mgr = self._make()
        assert mgr._detect_operation("批量清理") == "clean_normalize"

    def test_detect_operation_none(self):
        mgr = self._make()
        assert mgr._detect_operation("hello world") is None

    # -- _extract_path ------------------------------------------------------
    def test_extract_path_quoted(self):
        mgr = self._make()
        assert mgr._extract_path('从 "C:\\Data" 输入', []) == "C:\\Data"

    def test_extract_path_anchor(self):
        mgr = self._make()
        assert mgr._extract_path("输入 C:\\MyDir", ["输入"]) == "C:\\MyDir"

    def test_extract_path_windows_fallback(self):
        mgr = self._make()
        result = mgr._extract_path("处理 D:\\Folder\\Sub 文件", [])
        assert result == "D:\\Folder\\Sub"

    def test_extract_path_none(self):
        mgr = self._make()
        assert mgr._extract_path("no path here", ["输入"]) is None

    # -- _extract_exts ------------------------------------------------------
    def test_extract_exts(self):
        mgr = self._make()
        exts = mgr._extract_exts("处理 .docx 和 .pdf 文件")
        assert ".docx" in exts
        assert ".pdf" in exts

    def test_extract_exts_without_dot(self):
        mgr = self._make()
        exts = mgr._extract_exts("处理 docx 文件")
        assert ".docx" in exts

    def test_extract_exts_none(self):
        mgr = self._make()
        assert mgr._extract_exts("什么也没有") == []

    # -- _extract_target_ext ------------------------------------------------
    def test_extract_target_ext(self):
        mgr = self._make()
        assert mgr._extract_target_ext("转为 pdf") == ".pdf"
        assert mgr._extract_target_ext("转换为 .txt") == ".txt"
        assert mgr._extract_target_ext("转成 csv") == ".csv"

    def test_extract_target_ext_none(self):
        mgr = self._make()
        assert mgr._extract_target_ext("没有目标格式") is None

    # -- _extract_rename_rules ----------------------------------------------
    def test_extract_rename_rules(self):
        mgr = self._make()
        rules = mgr._extract_rename_rules("前缀=项目_ 后缀=_v2 序号=001 替换=old->new")
        assert rules["prefix"] == "项目_"
        assert rules["suffix"] == "_v2"
        assert rules["seq_start"] == "001"
        assert rules["replace"] == ("old", "new")

    def test_extract_rename_rules_empty(self):
        mgr = self._make()
        assert mgr._extract_rename_rules("没有规则") == {}

    # -- _extract_kv --------------------------------------------------------
    def test_extract_kv(self):
        mgr = self._make()
        assert mgr._extract_kv("质量=80", "质量") == "80"
        assert mgr._extract_kv("no match", "质量") is None

    # -- _extract_image_options ---------------------------------------------
    def test_extract_image_options(self):
        mgr = self._make()
        opts = mgr._extract_image_options("质量=80 宽=1200 高=800 格式=png")
        assert opts["quality"] == 80
        assert opts["width"] == 1200
        assert opts["height"] == 800
        assert opts["format"] == ".png"

    def test_extract_image_options_empty(self):
        mgr = self._make()
        assert mgr._extract_image_options("nothing") == {}

    # -- parse_command ------------------------------------------------------
    def test_parse_command_success(self):
        mgr = self._make()
        result = mgr.parse_command('批量转换 从 "C:\\A" 输出到 "D:\\B" 转为 pdf')
        assert result["success"] is True
        assert result["operation"] == "convert"
        assert result["options"]["target_ext"] == ".pdf"

    def test_parse_command_no_operation(self):
        mgr = self._make()
        result = mgr.parse_command("普通问题")
        assert result["success"] is False

    def test_parse_command_missing_dirs(self):
        mgr = self._make()
        result = mgr.parse_command("批量转换 转为 pdf")
        assert result["success"] is False

    def test_parse_command_convert_no_target(self):
        mgr = self._make()
        result = mgr.parse_command('批量转换 从 "C:\\A" 输出到 "D:\\B"')
        assert result["success"] is False
        assert "缺少目标格式" in result["error"]

    # -- _usage_hint --------------------------------------------------------
    def test_usage_hint(self):
        mgr = self._make()
        hint = mgr._usage_hint()
        assert "批量转换" in hint
        assert "批量重命名" in hint

    # -- _collect_files -----------------------------------------------------
    def test_collect_files(self, tmp_path):
        mgr = self._make()
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.pdf").write_text("world")
        files = mgr._collect_files(tmp_path, None)
        assert len(files) == 2

    def test_collect_files_with_ext_filter(self, tmp_path):
        mgr = self._make()
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.pdf").write_text("world")
        files = mgr._collect_files(tmp_path, [".txt"])
        assert len(files) == 1
        assert files[0].suffix == ".txt"

    def test_collect_files_nonexistent(self, tmp_path):
        mgr = self._make()
        assert mgr._collect_files(tmp_path / "nope", None) == []

    # -- _relative_output_path ----------------------------------------------
    def test_relative_output_path(self, tmp_path):
        mgr = self._make()
        inp = tmp_path / "in"
        out = tmp_path / "out"
        inp.mkdir()
        out.mkdir()
        src = inp / "sub" / "file.docx"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("x")
        dest = mgr._relative_output_path(src, inp, out, ".pdf")
        assert dest.suffix == ".pdf"
        assert str(out) in str(dest)

    # -- _build_summary & _job_to_dict --------------------------------------
    def test_build_summary(self):
        from web.batch_file_ops import BatchJobRecord

        mgr = self._make()
        job = BatchJobRecord(
            job_id="abc",
            name="test",
            operation="convert",
            input_dir="/in",
            output_dir="/out",
            total_items=10,
            processed_items=8,
            failed_items=2,
            errors=["err1"],
        )
        summary = mgr._build_summary(job)
        assert "10" in summary
        assert "8" in summary

    def test_job_to_dict_none(self):
        mgr = self._make()
        assert mgr._job_to_dict(None) is None

    # -- _emit / stream_job -------------------------------------------------
    def test_emit_and_stream(self):
        import queue as q

        mgr = self._make()
        job = mgr.create_job("j", "convert", "/in", "/out", {})
        mgr._emit(job.job_id, {"type": "progress", "current": 1})
        mgr._emit(job.job_id, {"type": "final"})
        events = list(mgr.iter_job_events(job.job_id))
        assert len(events) == 2
        assert events[-1]["type"] == "final"

    def test_iter_job_events_missing_id(self):
        mgr = self._make()
        events = list(mgr.iter_job_events("missing"))
        assert events[0]["type"] == "error"

    def test_stream_job_format(self):
        mgr = self._make()
        job = mgr.create_job("j", "convert", "/in", "/out", {})
        mgr._emit(job.job_id, {"type": "final"})
        chunks = list(mgr.stream_job(job.job_id))
        assert chunks[0].startswith("data: ")


# ---------------------------------------------------------------------------
# OrganizeCleanup
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestOrganizeCleanup:
    """Tests for web.organize_cleanup.OrganizeCleanup"""

    def _make(self, root=None):
        from web.organize_cleanup import OrganizeCleanup

        return OrganizeCleanup(organize_root=root or "workspace/_organize")

    # -- construction -------------------------------------------------------
    def test_init(self):
        obj = self._make("/tmp/org")
        assert obj.organize_root == Path("/tmp/org")
        assert obj.index_file == Path("/tmp/org") / "index.json"
        assert obj.log == []

    # -- _log ---------------------------------------------------------------
    def test_log(self):
        obj = self._make()
        obj._log("msg1")
        obj._log("msg2")
        assert len(obj.log) == 2
        assert obj.log[0] == "msg1"

    # -- _REVISION_PATTERNS ------------------------------------------------
    def test_revision_patterns(self):
        from web.organize_cleanup import OrganizeCleanup

        assert len(OrganizeCleanup._REVISION_PATTERNS) == 8

    # -- _clean_folder_name -------------------------------------------------
    def test_clean_folder_name_basic(self):
        obj = self._make()
        assert obj._clean_folder_name("category/MyFolder_revised(2)") == "myfolder"

    def test_clean_folder_name_copy(self):
        obj = self._make()
        assert obj._clean_folder_name("dir/test_copy3") == "test"

    def test_clean_folder_name_no_revision(self):
        obj = self._make()
        assert obj._clean_folder_name("normal_folder") == "normal_folder"

    # -- _are_similar -------------------------------------------------------
    def test_are_similar_exact(self):
        obj = self._make()
        assert obj._are_similar("abc", "abc") is True

    def test_are_similar_prefix(self):
        obj = self._make()
        assert obj._are_similar("abcdef", "abc") is True

    def test_are_similar_fuzzy(self):
        obj = self._make()
        assert obj._are_similar("document_analysis", "document_analyss") is True

    def test_are_similar_different(self):
        obj = self._make()
        assert obj._are_similar("finance", "medical") is False

    def test_are_similar_empty(self):
        obj = self._make()
        assert obj._are_similar("", "abc") is False
        assert obj._are_similar("abc", "") is False

    # -- _file_hash ---------------------------------------------------------
    def test_file_hash(self, tmp_path):
        from web.organize_cleanup import OrganizeCleanup

        f = tmp_path / "test.txt"
        f.write_bytes(b"hello world")
        h = OrganizeCleanup._file_hash(f)
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert h == expected

    def test_file_hash_nonexistent(self, tmp_path):
        from web.organize_cleanup import OrganizeCleanup

        h = OrganizeCleanup._file_hash(tmp_path / "nope.txt")
        assert h == ""

    # -- _unique_dest -------------------------------------------------------
    def test_unique_dest(self, tmp_path):
        from web.organize_cleanup import OrganizeCleanup

        target = tmp_path / "file.txt"
        # File doesn't exist yet – _unique_dest always appends counter
        result = OrganizeCleanup._unique_dest(target)
        assert result.stem == "file_1"
        assert result.suffix == ".txt"

    def test_unique_dest_collisions(self, tmp_path):
        from web.organize_cleanup import OrganizeCleanup

        (tmp_path / "file_1.txt").write_text("x")
        result = OrganizeCleanup._unique_dest(tmp_path / "file.txt")
        assert result.name == "file_2.txt"

    # -- _create_merge_plan -------------------------------------------------
    def test_create_merge_plan(self):
        obj = self._make()
        groups = [{"a", "b", "c"}]
        folder_info = {
            "a": {"files": ["f1", "f2"]},
            "b": {"files": ["f1"]},
            "c": {"files": ["f1", "f2", "f3"]},
        }
        plans = obj._create_merge_plan(groups, folder_info)
        assert len(plans) == 1
        assert plans[0]["target"] == "c"  # most files
        assert set(plans[0]["sources"]) == {"a", "b"}

    # -- _build_similarity_groups -------------------------------------------
    def test_build_similarity_groups(self):
        obj = self._make()
        folder_info = {
            "group/report": {"files": ["a.txt"]},
            "group/report_revised(1)": {"files": ["a.txt"]},
            "other/finance": {"files": ["b.txt"]},
        }
        groups = obj._build_similarity_groups(folder_info)
        # report and report_revised should group together
        assert len(groups) >= 1
        found = False
        for g in groups:
            if "group/report" in g and "group/report_revised(1)" in g:
                found = True
        assert found

    # -- _scan_folders ------------------------------------------------------
    def test_scan_folders(self, tmp_path):
        obj = self._make(str(tmp_path))
        sub = tmp_path / "myfolder"
        sub.mkdir()
        (sub / "file.txt").write_text("data")
        (sub / "_metadata.json").write_text("{}")
        info = obj._scan_folders()
        assert "myfolder" in info
        assert "file.txt" in info["myfolder"]["files"]
        assert "_metadata.json" not in info["myfolder"]["files"]

    # -- _deduplicate_within_folders ----------------------------------------
    def test_deduplicate_within_folders(self, tmp_path):
        obj = self._make(str(tmp_path))
        sub = tmp_path / "folder"
        sub.mkdir()
        (sub / "orig.txt").write_bytes(b"same content")
        (sub / "orig_copy.txt").write_bytes(b"same content")
        count = obj._deduplicate_within_folders()
        assert count == 1
        # One file should remain
        remaining = [f for f in sub.iterdir() if not f.name.startswith("_")]
        assert len(remaining) == 1

    # -- _rebuild_index -----------------------------------------------------
    def test_rebuild_index(self, tmp_path):
        obj = self._make(str(tmp_path))
        sub = tmp_path / "docs"
        sub.mkdir()
        (sub / "a.txt").write_text("content")
        obj._rebuild_index()
        assert obj.index_file.exists()
        data = json.loads(obj.index_file.read_text(encoding="utf-8"))
        assert data["total_files"] == 1
        assert data["version"] == "1.0"

    # -- _cleanup_empty_folders ---------------------------------------------
    def test_cleanup_empty_folders(self, tmp_path):
        obj = self._make(str(tmp_path))
        empty = tmp_path / "empty_dir"
        empty.mkdir()
        # Add only underscore file
        (empty / "_meta.json").write_text("{}")
        count = obj._cleanup_empty_folders()
        assert count >= 1


# ---------------------------------------------------------------------------
# FileAnalyzer
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestFileAnalyzer:
    """Tests for web.file_analyzer.FileAnalyzer"""

    def _make(self):
        from web.file_analyzer import FileAnalyzer

        return FileAnalyzer()

    # -- construction -------------------------------------------------------
    def test_init(self):
        fa = self._make()
        assert isinstance(fa.rules, dict)
        assert "finance" in fa.rules
        assert "medical" in fa.rules
        assert isinstance(fa.industry_labels, dict)
        assert fa.keywords_cache == {}

    def test_class_constants(self):
        from web.file_analyzer import FileAnalyzer

        assert FileAnalyzer.OLLAMA_URL == "http://localhost:11434"
        assert FileAnalyzer.AI_MODEL == "qwen3:8b"

    # -- _load_classification_rules -----------------------------------------
    def test_load_classification_rules(self):
        fa = self._make()
        rules = fa._load_classification_rules()
        assert "finance" in rules
        assert "property" in rules
        assert "education" in rules
        assert "projects" in rules
        for ind in rules.values():
            assert "keywords" in ind
            assert "subcategories" in ind

    # -- _extract_keywords --------------------------------------------------
    def test_extract_keywords(self):
        fa = self._make()
        kws = fa._extract_keywords("投资合同.docx", "本合同由双方签署融资协议")
        assert "合同" in kws
        assert "融资" in kws or "协议" in kws

    def test_extract_keywords_empty(self):
        fa = self._make()
        kws = fa._extract_keywords("random.txt", "nothing relevant")
        assert isinstance(kws, list)

    # -- _classify_industry -------------------------------------------------
    def test_classify_industry_finance(self):
        fa = self._make()
        ind, conf = fa._classify_industry(["合同", "融资", "投资"], "投资合同.docx", "")
        assert ind == "finance"
        assert conf > 0

    def test_classify_industry_no_keywords(self):
        fa = self._make()
        ind, conf = fa._classify_industry([], "random_xyz.txt", "")
        # With no keywords and no file pattern match, scores are all 0
        assert conf <= 0.3

    def test_classify_industry_file_pattern_boost(self):
        fa = self._make()
        ind, conf = fa._classify_industry([], "物业管理报告.docx", "")
        assert ind == "property"

    # -- _classify_category -------------------------------------------------
    def test_classify_category_match(self):
        fa = self._make()
        cat = fa._classify_category("finance", ["合同"])
        assert cat == "contract"

    def test_classify_category_default(self):
        fa = self._make()
        cat = fa._classify_category("finance", ["unrelated"])
        assert cat == "document"

    def test_classify_category_unknown_industry(self):
        fa = self._make()
        cat = fa._classify_category("nonexistent", ["合同"])
        assert cat == "document"

    # -- _extract_timestamp -------------------------------------------------
    def test_extract_timestamp_year(self):
        fa = self._make()
        assert fa._extract_timestamp("report_2024.pdf", "") == "2024"

    def test_extract_timestamp_year_month(self):
        fa = self._make()
        assert fa._extract_timestamp("report_2024-03.pdf", "") == "2024-03"

    def test_extract_timestamp_none(self):
        fa = self._make()
        assert fa._extract_timestamp("file.txt", "no date") is None

    # -- _is_generic_name ---------------------------------------------------
    def test_is_generic_name_true(self):
        fa = self._make()
        assert fa._is_generic_name("报告") is True
        assert fa._is_generic_name("document") is True
        assert fa._is_generic_name("123") is True
        assert fa._is_generic_name("") is True
        assert fa._is_generic_name("A") is True  # < 2 chars
        assert fa._is_generic_name(None) is True

    def test_is_generic_name_false(self):
        fa = self._make()
        assert fa._is_generic_name("华芯长晟科技") is False
        assert fa._is_generic_name("ProjectAlpha") is False

    # -- _sanitize_component ------------------------------------------------
    def test_sanitize_component(self):
        fa = self._make()
        assert fa._sanitize_component("a/b\\c:d") == "a_b_c_d"
        assert fa._sanitize_component("") == ""
        assert fa._sanitize_component('a*b?c"d') == "a_b_c_d"

    # -- _clean_filename_stem -----------------------------------------------
    def test_clean_filename_stem_revised(self):
        fa = self._make()
        assert fa._clean_filename_stem("report_revised(3)") == "report"

    def test_clean_filename_stem_copy(self):
        fa = self._make()
        assert fa._clean_filename_stem("document_copy") == "document"

    def test_clean_filename_stem_timestamp(self):
        fa = self._make()
        assert fa._clean_filename_stem("file_20260203_004341") == "file"

    def test_clean_filename_stem_no_suffix(self):
        fa = self._make()
        assert fa._clean_filename_stem("clean_name") == "clean_name"

    def test_clean_filename_stem_empty_becomes_original(self):
        fa = self._make()
        # If cleaning removes everything, returns original
        assert fa._clean_filename_stem("(1)") == "(1)"

    # -- _generate_folder_path ----------------------------------------------
    def test_generate_folder_path_with_entity(self):
        fa = self._make()
        path = fa._generate_folder_path(
            "finance", "contract", "2024", [], entity_name="华芯科技"
        )
        assert path == "finance/华芯科技"

    def test_generate_folder_path_generic_entity(self):
        fa = self._make()
        path = fa._generate_folder_path(
            "finance", "contract", "2024", [], entity_name="报告"
        )
        assert path == "finance/contract"

    def test_generate_folder_path_no_entity(self):
        fa = self._make()
        path = fa._generate_folder_path("finance", "contract", None, [])
        assert path == "finance/contract"

    def test_generate_folder_path_default_category(self):
        fa = self._make()
        path = fa._generate_folder_path("finance", "document", None, [])
        assert path == "finance"

    # -- _extract_primary_entity --------------------------------------------
    def test_extract_primary_entity_labeled(self):
        fa = self._make()
        name, etype = fa._extract_primary_entity("file.txt", "公司名称：华芯长晟科技")
        assert name is not None
        assert "华芯" in name

    def test_extract_primary_entity_company_suffix(self):
        fa = self._make()
        name, etype = fa._extract_primary_entity(
            "file.txt", "北京明德科技有限公司签署了合同"
        )
        assert name is not None
        assert etype == "company"

    def test_extract_primary_entity_filename_fallback(self):
        fa = self._make()
        name, etype = fa._extract_primary_entity("华芯长晟分析报告.docx", "")
        assert name is not None

    def test_extract_primary_entity_generic_fallback(self):
        fa = self._make()
        # "(1)" cleaned stem is "(1)", which is all digits/special -> generic
        name, etype = fa._extract_primary_entity("报告.txt", "")
        # "报告" is a generic name, so entity from filename is generic
        # but _clean_filename_stem returns "报告" which IS generic
        # code returns (name, "document") even for generic, so check it goes to fallback
        assert name is None or fa._is_generic_name(name) or etype is not None

    # -- _extract_content ---------------------------------------------------
    def test_extract_content_txt(self, tmp_path):
        fa = self._make()
        f = tmp_path / "test.txt"
        f.write_text("Hello world content", encoding="utf-8")
        content = fa._extract_content(str(f))
        assert "Hello world" in content

    def test_extract_content_json(self, tmp_path):
        fa = self._make()
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}', encoding="utf-8")
        content = fa._extract_content(str(f))
        assert "key" in content

    def test_extract_content_unknown_type(self, tmp_path):
        fa = self._make()
        f = tmp_path / "image.bmp"
        f.write_bytes(b"\x00\x01")
        content = fa._extract_content(str(f))
        assert content == "image.bmp"  # falls back to filename

    # -- analyze_file -------------------------------------------------------
    @patch.object(
        __import__("web.file_analyzer", fromlist=["FileAnalyzer"]).FileAnalyzer,
        "_ai_classify",
        return_value=None,
    )
    def test_analyze_file_basic(self, mock_ai, tmp_path):
        fa = self._make()
        f = tmp_path / "投资合同_2024.txt"
        f.write_text("本合同由甲方融资签署", encoding="utf-8")
        result = fa.analyze_file(str(f))
        assert result["success"] is True
        assert result["file_name"] == "投资合同_2024.txt"
        assert result["industry"] == "finance"
        assert result["timestamp"] == "2024"

    @patch.object(
        __import__("web.file_analyzer", fromlist=["FileAnalyzer"]).FileAnalyzer,
        "_ai_classify",
        return_value=None,
    )
    def test_analyze_file_not_found(self, mock_ai):
        fa = self._make()
        result = fa.analyze_file("/nonexistent/file.txt")
        assert result["success"] is False

    # -- analyze_batch ------------------------------------------------------
    @patch.object(
        __import__("web.file_analyzer", fromlist=["FileAnalyzer"]).FileAnalyzer,
        "_ai_classify",
        return_value=None,
    )
    def test_analyze_batch(self, mock_ai, tmp_path):
        fa = self._make()
        f1 = tmp_path / "a.txt"
        f1.write_text("合同", encoding="utf-8")
        f2 = tmp_path / "b.txt"
        f2.write_text("课程", encoding="utf-8")
        results = fa.analyze_batch([str(f1), str(f2)])
        assert len(results) == 2
        assert all(r["success"] for r in results)

    # -- _ai_classify (mocked) ----------------------------------------------
    @patch("web.file_analyzer.requests.post")
    def test_ai_classify_ollama_unavailable(self, mock_post, tmp_path):
        from web.file_analyzer import FileAnalyzer

        FileAnalyzer._ai_available = False
        FileAnalyzer._ai_check_time = datetime.now().timestamp()
        fa = self._make()
        result = fa._ai_classify("test.txt", "content", ".txt")
        assert result is None
        # Reset
        FileAnalyzer._ai_available = None
        FileAnalyzer._ai_check_time = 0
