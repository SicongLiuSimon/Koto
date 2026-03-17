# -*- coding: utf-8 -*-
"""
Comprehensive unit tests for low-coverage app/core modules.

Modules tested:
  1. FileRegistry    — file metadata registry (SQLite-backed)
  2. FileWatcher     — directory polling monitor
  3. TrainingDB      — local model training database
  4. ShadowTracer    — shadow trace recording for LoRA fine-tuning
  5. ConfigurationManager — system threshold configuration
  6. JobRunner       — background job executor
  7. OpsEventBus     — operational event bus
  8. OllamaLLMProvider — Ollama LLM provider
  9. RemediationManager — auto-remediation with approval workflow
"""

import json
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_file_registry_singleton():
    """Reset the global FileRegistry singleton between tests to avoid lock conflicts."""
    yield
    try:
        import app.core.file.file_registry as fr

        if fr._registry_instance is not None:
            try:
                fr._registry_instance._conn.close()
            except Exception:
                pass
            fr._registry_instance = None
    except Exception:
        pass


# ============================================================================
# 1. TestFileRegistry
# ============================================================================


@pytest.mark.unit
class TestFileRegistry:
    """Tests for app.core.file.file_registry.FileRegistry."""

    @pytest.fixture
    def registry(self, tmp_path):
        """Create a FileRegistry backed by a temp SQLite file."""
        db_path = str(tmp_path / "test_registry.sqlite")
        from app.core.file.file_registry import FileRegistry

        reg = FileRegistry(db_path=db_path)
        yield reg
        reg._conn.close()

    @pytest.fixture
    def sample_file(self, tmp_path):
        """Create a temp text file for registration."""
        f = tmp_path / "sample.txt"
        f.write_text("Hello Koto test content", encoding="utf-8")
        return str(f)

    def test_init_creates_db(self, tmp_path):
        """FileRegistry __init__ creates the SQLite database file."""
        db_path = str(tmp_path / "sub" / "test.sqlite")
        from app.core.file.file_registry import FileRegistry

        reg = FileRegistry(db_path=db_path)
        assert Path(db_path).exists()

    def test_register_new_file(self, registry, sample_file):
        """register() inserts a new file and returns a FileEntry."""
        entry = registry.register(sample_file, source="upload")
        assert entry is not None
        assert entry.name == "sample.txt"
        assert entry.ext == ".txt"
        assert entry.source == "upload"
        assert entry.file_id  # non-empty UUID

    def test_register_nonexistent_file_returns_none(self, registry):
        """register() returns None for a path that does not exist."""
        result = registry.register("/nonexistent/path/to/file.txt")
        assert result is None

    def test_register_updates_existing(self, registry, tmp_path):
        """register() updates mtime/hash when file content changes."""
        f = tmp_path / "changing.txt"
        f.write_text("version 1", encoding="utf-8")
        entry1 = registry.register(str(f), source="manual")
        assert entry1 is not None

        # Modify the file to change mtime and content
        time.sleep(0.05)
        f.write_text("version 2 with more content", encoding="utf-8")
        # Force mtime change
        os.utime(str(f), (time.time() + 2, time.time() + 2))

        entry2 = registry.register(str(f), source="manual")
        assert entry2 is not None
        assert entry2.file_id == entry1.file_id

    def test_get_by_path(self, registry, sample_file):
        """get_by_path() retrieves a previously registered file."""
        registry.register(sample_file)
        entry = registry.get_by_path(sample_file)
        assert entry is not None
        assert entry.path == sample_file

    def test_get_by_path_not_found(self, registry):
        """get_by_path() returns None for unregistered path."""
        assert registry.get_by_path("/no/such/file") is None

    def test_get_by_id(self, registry, sample_file):
        """get_by_id() retrieves file by its UUID."""
        entry = registry.register(sample_file)
        fetched = registry.get_by_id(entry.file_id)
        assert fetched is not None
        assert fetched.path == sample_file

    def test_search_by_name(self, registry, tmp_path):
        """search() finds files by name substring."""
        f1 = tmp_path / "report_2024.txt"
        f1.write_text("quarterly report", encoding="utf-8")
        registry.register(str(f1), source="scanner")

        results = registry.search("report")
        assert len(results) >= 1
        assert any("report" in r.name for r in results)

    def test_search_with_category_filter(self, registry, tmp_path):
        """search() respects the category filter."""
        f = tmp_path / "code.py"
        f.write_text("print('hello')", encoding="utf-8")
        registry.register(str(f), source="manual")

        results_code = registry.search("code", category="代码")
        results_doc = registry.search("code", category="文档")
        assert len(results_code) >= 1
        assert len(results_doc) == 0

    def test_delete_file(self, registry, sample_file):
        """delete() removes a registered file and returns True."""
        registry.register(sample_file)
        assert registry.delete(sample_file) is True
        assert registry.get_by_path(sample_file) is None

    def test_delete_nonexistent_returns_false(self, registry):
        """delete() returns False for a path not in the registry."""
        assert registry.delete("/no/such/path.txt") is False

    def test_count(self, registry, tmp_path):
        """count() returns correct number of registered files."""
        for i in range(3):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content {i}", encoding="utf-8")
            registry.register(str(f))
        assert registry.count() == 3

    def test_stats(self, registry, tmp_path):
        """stats() returns category-grouped summary."""
        f = tmp_path / "test.py"
        f.write_text("x = 1", encoding="utf-8")
        registry.register(str(f))
        s = registry.stats()
        assert "total" in s
        assert "by_category" in s
        assert s["total"] >= 1

    def test_update_path(self, registry, tmp_path):
        """update_path() changes the stored path for a file."""
        f = tmp_path / "old_name.txt"
        f.write_text("content", encoding="utf-8")
        registry.register(str(f))

        new_path = str(tmp_path / "new_name.txt")
        f.rename(new_path)
        assert registry.update_path(str(f), new_path) is True
        assert registry.get_by_path(new_path) is not None

    def test_add_and_get_tags(self, registry, sample_file):
        """add_tag() and get_tags() manage file tags."""
        registry.register(sample_file)
        assert registry.add_tag(sample_file, "important") is True
        assert registry.add_tag(sample_file, "urgent") is True
        tags = registry.get_tags(sample_file)
        assert "important" in tags
        assert "urgent" in tags

    def test_remove_tag(self, registry, sample_file):
        """remove_tag() deletes a specific tag."""
        registry.register(sample_file)
        registry.add_tag(sample_file, "temp")
        assert registry.remove_tag(sample_file, "temp") is True
        assert "temp" not in registry.get_tags(sample_file)

    def test_add_empty_tag_returns_false(self, registry, sample_file):
        """add_tag() rejects empty tags."""
        assert registry.add_tag(sample_file, "") is False
        assert registry.add_tag(sample_file, "   ") is False

    def test_favorites(self, registry, sample_file):
        """add_favorite / list_favorites / remove_favorite work correctly."""
        registry.register(sample_file)
        assert registry.add_favorite(sample_file) is True
        assert sample_file in registry.list_favorites()
        assert registry.remove_favorite(sample_file) is True
        assert sample_file not in registry.list_favorites()

    def test_log_op_and_get_op_log(self, registry):
        """log_op() records operations and get_op_log() retrieves them."""
        op_id = registry.log_op(
            "move", "/src/a.txt", "/dst/a.txt", meta={"reason": "cleanup"}
        )
        assert op_id
        log = registry.get_op_log(limit=5)
        assert len(log) >= 1
        assert log[0]["op_type"] == "move"

    def test_escape_fts_special_chars(self, registry):
        """_escape_fts() safely escapes user queries for FTS5."""
        assert registry._escape_fts("hello world") == '"hello" "world"'
        assert registry._escape_fts("") == '""'

    def test_batch_register(self, registry, tmp_path):
        """batch_register() returns (added, updated) counts."""
        paths = []
        for i in range(3):
            f = tmp_path / f"batch_{i}.txt"
            f.write_text(f"batch content {i}", encoding="utf-8")
            paths.append(str(f))
        added, updated = registry.batch_register(paths, source="scanner")
        assert added == 3
        assert updated == 0

    def test_list_recent(self, registry, sample_file):
        """list_recent() returns recently indexed files."""
        registry.register(sample_file)
        recent = registry.list_recent(days=1)
        assert len(recent) >= 1


# ============================================================================
# 2. TestFileWatcher
# ============================================================================


@pytest.mark.unit
class TestFileWatcher:
    """Tests for app.core.file.file_watcher.FileWatcher."""

    @pytest.fixture
    def watcher(self, tmp_path):
        """Create a FileWatcher with a temporary settings file."""
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "file_watcher": {
                        "enabled": True,
                        "watch_dirs": [str(tmp_path / "watched")],
                        "interval_seconds": 10,
                        "max_file_size_mb": 5,
                        "skip_extensions": [".bak"],
                    }
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "watched").mkdir()
        from app.core.file.file_watcher import FileWatcher

        return FileWatcher(settings_path=str(settings))

    def test_init_loads_config(self, watcher, tmp_path):
        """__init__ loads configuration from the settings file."""
        assert watcher.enabled is True
        assert len(watcher.watch_dirs) == 1
        assert str(tmp_path / "watched") in watcher.watch_dirs[0]

    def test_default_properties(self, tmp_path):
        """Properties return defaults when config file is absent."""
        from app.core.file.file_watcher import FileWatcher

        w = FileWatcher(settings_path=str(tmp_path / "nonexistent.json"))
        assert w.enabled is False
        assert w.watch_dirs == []
        assert w.interval >= 10

    def test_add_dir(self, watcher):
        """add_dir() appends a new directory to watch_dirs."""
        initial_count = len(watcher.watch_dirs)
        watcher.add_dir("C:/new_dir")
        assert len(watcher.watch_dirs) == initial_count + 1
        assert "C:/new_dir" in watcher.watch_dirs

    def test_add_dir_no_duplicate(self, watcher):
        """add_dir() does not add the same directory twice."""
        watcher.add_dir("C:/dup_dir")
        watcher.add_dir("C:/dup_dir")
        assert watcher.watch_dirs.count("C:/dup_dir") == 1

    def test_remove_dir(self, watcher):
        """remove_dir() removes a directory from the list."""
        watcher.add_dir("C:/to_remove")
        watcher.remove_dir("C:/to_remove")
        assert "C:/to_remove" not in watcher.watch_dirs

    def test_skip_exts_includes_defaults_and_custom(self, watcher):
        """skip_exts combines default and user-configured extensions."""
        exts = watcher.skip_exts
        assert ".tmp" in exts  # default
        assert ".bak" in exts  # custom from settings

    def test_max_file_size_bytes(self, watcher):
        """max_file_size_bytes converts MB config to bytes."""
        assert watcher.max_file_size_bytes == 5 * 1024 * 1024

    def test_start_and_stop(self, watcher):
        """start() creates a daemon thread, stop() joins it."""
        # Prevent the loop from actually scanning (which triggers global singleton)
        watcher._stop_event = threading.Event()
        watcher._stop_event.set()  # immediately stop the loop
        watcher._running = False
        # Manually test start logic
        from app.core.file.file_watcher import FileWatcher

        watcher._stop_event.clear()
        watcher._running = True
        watcher._thread = threading.Thread(target=lambda: None, daemon=True)
        watcher._thread.start()
        assert watcher._running is True
        assert watcher._thread is not None
        watcher.stop()
        assert watcher._running is False

    def test_start_skips_if_already_running(self, watcher):
        """start() is a no-op when already running."""
        # Simulate already running state
        watcher._running = True
        watcher._thread = threading.Thread(target=lambda: None, daemon=True)
        watcher._thread.start()
        thread1 = watcher._thread
        watcher.start()  # second call should be no-op
        assert watcher._thread is thread1
        watcher._running = False

    def test_stop_noop_when_not_running(self, tmp_path):
        """stop() does nothing when watcher is not started."""
        from app.core.file.file_watcher import FileWatcher

        w = FileWatcher(settings_path=str(tmp_path / "no.json"))
        w.stop()  # should not raise

    @patch("app.core.file.file_registry.get_file_registry")
    def test_scan_once(self, mock_get_reg, watcher, tmp_path):
        """scan_once() registers discovered files and returns count."""
        watched = tmp_path / "watched"
        (watched / "a.txt").write_text("aaa", encoding="utf-8")
        (watched / "b.md").write_text("bbb", encoding="utf-8")
        (watched / "c.tmp").write_text("skip me", encoding="utf-8")  # skipped ext

        mock_reg = MagicMock()
        mock_entry = MagicMock()
        mock_reg.register.return_value = mock_entry
        mock_get_reg.return_value = mock_reg

        count = watcher.scan_once(str(watched))
        # .tmp should be skipped, so expect 2 registrations
        assert count == 2

    @patch("app.core.file.file_registry.get_file_registry")
    def test_scan_once_empty_dir(self, mock_get_reg, watcher, tmp_path):
        """scan_once() returns 0 for an empty directory."""
        empty = tmp_path / "empty_dir"
        empty.mkdir()
        assert watcher.scan_once(str(empty)) == 0

    def test_scan_once_nonexistent_dir(self, watcher):
        """scan_once() returns 0 for a non-existent directory."""
        assert watcher.scan_once("/nonexistent/dir") == 0

    def test_reload_config_with_bad_json(self, tmp_path):
        """_reload_config handles corrupt JSON gracefully."""
        bad_settings = tmp_path / "bad.json"
        bad_settings.write_text("{invalid json!!", encoding="utf-8")
        from app.core.file.file_watcher import FileWatcher

        w = FileWatcher(settings_path=str(bad_settings))
        assert w.enabled is False


# ============================================================================
# 3. TestTrainingDB
# ============================================================================


@pytest.mark.unit
class TestTrainingDB:
    """Tests for app.core.learning.training_db.TrainingDB."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create a TrainingDB with a temp database."""
        db_path = tmp_path / "test_training.db"
        from app.core.learning.training_db import TrainingDB

        tdb = TrainingDB(db_path=db_path)
        yield tdb
        # Force WAL checkpoint to release file locks on Windows
        import sqlite3

        try:
            c = sqlite3.connect(str(db_path))
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()
        except Exception:
            pass

    @pytest.fixture
    def sample(self):
        from app.core.learning.training_db import DBSample

        return DBSample(
            user_input="帮我写一份周报",
            task_type="CHAT",
            confidence=0.92,
            source="synthetic",
            quality=0.90,
        )

    def test_init_creates_tables(self, db):
        """__init__ creates the samples and build_history tables."""
        with db._conn() as conn:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
        assert "samples" in tables
        assert "build_history" in tables
        assert "pending_corrections" in tables

    def test_upsert_inserts_new_sample(self, db, sample):
        """upsert() inserts a new sample and returns (True, 'inserted')."""
        inserted, action = db.upsert(sample)
        assert inserted is True
        assert action == "inserted"

    def test_upsert_skips_duplicate_lower_quality(self, db, sample):
        """upsert() skips duplicates with equal or lower quality."""
        db.upsert(sample)
        from app.core.learning.training_db import DBSample

        dup = DBSample(
            user_input="帮我写一份周报",
            task_type="CODER",
            confidence=0.80,
            source="synthetic",
            quality=0.50,  # lower quality
        )
        inserted, action = db.upsert(dup)
        assert inserted is False
        assert action == "skipped_lower_quality"

    def test_upsert_updates_higher_quality(self, db, sample):
        """upsert() updates when the new sample has higher quality."""
        db.upsert(sample)
        from app.core.learning.training_db import DBSample

        better = DBSample(
            user_input="帮我写一份周报",
            task_type="CODER",
            confidence=0.95,
            source="manual",
            quality=0.99,  # higher quality
        )
        inserted, action = db.upsert(better)
        assert inserted is False
        assert action == "updated"

    def test_upsert_batch(self, db):
        """upsert_batch() inserts multiple samples and returns stats."""
        from app.core.learning.training_db import DBSample

        samples = [
            DBSample(user_input=f"input {i}", task_type="CHAT", quality=0.90)
            for i in range(5)
        ]
        result = db.upsert_batch(samples)
        assert result["inserted"] == 5
        assert result["skipped"] == 0

    def test_correct_label_existing(self, db, sample):
        """correct_label() updates an existing sample's corrected_task."""
        db.upsert(sample)
        ok = db.correct_label("帮我写一份周报", "CODER", corrected_by="user")
        assert ok is True
        # Verify correction sticks
        actives = db.get_all_active()
        match = [s for s in actives if s.user_input == "帮我写一份周报"]
        assert len(match) == 1
        assert match[0].corrected_task == "CODER"

    def test_correct_label_new_sample(self, db):
        """correct_label() creates a new sample when input doesn't exist yet."""
        ok = db.correct_label("brand new input", "RESEARCH", corrected_by="admin")
        assert ok is True
        actives = db.get_all_active(min_quality=0.0)
        match = [s for s in actives if s.user_input == "brand new input"]
        assert len(match) == 1

    def test_correct_label_invalid_task_raises(self, db):
        """correct_label() raises ValueError for invalid task type."""
        with pytest.raises(ValueError, match="无效任务类型"):
            db.correct_label("some input", "INVALID_TASK")

    def test_log_prediction(self, db):
        """log_prediction() records a prediction and returns an ID."""
        pid = db.log_prediction("test input", "CHAT", session_id="s1")
        assert isinstance(pid, int)
        assert pid > 0

    def test_resolve_correction_with_correct_task(self, db):
        """resolve_correction() writes the correction into samples."""
        pid = db.log_prediction("resolve me", "CHAT")
        db.resolve_correction(pid, "CODER")
        actives = db.get_all_active(min_quality=0.0)
        match = [s for s in actives if s.user_input == "resolve me"]
        assert len(match) == 1

    def test_resolve_correction_none_task(self, db):
        """resolve_correction with None means prediction was correct (no sample added)."""
        pid = db.log_prediction("correct prediction", "CHAT")
        db.resolve_correction(pid, None)
        actives = db.get_all_active(min_quality=0.0)
        match = [s for s in actives if s.user_input == "correct prediction"]
        assert len(match) == 0

    def test_get_all_active(self, db):
        """get_all_active() returns active samples above quality threshold."""
        from app.core.learning.training_db import DBSample

        db.upsert(DBSample(user_input="high q", task_type="CHAT", quality=0.95))
        db.upsert(DBSample(user_input="low q", task_type="CHAT", quality=0.30))
        actives = db.get_all_active(min_quality=0.7)
        inputs = [s.user_input for s in actives]
        assert "high q" in inputs
        assert "low q" not in inputs

    def test_stats(self, db, sample):
        """stats() returns comprehensive statistics."""
        db.upsert(sample)
        s = db.stats()
        assert s["total"] >= 1
        assert "by_task" in s
        assert "by_source" in s
        assert "db_path" in s

    def test_get_unexported_count(self, db, sample):
        """get_unexported_count() counts samples not yet exported."""
        db.upsert(sample)
        assert db.get_unexported_count() >= 1

    def test_export_jsonl(self, db, sample, tmp_path):
        """export_jsonl() writes a JSONL file and marks samples exported."""
        db.upsert(sample)
        out_dir = tmp_path / "export"
        path = db.export_jsonl(output_dir=out_dir)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "CHAT" in content
        # After export, unexported count should be 0
        assert db.get_unexported_count() == 0

    def test_export_jsonl_empty_db_raises(self, db, tmp_path):
        """export_jsonl() raises RuntimeError on an empty database."""
        out_dir = tmp_path / "empty_export"
        with pytest.raises(RuntimeError, match="没有可导出的样本"):
            db.export_jsonl(output_dir=out_dir)

    def test_dbsample_effective_task(self):
        """DBSample.effective_task prefers corrected_task over task_type."""
        from app.core.learning.training_db import DBSample

        s = DBSample(user_input="x", task_type="CHAT", corrected_task="CODER")
        assert s.effective_task == "CODER"
        assert s.effective_confidence == 0.99

    def test_dbsample_effective_task_no_correction(self):
        """DBSample.effective_task uses task_type when no correction."""
        from app.core.learning.training_db import DBSample

        s = DBSample(user_input="x", task_type="CHAT")
        assert s.effective_task == "CHAT"
        assert s.effective_confidence == 0.90


# ============================================================================
# 4. TestShadowTracer
# ============================================================================


@pytest.mark.unit
class TestShadowTracer:
    """Tests for app.core.learning.shadow_tracer.ShadowTracer."""

    @pytest.fixture(autouse=True)
    def setup_tracer(self, tmp_path, monkeypatch):
        """Redirect ShadowTracer to a temp directory and reset state."""
        from app.core.learning.shadow_tracer import ShadowTracer

        self.tracer = ShadowTracer
        # Redirect _traces_dir to tmp_path
        monkeypatch.setattr(
            ShadowTracer, "_traces_dir", classmethod(lambda cls: tmp_path)
        )
        # Reset class state
        self.tracer.recording_enabled = True
        self.tracer._listeners = []
        self.tmp_path = tmp_path

    def test_record_approved_returns_trace_id(self):
        """record_approved() returns a non-None trace_id when enabled."""
        trace_id = self.tracer.record_approved(
            session_id="s1",
            user_input="hello",
            ai_response="hi there",
            skill_id="test_skill",
        )
        assert trace_id is not None
        assert len(trace_id) == 36  # UUID format

    def test_record_approved_disabled_returns_none(self):
        """record_approved() returns None when recording is disabled."""
        self.tracer.recording_enabled = False
        result = self.tracer.record_approved("s1", "hello", "response")
        assert result is None

    def test_record_adopted(self):
        """record_adopted() records with 'adopted' feedback type."""
        trace_id = self.tracer.record_adopted(
            session_id="s2",
            user_input="copy this",
            ai_response="content to copy",
        )
        assert trace_id is not None

    def test_record_workflow(self):
        """record_workflow() records multi-step workflows."""
        steps = [
            {"input": "step1", "output": "result1"},
            {"input": "step2", "output": "result2"},
        ]
        trace_id = self.tracer.record_workflow(
            session_id="s3",
            steps=steps,
            skill_id="code_review",
        )
        assert trace_id is not None

    def test_get_counts_empty(self):
        """get_counts() returns empty dict when no manifest exists."""
        counts = self.tracer.get_counts()
        assert counts == {}

    def test_get_counts_with_manifest(self):
        """get_counts() reads from the manifest file."""
        manifest = self.tmp_path / "_manifest.json"
        manifest.write_text(json.dumps({"test_skill": 10}), encoding="utf-8")
        counts = self.tracer.get_counts()
        assert counts["test_skill"] == 10

    def test_get_traces_empty(self):
        """get_traces() returns empty list for nonexistent skill."""
        assert self.tracer.get_traces(skill_id="nonexistent") == []

    def test_get_traces_reads_jsonl(self):
        """get_traces() reads JSONL records for a skill."""
        trace_file = self.tmp_path / "my_skill.jsonl"
        records = [
            json.dumps({"trace_id": str(uuid.uuid4()), "feedback": "thumbs_up"}),
            json.dumps({"trace_id": str(uuid.uuid4()), "feedback": "adopted"}),
        ]
        trace_file.write_text("\n".join(records) + "\n", encoding="utf-8")
        result = self.tracer.get_traces(skill_id="my_skill", limit=10)
        assert len(result) == 2
        # Most recent first
        assert result[0]["feedback"] == "adopted"

    def test_add_listener(self):
        """add_listener() registers a callback."""
        cb = Mock()
        self.tracer.add_listener(cb)
        assert cb in self.tracer._listeners

    def test_clear_traces(self):
        """clear_traces() deletes the trace file."""
        trace_file = self.tmp_path / "to_clear.jsonl"
        trace_file.write_text('{"data":"test"}\n', encoding="utf-8")
        self.tracer.clear_traces(skill_id="to_clear")
        assert not trace_file.exists()

    def test_trace_record_to_jsonl_line(self):
        """TraceRecord.to_jsonl_line() produces valid JSON."""
        from app.core.learning.shadow_tracer import TraceRecord

        rec = TraceRecord(
            session_id="s1",
            user_input="test input",
            ai_response="test response",
            feedback="thumbs_up",
        )
        line = rec.to_jsonl_line()
        parsed = json.loads(line)
        assert parsed["session_id"] == "s1"
        assert parsed["feedback"] == "thumbs_up"

    def test_trace_file_safe_name(self):
        """_trace_file() sanitizes special characters in skill_id."""
        tf = self.tracer._trace_file("my/skill*name")
        assert "*" not in tf.name
        assert "/" not in tf.stem

    def test_fire_training_ready_calls_listeners(self):
        """_fire_training_ready() invokes all registered listeners."""
        cb = Mock()
        self.tracer._listeners = [cb]
        self.tracer._fire_training_ready("skill_x", 10)
        cb.assert_called_once()
        args = cb.call_args[0]
        assert args[1] == "skill_x"
        assert args[2] == 10


# ============================================================================
# 5. TestConfigurationManager
# ============================================================================


@pytest.mark.unit
class TestConfigurationManager:
    """Tests for app.core.config.configuration_manager.ConfigurationManager."""

    @pytest.fixture
    def cm(self):
        from app.core.config.configuration_manager import ConfigurationManager

        return ConfigurationManager()

    def test_init_has_default_thresholds(self, cm):
        """ConfigurationManager initializes with default thresholds."""
        assert "cpu" in cm.thresholds
        assert "memory" in cm.thresholds
        assert cm.thresholds["cpu"]["warning"] == 70
        assert cm.thresholds["cpu"]["critical"] == 85

    def test_set_threshold_success(self, cm):
        """set_threshold() updates a known metric's level."""
        assert cm.set_threshold("cpu", "warning", 80) is True
        assert cm.get_threshold("cpu", "warning") == 80

    def test_set_threshold_unknown_metric(self, cm):
        """set_threshold() returns False for unknown metrics."""
        assert cm.set_threshold("unknown_metric", "warning", 50) is False

    def test_set_threshold_invalid_level(self, cm):
        """set_threshold() returns False for invalid severity levels."""
        assert cm.set_threshold("cpu", "extreme", 50) is False

    def test_get_threshold_full(self, cm):
        """get_threshold() without level returns the full dict."""
        t = cm.get_threshold("cpu")
        assert isinstance(t, dict)
        assert "warning" in t
        assert "critical" in t

    def test_get_threshold_unknown_returns_none(self, cm):
        """get_threshold() returns None for unknown metrics."""
        assert cm.get_threshold("nonexistent") is None

    def test_get_all_thresholds(self, cm):
        """get_all_thresholds() returns all configured thresholds."""
        all_t = cm.get_all_thresholds()
        assert len(all_t) == len(cm.DEFAULT_THRESHOLDS)

    def test_reset_threshold(self, cm):
        """reset_threshold() restores a metric to its default."""
        cm.set_threshold("cpu", "warning", 99)
        assert cm.reset_threshold("cpu") is True
        assert cm.get_threshold("cpu", "warning") == 70

    def test_reset_threshold_unknown(self, cm):
        """reset_threshold() returns False for unknown metrics."""
        assert cm.reset_threshold("bogus") is False

    def test_reset_all_thresholds(self, cm):
        """reset_all_thresholds() restores all to defaults."""
        cm.set_threshold("cpu", "warning", 10)
        cm.set_threshold("memory", "critical", 10)
        assert cm.reset_all_thresholds() is True
        assert cm.get_threshold("cpu", "warning") == 70
        assert cm.get_threshold("memory", "critical") == 90

    def test_set_and_get_setting(self, cm):
        """set_setting() and get_setting() manage arbitrary settings."""
        cm.set_setting("refresh_rate", 30)
        assert cm.get_setting("refresh_rate") == 30

    def test_get_setting_default(self, cm):
        """get_setting() returns default when key doesn't exist."""
        assert cm.get_setting("missing_key", "default_val") == "default_val"

    def test_get_all_settings(self, cm):
        """get_all_settings() returns all custom settings."""
        cm.set_setting("a", 1)
        cm.set_setting("b", 2)
        settings = cm.get_all_settings()
        assert settings["a"] == 1
        assert settings["b"] == 2

    def test_export_import_config(self, cm):
        """export_config() and import_config() round-trip correctly."""
        cm.set_threshold("cpu", "warning", 55)
        cm.set_setting("mode", "production")
        exported = cm.export_config()
        assert isinstance(exported, str)

        # Import into a fresh instance
        from app.core.config.configuration_manager import ConfigurationManager

        cm2 = ConfigurationManager()
        assert cm2.import_config(exported) is True
        assert cm2.get_threshold("cpu", "warning") == 55
        assert cm2.get_setting("mode") == "production"

    def test_import_config_bad_json(self, cm):
        """import_config() returns False for invalid JSON."""
        assert cm.import_config("not valid json{{{") is False

    def test_validate_metric_value_normal(self, cm):
        """validate_metric_value() returns normal status for low values."""
        result = cm.validate_metric_value("cpu", 30)
        assert result["valid"] is True
        assert result["status"] == "normal"

    def test_validate_metric_value_warning(self, cm):
        """validate_metric_value() returns warning for moderate values."""
        result = cm.validate_metric_value("cpu", 75)
        assert result["valid"] is False
        assert result["status"] == "warning"

    def test_validate_metric_value_critical(self, cm):
        """validate_metric_value() returns critical for high values."""
        result = cm.validate_metric_value("cpu", 90)
        assert result["valid"] is False
        assert result["status"] == "critical"

    def test_validate_metric_value_unknown(self, cm):
        """validate_metric_value() returns 'unknown' for unmapped metrics."""
        result = cm.validate_metric_value("nonexistent", 50)
        assert result["status"] == "unknown"


# ============================================================================
# 6. TestJobRunner
# ============================================================================


@pytest.mark.unit
class TestJobRunner:
    """Tests for app.core.jobs.job_runner.JobRunner."""

    @pytest.fixture
    def runner(self):
        from app.core.jobs.job_runner import JobRunner

        return JobRunner(max_workers=2)

    def test_init(self, runner):
        """JobRunner initializes with given max_workers."""
        assert runner._max_workers == 2
        assert runner._running is False
        assert runner._handlers == {}

    def test_register_handler(self, runner):
        """register_handler() stores a callable for a job_type."""
        handler = Mock()
        runner.register_handler("test_job", handler)
        assert "test_job" in runner._handlers
        assert runner._handlers["test_job"] is handler

    def test_start_and_stop(self, runner):
        """start() launches dispatcher thread, stop() shuts down."""
        with patch("app.core.jobs.job_runner.JobRunner._recover_stale_tasks"):
            runner.start()
            assert runner._running is True
            assert runner._dispatcher_thread is not None
            runner.stop()
            assert runner._running is False

    @patch("app.core.tasks.task_ledger.get_ledger")
    def test_submit_creates_task(self, mock_get_ledger, runner):
        """submit() creates a TaskLedger entry and returns task_id."""
        mock_ledger = MagicMock()
        mock_task = MagicMock()
        mock_task.task_id = "tid-123"
        mock_ledger.create.return_value = mock_task
        mock_get_ledger.return_value = mock_ledger

        from app.core.jobs.job_runner import JobSpec

        spec = JobSpec(
            job_type="agent_query",
            payload={"query": "test"},
            session_id="sess-1",
        )
        task_id = runner.submit(spec)
        assert task_id == "tid-123"
        mock_ledger.create.assert_called_once()

    def test_jobspec_defaults(self):
        """JobSpec has sensible defaults."""
        from app.core.jobs.job_runner import JobSpec

        spec = JobSpec(job_type="test")
        assert spec.payload == {}
        assert spec.max_retries == 0
        assert spec.timeout_seconds == 300.0
        assert spec.session_id == ""

    def test_jobcontext_is_cancelled(self):
        """JobContext.is_cancelled() delegates to ledger."""
        from app.core.jobs.job_runner import JobContext

        mock_ledger = MagicMock()
        mock_ledger.is_cancel_requested.return_value = True
        ctx = JobContext(
            task_id="t1",
            session_id="s1",
            payload={},
            ledger=mock_ledger,
            bus=MagicMock(),
        )
        assert ctx.is_cancelled() is True
        mock_ledger.is_cancel_requested.assert_called_with("t1")

    def test_jobcontext_is_interrupted(self):
        """JobContext.is_interrupted() delegates to ledger."""
        from app.core.jobs.job_runner import JobContext

        mock_ledger = MagicMock()
        mock_ledger.is_interrupt_requested.return_value = False
        ctx = JobContext(
            task_id="t1",
            session_id="s1",
            payload={},
            ledger=mock_ledger,
            bus=MagicMock(),
        )
        assert ctx.is_interrupted() is False

    def test_jobcontext_step(self):
        """JobContext.step() calls both ledger.add_step and bus.publish_step."""
        from app.core.jobs.job_runner import JobContext

        mock_ledger = MagicMock()
        mock_bus = MagicMock()
        ctx = JobContext(
            task_id="t1",
            session_id="s1",
            payload={},
            ledger=mock_ledger,
            bus=mock_bus,
        )
        ctx.step("THOUGHT", "thinking...", progress=10)
        mock_ledger.add_step.assert_called_once()
        mock_bus.publish_step.assert_called_once()

    @patch("app.core.tasks.progress_bus.get_progress_bus")
    @patch("app.core.tasks.task_ledger.get_ledger")
    def test_run_job_unknown_type_marks_failed(
        self, mock_get_ledger, mock_get_bus, runner
    ):
        """_run_job() marks the task as failed for unknown job_type."""
        mock_ledger = MagicMock()
        mock_get_ledger.return_value = mock_ledger
        mock_get_bus.return_value = MagicMock()

        from app.core.jobs.job_runner import JobSpec

        spec = JobSpec(job_type="unknown_type", payload={})
        runner._run_job("tid-1", spec)
        mock_ledger.mark_failed.assert_called_once()

    @patch("app.core.tasks.progress_bus.get_progress_bus")
    @patch("app.core.tasks.task_ledger.get_ledger")
    def test_run_job_success(self, mock_get_ledger, mock_get_bus, runner):
        """_run_job() marks the task as completed on handler success."""
        mock_ledger = MagicMock()
        mock_ledger.is_cancel_requested.return_value = False
        mock_get_ledger.return_value = mock_ledger
        mock_get_bus.return_value = MagicMock()

        handler = Mock(return_value="done!")
        runner.register_handler("test_type", handler)

        from app.core.jobs.job_runner import JobSpec

        spec = JobSpec(job_type="test_type", payload={"x": 1}, session_id="s1")
        runner._run_job("tid-2", spec)
        mock_ledger.mark_running.assert_called_with("tid-2")
        mock_ledger.mark_completed.assert_called_once()

    @patch("app.core.tasks.progress_bus.get_progress_bus")
    @patch("app.core.tasks.task_ledger.get_ledger")
    def test_run_job_handler_exception(self, mock_get_ledger, mock_get_bus, runner):
        """_run_job() handles handler exceptions by marking task failed."""
        mock_ledger = MagicMock()
        mock_task = MagicMock()
        mock_task.retry_count = 0
        mock_task.metadata = '{"max_retries": 0}'
        mock_ledger.get.return_value = mock_task
        mock_get_ledger.return_value = mock_ledger
        mock_get_bus.return_value = MagicMock()

        handler = Mock(side_effect=RuntimeError("boom"))
        runner.register_handler("fail_type", handler)

        from app.core.jobs.job_runner import JobSpec

        spec = JobSpec(job_type="fail_type", payload={})
        runner._run_job("tid-3", spec)
        mock_ledger.mark_failed.assert_called_once()


# ============================================================================
# 7. TestOpsEventBus
# ============================================================================


@pytest.mark.unit
class TestOpsEventBus:
    """Tests for app.core.ops.ops_event_bus.OpsEventBus."""

    @pytest.fixture
    def bus(self):
        from app.core.ops.ops_event_bus import OpsEventBus

        return OpsEventBus()

    def test_init_empty(self, bus):
        """OpsEventBus starts with empty history and handlers."""
        assert len(bus._history) == 0
        assert len(bus._handlers) == 0

    def test_emit_stores_event(self, bus):
        """emit() adds an event to the history."""
        bus.emit("test_event", {"key": "value"})
        assert len(bus._history) == 1
        assert bus._history[0].event_type == "test_event"

    def test_emit_with_severity(self, bus):
        """emit() stores events with the specified severity."""
        bus.emit("error_event", {"msg": "bad"}, severity="error")
        assert bus._history[0].severity == "error"

    def test_subscribe_and_emit(self, bus):
        """subscribe() registers a handler that gets called on emit."""
        handler = Mock()
        bus.subscribe("my_event", handler)
        bus.emit("my_event", {"data": 1})
        # Handler is called in a separate thread, wait a bit
        time.sleep(0.2)
        handler.assert_called_once()
        event_arg = handler.call_args[0][0]
        assert event_arg.event_type == "my_event"

    def test_subscribe_wildcard(self, bus):
        """subscribe('*', handler) receives all event types."""
        handler = Mock()
        bus.subscribe("*", handler)
        bus.emit("event_a", {})
        bus.emit("event_b", {})
        time.sleep(0.3)
        assert handler.call_count == 2

    def test_unsubscribe(self, bus):
        """unsubscribe() removes a handler."""
        handler = Mock()
        bus.subscribe("evt", handler)
        bus.unsubscribe("evt", handler)
        bus.emit("evt", {})
        time.sleep(0.1)
        handler.assert_not_called()

    def test_unsubscribe_nonexistent_handler(self, bus):
        """unsubscribe() silently ignores handlers not in the list."""
        handler = Mock()
        bus.unsubscribe("evt", handler)  # should not raise

    def test_get_recent(self, bus):
        """get_recent() returns events in reverse chronological order."""
        for i in range(5):
            bus.emit(f"event_{i}", {"i": i})
        recent = bus.get_recent(n=3)
        assert len(recent) == 3
        # Most recent first
        assert recent[0].event_type == "event_4"

    def test_get_by_type(self, bus):
        """get_by_type() filters events by type."""
        bus.emit("alpha", {})
        bus.emit("beta", {})
        bus.emit("alpha", {"second": True})
        result = bus.get_by_type("alpha")
        assert len(result) == 2
        assert all(e.event_type == "alpha" for e in result)

    def test_get_stats(self, bus):
        """get_stats() returns event count by type."""
        bus.emit("a", {})
        bus.emit("a", {})
        bus.emit("b", {})
        stats = bus.get_stats()
        assert stats["total"] == 3
        assert stats["by_type"]["a"] == 2
        assert stats["by_type"]["b"] == 1

    def test_ops_event_to_dict(self):
        """OpsEvent.to_dict() serializes all fields."""
        from app.core.ops.ops_event_bus import OpsEvent

        event = OpsEvent(
            event_type="test",
            detail={"k": "v"},
            severity="warning",
            source="unit_test",
        )
        d = event.to_dict()
        assert d["event_type"] == "test"
        assert d["severity"] == "warning"
        assert d["source"] == "unit_test"

    def test_history_max_limit(self):
        """History is bounded by _HISTORY_MAX."""
        from app.core.ops.ops_event_bus import _HISTORY_MAX, OpsEventBus

        bus = OpsEventBus()
        for i in range(_HISTORY_MAX + 50):
            bus.emit(f"overflow_{i}", {})
        assert len(bus._history) == _HISTORY_MAX

    def test_handler_exception_does_not_crash(self, bus):
        """Handler exceptions are caught and don't crash the bus."""
        bad_handler = Mock(side_effect=RuntimeError("handler crash"))
        bus.subscribe("crash_event", bad_handler)
        bus.emit("crash_event", {})
        time.sleep(0.2)
        # Should not raise, event still in history
        assert len(bus._history) == 1


# ============================================================================
# 8. TestOllamaLLMProvider
# ============================================================================


@pytest.mark.unit
class TestOllamaLLMProvider:
    """Tests for app.core.llm.ollama_llm_provider module."""

    def test_init_defaults(self):
        """OllamaLLMProvider initializes with default values."""
        from app.core.llm.ollama_llm_provider import OllamaLLMProvider

        provider = OllamaLLMProvider(model="qwen3:8b")
        assert provider.model == "qwen3:8b"
        assert "localhost" in provider.base_url
        assert provider._options["temperature"] == 0.7

    def test_init_custom_params(self):
        """OllamaLLMProvider accepts custom parameters."""
        from app.core.llm.ollama_llm_provider import OllamaLLMProvider

        provider = OllamaLLMProvider(
            model="gemma3:4b",
            base_url="http://myhost:11434",
            temperature=0.3,
            num_predict=2048,
        )
        assert provider.model == "gemma3:4b"
        assert provider.base_url == "http://myhost:11434"
        assert provider._options["temperature"] == 0.3
        assert provider._options["num_predict"] == 2048

    def test_get_token_count_returns_zero(self):
        """get_token_count() always returns 0 (no endpoint available)."""
        from app.core.llm.ollama_llm_provider import OllamaLLMProvider

        provider = OllamaLLMProvider(model="test")
        assert provider.get_token_count("hello world", "test") == 0

    def test_to_ollama_messages_string_prompt(self):
        """_to_ollama_messages() converts string prompt to messages list."""
        from app.core.llm.ollama_llm_provider import _to_ollama_messages

        msgs = _to_ollama_messages("hello", "You are a helper")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are a helper"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "hello"

    def test_to_ollama_messages_list_prompt(self):
        """_to_ollama_messages() converts message list prompt."""
        from app.core.llm.ollama_llm_provider import _to_ollama_messages

        prompt = [
            {"role": "user", "content": "question"},
            {"role": "model", "content": "answer"},  # should become "assistant"
        ]
        msgs = _to_ollama_messages(prompt, None)
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_to_ollama_messages_no_system(self):
        """_to_ollama_messages() omits system message when None."""
        from app.core.llm.ollama_llm_provider import _to_ollama_messages

        msgs = _to_ollama_messages("hi", None)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"

    def test_to_ollama_tools_none(self):
        """_to_ollama_tools() returns None for empty/None input."""
        from app.core.llm.ollama_llm_provider import _to_ollama_tools

        assert _to_ollama_tools(None) is None
        assert _to_ollama_tools([]) is None

    def test_to_ollama_tools_conversion(self):
        """_to_ollama_tools() converts tool defs to Ollama format."""
        from app.core.llm.ollama_llm_provider import _to_ollama_tools

        tools = [{"name": "search", "description": "Search the web"}]
        result = _to_ollama_tools(tools)
        assert result is not None
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "search"

    def test_to_ollama_tools_skips_invalid(self):
        """_to_ollama_tools() skips tools without a name."""
        from app.core.llm.ollama_llm_provider import _to_ollama_tools

        tools = [{"description": "no name"}, {"name": "valid", "description": "ok"}]
        result = _to_ollama_tools(tools)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "valid"

    def test_parse_ollama_response(self):
        """_parse_ollama_response() extracts content, tool_calls, usage."""
        from app.core.llm.ollama_llm_provider import _parse_ollama_response

        resp = {
            "message": {"content": "Hello!", "tool_calls": None},
            "prompt_eval_count": 10,
            "eval_count": 20,
        }
        result = _parse_ollama_response(resp)
        assert result["content"] == "Hello!"
        assert result["tool_calls"] == []
        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["completion_tokens"] == 20

    def test_parse_ollama_response_with_tool_calls(self):
        """_parse_ollama_response() parses tool_calls correctly."""
        from app.core.llm.ollama_llm_provider import _parse_ollama_response

        resp = {
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "search", "arguments": {"q": "test"}}}
                ],
            }
        }
        result = _parse_ollama_response(resp)
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "search"
        assert result["tool_calls"][0]["args"] == {"q": "test"}

    def test_parse_ollama_response_string_args(self):
        """_parse_ollama_response() parses string-encoded tool arguments."""
        from app.core.llm.ollama_llm_provider import _parse_ollama_response

        resp = {
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "calc", "arguments": '{"expr": "1+1"}'}}
                ],
            }
        }
        result = _parse_ollama_response(resp)
        assert result["tool_calls"][0]["args"] == {"expr": "1+1"}

    @patch("app.core.llm.ollama_llm_provider._raw_post")
    def test_generate_content_non_stream(self, mock_post):
        """generate_content() calls Ollama API and returns parsed response."""
        from app.core.llm.ollama_llm_provider import OllamaLLMProvider

        mock_post.return_value = {
            "message": {"content": "Test response"},
            "prompt_eval_count": 5,
            "eval_count": 10,
        }
        provider = OllamaLLMProvider(model="qwen3:8b")
        result = provider.generate_content("What is 2+2?")
        assert result["content"] == "Test response"
        mock_post.assert_called_once()

    @patch("app.core.llm.ollama_llm_provider._raw_post")
    def test_generate_content_with_skill_preamble(self, mock_post):
        """generate_content() injects skill preamble when Skills marker present."""
        from app.core.llm.ollama_llm_provider import (
            _SKILL_BLOCK_MARKER,
            OllamaLLMProvider,
        )

        mock_post.return_value = {
            "message": {"content": "response"},
        }
        provider = OllamaLLMProvider(model="test")
        sys_instr = f"Some instruction\n{_SKILL_BLOCK_MARKER}\n## Skill A"
        provider.generate_content("hello", system_instruction=sys_instr)

        call_args = mock_post.call_args
        payload = call_args[0][1]
        # System message should include the preamble
        sys_msg = payload["messages"][0]["content"]
        assert "Koto AI" in sys_msg

    def test_resolve_model_explicit(self):
        """_resolve_model() returns the explicit model when set."""
        from app.core.llm.ollama_llm_provider import OllamaLLMProvider

        provider = OllamaLLMProvider(model="explicit:model")
        assert provider._resolve_model() == "explicit:model"

    def test_base_url_trailing_slash_stripped(self):
        """Base URL trailing slash is stripped."""
        from app.core.llm.ollama_llm_provider import OllamaLLMProvider

        provider = OllamaLLMProvider(model="m", base_url="http://host:1234/")
        assert provider.base_url == "http://host:1234"


# ============================================================================
# 9. TestRemediationManager
# ============================================================================


@pytest.mark.unit
class TestRemediationManager:
    """Tests for app.core.remediation.remediation_manager.RemediationManager."""

    @pytest.fixture
    def mgr(self):
        from app.core.remediation.remediation_manager import RemediationManager

        return RemediationManager()

    def test_create_action(self, mgr):
        """create_action() returns an action ID and stores the action."""
        aid = mgr.create_action(
            event_id=1,
            action_type="restart_service",
            description="Restart the web server",
            risk_level="low",
        )
        assert aid is not None
        assert aid in mgr.actions

    def test_get_action(self, mgr):
        """get_action() returns action details as a dict."""
        aid = mgr.create_action(1, "clear_cache", "Clear app cache")
        result = mgr.get_action(aid)
        assert result is not None
        assert result["action_type"] == "clear_cache"
        assert result["status"] == "pending"

    def test_get_action_not_found(self, mgr):
        """get_action() returns None for unknown action ID."""
        assert mgr.get_action("nonexistent") is None

    def test_approve_action(self, mgr):
        """approve_action() transitions from pending to approved."""
        aid = mgr.create_action(1, "restart", "Restart service")
        assert mgr.approve_action(aid, reason="looks good") is True
        action_dict = mgr.get_action(aid)
        assert action_dict["status"] == "approved"

    def test_approve_non_pending_fails(self, mgr):
        """approve_action() fails for already-approved actions."""
        aid = mgr.create_action(1, "restart", "Restart")
        mgr.approve_action(aid)
        assert mgr.approve_action(aid) is False  # already approved

    def test_approve_unknown_action(self, mgr):
        """approve_action() returns False for unknown action ID."""
        assert mgr.approve_action("fake_id") is False

    def test_reject_action(self, mgr):
        """reject_action() transitions from pending to rejected."""
        aid = mgr.create_action(1, "delete_logs", "Delete old logs")
        assert mgr.reject_action(aid, reason="too risky") is True
        action_dict = mgr.get_action(aid)
        assert action_dict["status"] == "rejected"

    def test_reject_non_pending_fails(self, mgr):
        """reject_action() fails for non-pending actions."""
        aid = mgr.create_action(1, "restart", "Restart")
        mgr.approve_action(aid)
        assert mgr.reject_action(aid) is False

    def test_get_pending_actions(self, mgr):
        """get_pending_actions() returns only pending actions."""
        aid1 = mgr.create_action(1, "type_a", "desc_a")
        aid2 = mgr.create_action(2, "type_b", "desc_b")
        mgr.approve_action(aid2)
        pending = mgr.get_pending_actions()
        assert len(pending) == 1
        assert pending[0]["id"] == aid1

    def test_get_all_actions(self, mgr):
        """get_all_actions() returns all actions."""
        mgr.create_action(1, "a", "desc")
        mgr.create_action(2, "b", "desc")
        all_actions = mgr.get_all_actions()
        assert len(all_actions) == 2

    def test_get_all_actions_filter_by_status(self, mgr):
        """get_all_actions(status=...) filters by status."""
        aid1 = mgr.create_action(1, "a", "desc")
        aid2 = mgr.create_action(2, "b", "desc")
        mgr.approve_action(aid2)
        approved = mgr.get_all_actions(status="approved")
        assert len(approved) == 1
        assert approved[0]["id"] == aid2

    def test_get_all_actions_invalid_status(self, mgr):
        """get_all_actions() returns empty list for invalid status."""
        mgr.create_action(1, "a", "desc")
        result = mgr.get_all_actions(status="nonexistent_status")
        assert result == []

    def test_execute_action(self, mgr):
        """execute_action() starts execution for an approved action."""
        aid = mgr.create_action(1, "restart", "Restart service")
        mgr.approve_action(aid)
        assert mgr.execute_action(aid) is True
        # The action should be in EXECUTING state immediately
        action_dict = mgr.get_action(aid)
        assert action_dict["status"] == "executing"

    def test_execute_non_approved_fails(self, mgr):
        """execute_action() fails for non-approved actions."""
        aid = mgr.create_action(1, "restart", "Restart")
        assert mgr.execute_action(aid) is False  # still pending

    def test_execute_unknown_action(self, mgr):
        """execute_action() returns False for unknown action IDs."""
        assert mgr.execute_action("fake") is False

    def test_get_stats(self, mgr):
        """get_stats() returns status breakdown."""
        mgr.create_action(1, "a", "desc")
        mgr.create_action(2, "b", "desc")
        aid3 = mgr.create_action(3, "c", "desc")
        mgr.approve_action(aid3)
        stats = mgr.get_stats()
        assert stats["total_actions"] == 3
        assert stats["by_status"]["pending"] == 2
        assert stats["by_status"]["approved"] == 1

    def test_remediation_action_lifecycle(self, mgr):
        """Full lifecycle: create → approve → execute → complete."""
        from app.core.remediation.remediation_manager import RemediationAction

        action = RemediationAction(
            event_id=99,
            action_type="fix",
            description="Fix issue",
        )
        assert action.approve("approved") is True
        assert action.start_execution() is True
        assert action.complete(True, "Fixed successfully") is True
        assert action.status.value == "success"
        assert action.result == "Fixed successfully"

    def test_remediation_action_complete_failure(self):
        """RemediationAction marks failure correctly."""
        from app.core.remediation.remediation_manager import RemediationAction

        action = RemediationAction(event_id=1, action_type="fix", description="Try")
        action.approve()
        action.start_execution()
        assert action.complete(False, "Something went wrong") is True
        assert action.status.value == "failed"

    def test_remediation_action_to_dict(self):
        """RemediationAction.to_dict() serializes all fields."""
        from app.core.remediation.remediation_manager import RemediationAction

        action = RemediationAction(
            event_id=5,
            action_type="scale_up",
            description="Scale up workers",
            risk_level="medium",
        )
        d = action.to_dict()
        assert d["event_id"] == 5
        assert d["action_type"] == "scale_up"
        assert d["risk_level"] == "medium"
        assert d["status"] == "pending"
        assert "created_at" in d

    def test_execute_async_completes(self, mgr):
        """_execute_async() marks the action as completed after running."""
        aid = mgr.create_action(1, "fix", "Auto fix")
        mgr.approve_action(aid)
        mgr.execute_action(aid)
        # Wait for async execution
        time.sleep(1.0)
        action_dict = mgr.get_action(aid)
        assert action_dict["status"] == "success"
