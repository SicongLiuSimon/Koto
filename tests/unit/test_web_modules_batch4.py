"""
Unit tests for web modules batch 4:
  - EnhancedMemoryManager (+ UserProfile)
  - KnowledgeBase
  - KnowledgeGraph
  - DocumentEditor
  - QualityEvaluator (PPTEvaluator, DocumentEvaluator, evaluate_quality)
  - FileIndexer

Covers constructors, public methods, utility functions, and error paths.
All external dependencies (filesystem, DB, APIs) are mocked.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, mock_open, PropertyMock

import pytest


# ── Pre-import patching ──────────────────────────────────────────────────────
# PersonalityMatrix is referenced in enhanced_memory_manager but not imported
# at module level (likely loaded via exec/plugin at runtime). Inject a stub
# into the module namespace so the class can be instantiated in tests.

def _ensure_personality_matrix():
    """Inject a stub PersonalityMatrix into the enhanced_memory_manager module."""
    import web.enhanced_memory_manager as _emm

    if not hasattr(_emm, "PersonalityMatrix"):
        class _StubPersonalityMatrix:
            def __init__(self, path=None):
                self.data = {}

            def to_context_string(self):
                return ""

            @staticmethod
            def update_async(*args, **kwargs):
                pass

        _emm.PersonalityMatrix = _StubPersonalityMatrix

_ensure_personality_matrix()

# concept_extractor is imported at top-level in knowledge_graph.py but may
# not be on sys.path. Provide a stub module so the import succeeds.
if "concept_extractor" not in sys.modules:
    _ce_mod = type(sys)("concept_extractor")
    _ce_mod.ConceptExtractor = MagicMock
    sys.modules["concept_extractor"] = _ce_mod


# ═══════════════════════════════════════════════════════════════════════════════
# EnhancedMemoryManager  (+ UserProfile helper)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestUserProfile:
    """Tests for web.enhanced_memory_manager.UserProfile."""

    @pytest.fixture()
    def tmp_profile(self, tmp_path):
        profile_file = tmp_path / "profile.json"
        from web.enhanced_memory_manager import UserProfile
        return UserProfile(str(profile_file))

    def test_creates_default_profile_when_no_file(self, tmp_profile):
        p = tmp_profile.profile
        assert "communication_style" in p
        assert "technical_background" in p
        assert p["communication_style"]["preferred_language"] == "zh-CN"

    def test_save_and_reload(self, tmp_path):
        profile_file = tmp_path / "profile.json"
        from web.enhanced_memory_manager import UserProfile
        up = UserProfile(str(profile_file))
        up.profile["communication_style"]["formality"] = "formal"
        up.save()

        up2 = UserProfile(str(profile_file))
        assert up2.profile["communication_style"]["formality"] == "formal"

    def test_deep_merge_preserves_base_keys(self, tmp_profile):
        base = {"a": 1, "b": {"c": 2, "d": 3}}
        override = {"b": {"c": 99}}
        result = tmp_profile._deep_merge(base, override)
        assert result == {"a": 1, "b": {"c": 99, "d": 3}}

    def test_update_from_extraction_adds_languages(self, tmp_profile):
        tmp_profile.update_from_extraction({"programming_languages": ["Python", "Go"]})
        assert "Python" in tmp_profile.profile["technical_background"]["programming_languages"]
        assert "Go" in tmp_profile.profile["technical_background"]["programming_languages"]

    def test_update_from_extraction_adds_tools_and_domains(self, tmp_profile):
        tmp_profile.update_from_extraction({"tools": ["VSCode"], "domains": ["AI"]})
        assert "VSCode" in tmp_profile.profile["technical_background"]["tools"]
        assert "AI" in tmp_profile.profile["technical_background"]["domains"]

    def test_update_from_extraction_likes_dislikes(self, tmp_profile):
        tmp_profile.update_from_extraction({"likes": ["dark mode"], "dislikes": ["verbose logs"]})
        assert "dark mode" in tmp_profile.profile["preferences"]["likes"]
        assert "verbose logs" in tmp_profile.profile["preferences"]["dislikes"]

    def test_increment_topic(self, tmp_profile):
        tmp_profile.increment_topic("Python")
        tmp_profile.increment_topic("Python")
        assert tmp_profile.profile["work_patterns"]["frequent_topics"]["Python"] == 2

    def test_to_context_string_returns_nonempty(self, tmp_profile):
        tmp_profile.profile["technical_background"]["programming_languages"] = ["Python"]
        ctx = tmp_profile.to_context_string()
        assert "Python" in ctx
        assert "[用户画像]" in ctx

    def test_get_brief_summary(self, tmp_profile):
        tmp_profile.profile["technical_background"]["programming_languages"] = ["Python"]
        tmp_profile.profile["technical_background"]["experience_level"] = "senior"
        s = tmp_profile.get_brief_summary()
        assert "senior" in s
        assert "Python" in s

    def test_load_corrupt_file_falls_back_default(self, tmp_path):
        profile_file = tmp_path / "bad.json"
        profile_file.write_text("NOT JSON", encoding="utf-8")
        from web.enhanced_memory_manager import UserProfile
        up = UserProfile(str(profile_file))
        assert "communication_style" in up.profile


@pytest.mark.unit
class TestEnhancedMemoryManager:
    """Tests for web.enhanced_memory_manager.EnhancedMemoryManager."""

    @pytest.fixture()
    def mgr(self, tmp_path):
        mem_path = str(tmp_path / "memory.json")
        prof_path = str(tmp_path / "profile.json")
        sum_path = str(tmp_path / "summaries.json")
        vec_path = str(tmp_path / "vectors.json")
        from web.enhanced_memory_manager import EnhancedMemoryManager
        m = EnhancedMemoryManager(
            memory_path=mem_path,
            profile_path=prof_path,
            summary_path=sum_path,
            vector_path=vec_path,
        )
        return m

    def test_init_creates_empty_state(self, mgr):
        assert mgr.memories == []
        assert mgr.summaries == {}
        assert mgr.vector_memories == []

    def test_add_memory_returns_item_and_saves(self, mgr):
        item = mgr.add_memory("user likes Python", category="user_preference")
        assert item is not None
        assert item["content"] == "user likes Python"
        assert item["category"] == "user_preference"
        assert len(mgr.memories) == 1

    def test_add_memory_empty_content_returns_none(self, mgr):
        assert mgr.add_memory("") is None
        assert mgr.add_memory("   ") is None

    def test_add_memory_dedup_rejects_exact_duplicate(self, mgr):
        mgr.add_memory("I like cats")
        result = mgr.add_memory("I like cats")
        assert result is None
        assert len(mgr.memories) == 1

    def test_is_duplicate_jaccard(self, mgr):
        mgr.memories = [{"content": "the quick brown fox jumps over the lazy dog"}]
        assert mgr._is_duplicate("the quick brown fox jumps over the lazy dog") is True
        assert mgr._is_duplicate("completely different text") is False

    def test_search_memories_keyword_match(self, mgr):
        mgr.add_memory("Python is my favorite language", category="user_preference")
        mgr.add_memory("I enjoy hiking on weekends", category="personal")
        results = mgr.search_memories("Python language")
        assert len(results) >= 1
        assert "Python" in results[0]["content"]

    def test_search_memories_empty_query(self, mgr):
        assert mgr.search_memories("") == []

    def test_delete_memory(self, mgr):
        item = mgr.add_memory("deletable")
        assert mgr.delete_memory(item["id"]) is True
        assert len(mgr.memories) == 0

    def test_delete_memory_nonexistent(self, mgr):
        assert mgr.delete_memory(99999) is False

    def test_get_all_memories_sorted(self, mgr):
        mgr.add_memory("first")
        time.sleep(0.01)
        mgr.add_memory("second")
        all_mems = mgr.get_all_memories()
        assert all_mems[0]["content"] == "second"

    def test_gc_stale_removes_old_auto_memories(self, mgr):
        old_date = (datetime.now() - timedelta(days=120)).isoformat()
        mgr.memories = [
            {"id": 1, "content": "old auto", "source": "extraction",
             "use_count": 0, "created_at": old_date, "category": "general"},
            {"id": 2, "content": "user added", "source": "user",
             "use_count": 0, "created_at": old_date, "category": "general"},
        ]
        removed = mgr._gc_stale()
        assert removed >= 1
        contents = [m["content"] for m in mgr.memories]
        assert "user added" in contents

    def test_keyword_extract_detects_python(self, mgr):
        extracted = {"memories": [], "profile_updates": {}}
        mgr._keyword_extract("I am learning python web dev", extracted)
        assert "python" in extracted["profile_updates"].get("programming_languages", [])

    def test_get_profile_returns_dict(self, mgr):
        p = mgr.get_profile()
        assert isinstance(p, dict)
        assert "communication_style" in p

    def test_update_profile_manually(self, mgr):
        mgr.update_profile_manually({"custom_key": "value"})
        assert mgr.user_profile.profile["custom_key"] == "value"


# ═══════════════════════════════════════════════════════════════════════════════
# KnowledgeBase
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestKnowledgeBase:
    """Tests for web.knowledge_base.KnowledgeBase."""

    @pytest.fixture()
    def kb(self, tmp_path):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            with patch("web.knowledge_base.genai", None):
                from web.knowledge_base import KnowledgeBase
                return KnowledgeBase(workspace_dir=str(tmp_path))

    def test_init_creates_kb_dir(self, kb, tmp_path):
        assert (tmp_path / "knowledge_base").is_dir()

    def test_empty_index_and_chunks(self, kb):
        assert kb.index == {"documents": {}, "last_updated": None}
        assert kb.chunks == {"chunks": {}, "last_updated": None}

    def test_chunk_text_short_string(self, kb):
        chunks = kb._chunk_text("short")
        assert chunks == ["short"]

    def test_chunk_text_empty_string(self, kb):
        assert kb._chunk_text("") == []
        assert kb._chunk_text(None) == []

    def test_chunk_text_long_string(self, kb):
        long_text = "A" * 2000
        chunks = kb._chunk_text(long_text)
        assert len(chunks) > 1

    def test_get_embeddings_no_client_returns_zeros(self, kb):
        kb.client = None
        result = kb._get_embeddings(["hello", "world"])
        assert len(result) == 2
        assert all(v == 0.0 for v in result[0])

    def test_add_content_empty_text(self, kb):
        result = kb.add_content("", {"file_path": "x"})
        assert result["success"] is False

    def test_add_content_success(self, kb):
        text = "Hello world this is a test document with some content."
        result = kb.add_content(text, {"file_path": "/test.txt", "file_name": "test.txt"})
        assert result["success"] is True
        assert "doc_id" in result

    def test_add_content_dedup(self, kb):
        text = "Duplicate content test"
        kb.add_content(text, {"file_path": "a.txt", "file_name": "a.txt"})
        result = kb.add_content(text, {"file_path": "a.txt", "file_name": "a.txt"})
        assert result["success"] is True
        assert "已存在" in result.get("message", "")

    def test_get_stats(self, kb):
        stats = kb.get_stats()
        assert stats["total_documents"] == 0
        assert stats["total_chunks"] == 0

    def test_remove_document_not_found(self, kb):
        result = kb.remove_document("nonexistent")
        assert result["success"] is False

    def test_remove_document_success(self, kb):
        text = "Remove me later"
        added = kb.add_content(text, {"file_path": "rm.txt", "file_name": "rm.txt"})
        doc_id = added["doc_id"]
        result = kb.remove_document(doc_id)
        assert result["success"] is True
        assert doc_id not in kb.index["documents"]

    def test_search_empty_kb(self, kb):
        results = kb.search("anything")
        assert results == []

    def test_extract_text_unsupported_format(self, kb):
        result = kb._extract_text("file.xyz")
        assert "不支持" in result


# ═══════════════════════════════════════════════════════════════════════════════
# KnowledgeGraph
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestKnowledgeGraph:
    """Tests for web.knowledge_graph.KnowledgeGraph."""

    @pytest.fixture()
    def kg(self, tmp_path):
        db_file = str(tmp_path / "kg.db")
        from web.knowledge_graph import KnowledgeGraph
        return KnowledgeGraph(db_path=db_file)

    def test_init_creates_tables(self, kg):
        conn = sqlite3.connect(kg.db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "nodes" in tables
        assert "edges" in tables
        assert "entity_triples" in tables

    def test_add_file_node_returns_node_id(self, kg):
        nid = kg.add_file_node("/some/file.py", {"size": 100})
        assert nid == "file:/some/file.py"

    def test_add_concept_node_returns_node_id(self, kg):
        nid = kg.add_concept_node("machine_learning", {"freq": 5})
        assert nid == "concept:machine_learning"

    def test_add_edge_and_get_graph_data(self, kg):
        fid = kg.add_file_node("a.py")
        cid = kg.add_concept_node("AI")
        kg.add_edge(fid, cid, "contains", weight=0.9)
        data = kg.get_graph_data()
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1
        assert data["edges"][0]["weight"] == 0.9

    def test_get_statistics_empty_graph(self, kg):
        stats = kg.get_statistics()
        assert stats["total_files"] == 0
        assert stats["total_concepts"] == 0

    def test_get_statistics_with_nodes(self, kg):
        kg.add_file_node("x.py")
        kg.add_concept_node("ML")
        stats = kg.get_statistics()
        assert stats["total_files"] == 1
        assert stats["total_concepts"] == 1

    def test_get_file_neighbors_missing_node(self, kg):
        result = kg.get_file_neighbors("nonexistent.py")
        assert "error" in result

    def test_get_file_neighbors_with_data(self, kg):
        fid = kg.add_file_node("main.py")
        cid = kg.add_concept_node("web")
        kg.add_edge(fid, cid, "contains", weight=0.7)
        result = kg.get_file_neighbors("main.py", depth=1)
        assert result["center_node"] == "file:main.py"
        assert len(result["edges"]) >= 1

    def test_add_triple_and_search(self, kg):
        ok = kg.add_triple("Python", "is_a", "Language", confidence=0.9)
        assert ok is True
        results = kg.search_triples("Python")
        assert len(results) == 1
        assert results[0]["relation"] == "is_a"

    def test_add_triple_dedup(self, kg):
        kg.add_triple("A", "rel", "B")
        ok = kg.add_triple("A", "rel", "B")
        assert ok is False

    def test_add_triple_empty_fields(self, kg):
        assert kg.add_triple("", "rel", "B") is False
        assert kg.add_triple("A", "", "B") is False

    def test_search_triples_fuzzy(self, kg):
        kg.add_triple("Python3", "version_of", "Python")
        results = kg.search_triples_fuzzy("Python")
        assert len(results) >= 1

    def test_get_concept_cluster(self, kg):
        fid = kg.add_file_node("data.py")
        cid = kg.add_concept_node("ML")
        kg.add_edge(fid, cid, "contains", weight=0.8)
        cluster = kg.get_concept_cluster("ML")
        assert cluster["concept"] == "ML"
        assert cluster["file_count"] >= 1

    def test_get_triple_stats_empty(self, kg):
        stats = kg.get_triple_stats()
        assert stats["total_triples"] == 0

    def test_get_entity_neighbors(self, kg):
        kg.add_triple("A", "knows", "B")
        kg.add_triple("B", "knows", "C")
        neighbors = kg.get_entity_neighbors("A", depth=2)
        assert "B" in neighbors
        assert "C" in neighbors


# ═══════════════════════════════════════════════════════════════════════════════
# DocumentEditor
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestDocumentEditor:
    """Tests for web.document_editor.DocumentEditor."""

    @pytest.fixture()
    def editor(self):
        from web.document_editor import DocumentEditor
        return DocumentEditor()

    def test_constructor(self, editor):
        assert editor is not None

    def test_edit_ppt_import_error(self):
        from web.document_editor import DocumentEditor
        with patch.dict("sys.modules", {"pptx": None, "pptx.util": None}):
            result = DocumentEditor.edit_ppt("nonexistent.pptx", [])
            assert result["success"] is False

    def test_edit_ppt_file_not_found(self):
        from web.document_editor import DocumentEditor
        mock_pptx = MagicMock()
        mock_pptx.Presentation.side_effect = FileNotFoundError("no file")
        with patch.dict("sys.modules", {"pptx": mock_pptx, "pptx.util": MagicMock()}):
            result = DocumentEditor.edit_ppt("missing.pptx", [{"slide_index": 0}])
            assert result["success"] is False

    def test_edit_excel_import_error(self):
        from web.document_editor import DocumentEditor
        with patch.dict("sys.modules", {"openpyxl": None}):
            result = DocumentEditor.edit_excel("fake.xlsx", [])
            assert result["success"] is False

    def test_parse_ai_suggestions_valid_json_block(self):
        from web.document_editor import DocumentEditor
        ai_text = '```json\n{"modifications": [{"action": "update_title"}]}\n```'
        mods = DocumentEditor.parse_ai_suggestions(ai_text)
        assert len(mods) == 1
        assert mods[0]["action"] == "update_title"

    def test_parse_ai_suggestions_bare_json(self):
        from web.document_editor import DocumentEditor
        ai_text = '{"modifications": [{"action": "add_content"}]}'
        mods = DocumentEditor.parse_ai_suggestions(ai_text)
        assert len(mods) == 1

    def test_parse_ai_suggestions_list_response(self):
        from web.document_editor import DocumentEditor
        ai_text = '[{"action": "delete"}]'
        mods = DocumentEditor.parse_ai_suggestions(ai_text)
        assert len(mods) == 1

    def test_parse_ai_suggestions_invalid_input(self):
        from web.document_editor import DocumentEditor
        mods = DocumentEditor.parse_ai_suggestions("not json at all")
        assert mods == []

    def test_parse_ai_suggestions_empty_modifications(self):
        from web.document_editor import DocumentEditor
        ai_text = '{"modifications": []}'
        mods = DocumentEditor.parse_ai_suggestions(ai_text)
        assert mods == []

    def test_edit_word_import_error(self):
        from web.document_editor import DocumentEditor
        with patch.dict("sys.modules", {"docx": None, "docx.oxml": None, "docx.oxml.ns": None}):
            result = DocumentEditor.edit_word("fake.docx", [])
            assert result["success"] is False

    def test_edit_ppt_with_mock_presentation(self):
        from web.document_editor import DocumentEditor
        mock_prs = MagicMock()
        mock_slide = MagicMock()
        mock_slide.shapes.title.text = "Old Title"
        mock_prs.slides.__getitem__ = MagicMock(return_value=mock_slide)
        mock_prs.slides.__len__ = MagicMock(return_value=2)

        mock_pptx = MagicMock()
        mock_pptx.Presentation.return_value = mock_prs

        mods = [{"slide_index": 0, "action": "update_title", "target": "title", "content": "New Title"}]
        with patch.dict("sys.modules", {"pptx": mock_pptx, "pptx.util": MagicMock()}):
            result = DocumentEditor.edit_ppt("test.pptx", mods)
        assert result["success"] is True
        assert result["applied_count"] >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# QualityEvaluator (PPTEvaluator + DocumentEvaluator + evaluate_quality)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestQualityEvaluator:
    """Tests for web.quality_evaluator module."""

    def test_ppt_evaluator_init(self):
        from web.quality_evaluator import PPTEvaluator
        ev = PPTEvaluator()
        assert ev.issues == []
        assert ev.suggestions == []

    def test_ppt_evaluator_missing_pptx_module(self):
        from web.quality_evaluator import PPTEvaluator
        ev = PPTEvaluator()
        with patch.dict("sys.modules", {"pptx": None}):
            result = ev.evaluate_pptx_file("fake.pptx")
        assert result.overall_score == 0
        assert any("不可用" in i for i in result.issues)

    def test_score_slide_count_optimal(self):
        from web.quality_evaluator import PPTEvaluator
        ev = PPTEvaluator()
        assert ev._score_slide_count(10) == 100.0

    def test_score_slide_count_too_few(self):
        from web.quality_evaluator import PPTEvaluator
        ev = PPTEvaluator()
        assert ev._score_slide_count(2) == 20.0

    def test_score_slide_count_too_many(self):
        from web.quality_evaluator import PPTEvaluator
        ev = PPTEvaluator()
        score = ev._score_slide_count(35)
        assert score == 60.0

    def test_prioritize_improvements(self):
        from web.quality_evaluator import PPTEvaluator
        ev = PPTEvaluator()
        scores = {"content_distribution": 50, "image_distribution": 60, "layout_consistency": 90}
        priorities = ev._prioritize_improvements(scores)
        assert len(priorities) <= 3
        assert any("内容" in p for p in priorities)

    def test_document_evaluator_init(self):
        from web.quality_evaluator import DocumentEvaluator
        ev = DocumentEvaluator()
        assert ev.issues == []

    def test_document_evaluator_good_document(self):
        from web.quality_evaluator import DocumentEvaluator
        ev = DocumentEvaluator()
        doc = (
            "# 标题\n\n"
            "## 第一部分\n\n" + "这是一个很好的文档内容。" * 50 + "\n\n"
            "## 第二部分\n\n" + "更多详细的内容。" * 50 + "\n\n"
            "## 结论\n\n总结内容。"
        )
        result = ev.evaluate_document(doc)
        assert result.overall_score > 60
        assert isinstance(result.category_scores, dict)

    def test_document_evaluator_short_document(self):
        from web.quality_evaluator import DocumentEvaluator
        ev = DocumentEvaluator()
        result = ev.evaluate_document("short")
        assert result.needs_improvement is True
        assert any("过短" in i for i in result.issues)

    def test_evaluate_structure_no_headings(self):
        from web.quality_evaluator import DocumentEvaluator
        ev = DocumentEvaluator()
        score = ev._evaluate_structure("no headings here at all")
        assert score == 40.0

    def test_evaluate_length_ranges(self):
        from web.quality_evaluator import DocumentEvaluator
        ev = DocumentEvaluator()
        assert ev._evaluate_length("A" * 2000) == 100.0
        assert ev._evaluate_length("A" * 800) == 75.0
        assert ev._evaluate_length("A" * 100) == 50.0

    def test_evaluate_quality_function_docx(self):
        from web.quality_evaluator import evaluate_quality
        result = evaluate_quality("docx", "# Title\n\n## Section\n\n" + "Content " * 200)
        assert "overall_score" in result
        assert isinstance(result["overall_score"], float)

    def test_evaluate_quality_function_ppt(self):
        from web.quality_evaluator import evaluate_quality
        with patch.dict("sys.modules", {"pptx": None}):
            result = evaluate_quality("pptx", "fake_path.pptx")
        assert result["overall_score"] == 0

    def test_evaluate_format_consistent(self):
        from web.quality_evaluator import DocumentEvaluator
        ev = DocumentEvaluator()
        content = "# Title\n\n- item1\n- item2\n\n```python\nprint('hi')\n```"
        score = ev._evaluate_format(content)
        assert score >= 80

    def test_evaluate_completeness(self):
        from web.quality_evaluator import DocumentEvaluator
        ev = DocumentEvaluator()
        content = "# Intro\n\n## Section\n\n详细内容\n\n## 结论\n\n总结"
        score = ev._evaluate_completeness(content)
        assert score > 60


# ═══════════════════════════════════════════════════════════════════════════════
# FileIndexer
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestFileIndexer:
    """Tests for web.file_indexer.FileIndexer."""

    @pytest.fixture()
    def indexer(self, tmp_path):
        db_path = tmp_path / "_index" / "file_index.db"
        from web.file_indexer import FileIndexer
        return FileIndexer(workspace_dir=str(tmp_path), db_path=str(db_path))

    def test_init_creates_db(self, indexer):
        assert Path(indexer.db_path).exists()

    def test_init_creates_tables(self, indexer):
        conn = sqlite3.connect(indexer.db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "file_index" in tables

    def test_compute_hash(self, indexer):
        h = indexer._compute_hash("hello")
        assert isinstance(h, str) and len(h) == 32

    def test_index_file_nonexistent(self, indexer):
        result = indexer.index_file("/nonexistent/file.txt")
        assert result["success"] is False

    def test_index_file_unsupported_extension(self, indexer, tmp_path):
        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG")
        result = indexer.index_file(str(img))
        assert result["success"] is False
        assert "不支持" in result["error"]

    def test_index_file_success(self, indexer, tmp_path):
        txt = tmp_path / "hello.txt"
        txt.write_text("Hello World", encoding="utf-8")
        result = indexer.index_file(str(txt))
        assert result["success"] is True
        assert result["indexed"] is True

    def test_index_file_unchanged_skips(self, indexer, tmp_path):
        txt = tmp_path / "same.txt"
        txt.write_text("constant", encoding="utf-8")
        indexer.index_file(str(txt))
        result = indexer.index_file(str(txt))
        assert result["success"] is True
        assert result["indexed"] is False

    def test_index_directory_empty(self, indexer, tmp_path):
        sub = tmp_path / "empty_dir"
        sub.mkdir()
        result = indexer.index_directory(str(sub))
        assert result["success"] is True
        assert result["total"] == 0

    def test_index_directory_with_files(self, indexer, tmp_path):
        sub = tmp_path / "docs"
        sub.mkdir()
        (sub / "a.py").write_text("print('a')", encoding="utf-8")
        (sub / "b.md").write_text("# B", encoding="utf-8")
        result = indexer.index_directory(str(sub), recursive=False)
        assert result["success"] is True
        assert result["indexed"] == 2

    def test_index_directory_nonexistent(self, indexer):
        result = indexer.index_directory("/no/such/dir")
        assert result["success"] is False

    def test_list_indexed_files(self, indexer, tmp_path):
        txt = tmp_path / "listed.txt"
        txt.write_text("some content", encoding="utf-8")
        indexer.index_file(str(txt))
        files = indexer.list_indexed_files()
        assert len(files) == 1
        assert files[0]["file_name"] == "listed.txt"

    def test_remove_file(self, indexer, tmp_path):
        txt = tmp_path / "removeme.txt"
        txt.write_text("bye", encoding="utf-8")
        indexer.index_file(str(txt))
        result = indexer.remove_file(str(txt.resolve()))
        assert result["success"] is True

    def test_get_file_info_found(self, indexer, tmp_path):
        txt = tmp_path / "info.txt"
        txt.write_text("data", encoding="utf-8")
        indexer.index_file(str(txt))
        info = indexer.get_file_info(str(txt.resolve()))
        assert info is not None
        assert info["file_name"] == "info.txt"

    def test_get_file_info_not_found(self, indexer):
        assert indexer.get_file_info("/nonexistent") is None

    def test_generate_snippet_found(self, indexer):
        snippet = indexer._generate_snippet("the quick brown fox", "brown")
        assert "**brown**" in snippet

    def test_generate_snippet_not_found(self, indexer):
        snippet = indexer._generate_snippet("hello world", "xyz")
        assert snippet.startswith("hello")

    def test_generate_snippet_empty(self, indexer):
        assert indexer._generate_snippet("", "xyz") == ""

    def test_rebuild_index(self, indexer, tmp_path):
        txt = tmp_path / "rebuild.txt"
        txt.write_text("rebuild me", encoding="utf-8")
        indexer.index_file(str(txt))
        result = indexer.rebuild_index()
        assert result["success"] is True
