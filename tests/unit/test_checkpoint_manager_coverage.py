# -*- coding: utf-8 -*-
"""
Comprehensive tests for app.core.agent.checkpoint_manager
"""

import sqlite3
import threading
from unittest.mock import MagicMock, PropertyMock, patch

import pytest


@pytest.mark.unit
class TestResetCheckpointer:
    """Tests for reset_checkpointer()."""

    def setup_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def teardown_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def test_reset_clears_instance(self):
        """reset_checkpointer sets _checkpointer_instance to None."""
        import app.core.agent.checkpoint_manager as cm

        cm._checkpointer_instance = "something"
        cm._checkpointer_type = "sqlite"

        cm.reset_checkpointer()

        assert cm._checkpointer_instance is None
        assert cm._checkpointer_type == "none"

    def test_reset_is_idempotent(self):
        """Calling reset_checkpointer twice is safe."""
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()
        reset_checkpointer()

        import app.core.agent.checkpoint_manager as cm

        assert cm._checkpointer_instance is None
        assert cm._checkpointer_type == "none"


@pytest.mark.unit
class TestGetCheckpointerType:
    """Tests for get_checkpointer_type()."""

    def setup_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def teardown_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def test_returns_none_initially(self):
        """Before any checkpointer is created, type should be 'none'."""
        from app.core.agent.checkpoint_manager import get_checkpointer_type

        assert get_checkpointer_type() == "none"

    def test_returns_sqlite_after_sqlite_init(self):
        """After SqliteSaver init, type should be 'sqlite'."""
        import app.core.agent.checkpoint_manager as cm

        cm._checkpointer_type = "sqlite"
        assert cm.get_checkpointer_type() == "sqlite"

    def test_returns_memory_after_memory_init(self):
        """After MemorySaver init, type should be 'memory'."""
        import app.core.agent.checkpoint_manager as cm

        cm._checkpointer_type = "memory"
        assert cm.get_checkpointer_type() == "memory"


@pytest.mark.unit
class TestGetCheckpointer:
    """Tests for get_checkpointer()."""

    def setup_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def teardown_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    @patch("app.core.agent.checkpoint_manager._get_sqlite_conn")
    @patch("app.core.agent.checkpoint_manager.Path")
    def test_sqlite_saver_when_available(self, mock_path_cls, mock_get_conn):
        """get_checkpointer uses SqliteSaver when import succeeds."""
        import app.core.agent.checkpoint_manager as cm

        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn

        mock_saver = MagicMock()
        mock_saver_cls = MagicMock(return_value=mock_saver)

        fake_module = MagicMock()
        fake_module.SqliteSaver = mock_saver_cls

        with patch.dict("sys.modules", {"langgraph.checkpoint.sqlite": fake_module}):
            result = cm.get_checkpointer(db_path="/tmp/test.sqlite")

        assert result is mock_saver
        assert cm._checkpointer_type == "sqlite"
        mock_saver_cls.assert_called_once_with(mock_conn)

    def test_fallback_to_memory_on_import_error(self):
        """get_checkpointer falls back to MemorySaver when SqliteSaver import fails."""
        import sys

        import app.core.agent.checkpoint_manager as cm

        # Ensure langgraph.checkpoint.sqlite is NOT importable
        saved = sys.modules.pop("langgraph.checkpoint.sqlite", None)
        try:
            with patch.dict(
                "sys.modules",
                {"langgraph.checkpoint.sqlite": None},
            ):
                mock_memory_saver = MagicMock()
                mock_memory_cls = MagicMock(return_value=mock_memory_saver)
                fake_memory_module = MagicMock()
                fake_memory_module.MemorySaver = mock_memory_cls

                with patch.dict(
                    "sys.modules",
                    {
                        "langgraph.checkpoint.sqlite": None,
                        "langgraph.checkpoint.memory": fake_memory_module,
                    },
                ):
                    result = cm.get_checkpointer()

            assert cm._checkpointer_type == "memory"
        finally:
            if saved is not None:
                sys.modules["langgraph.checkpoint.sqlite"] = saved

    @patch("app.core.agent.checkpoint_manager._get_sqlite_conn")
    @patch("app.core.agent.checkpoint_manager.Path")
    def test_fallback_to_memory_on_sqlite_init_exception(
        self, mock_path_cls, mock_get_conn
    ):
        """get_checkpointer falls back to MemorySaver when SqliteSaver() raises."""
        import app.core.agent.checkpoint_manager as cm

        mock_get_conn.side_effect = RuntimeError("disk full")

        mock_memory_saver = MagicMock()
        mock_memory_cls = MagicMock(return_value=mock_memory_saver)
        fake_memory_module = MagicMock()
        fake_memory_module.MemorySaver = mock_memory_cls

        fake_sqlite_module = MagicMock()
        # SqliteSaver class itself exists, but _get_sqlite_conn will fail before it's called
        fake_sqlite_module.SqliteSaver = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "langgraph.checkpoint.sqlite": fake_sqlite_module,
                "langgraph.checkpoint.memory": fake_memory_module,
            },
        ):
            result = cm.get_checkpointer(db_path="/tmp/fail.sqlite")

        assert result is mock_memory_saver
        assert cm._checkpointer_type == "memory"

    @patch("app.core.agent.checkpoint_manager._get_sqlite_conn")
    @patch("app.core.agent.checkpoint_manager.Path")
    def test_singleton_returns_same_instance(self, mock_path_cls, mock_get_conn):
        """Second call to get_checkpointer returns the cached instance."""
        import app.core.agent.checkpoint_manager as cm

        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_saver = MagicMock()
        mock_saver_cls = MagicMock(return_value=mock_saver)

        fake_module = MagicMock()
        fake_module.SqliteSaver = mock_saver_cls

        with patch.dict("sys.modules", {"langgraph.checkpoint.sqlite": fake_module}):
            first = cm.get_checkpointer(db_path="/tmp/test.sqlite")
            second = cm.get_checkpointer(db_path="/tmp/other.sqlite")

        assert first is second
        # SqliteSaver constructor called only once
        assert mock_saver_cls.call_count == 1

    def test_singleton_early_return_before_lock(self):
        """When instance already set, get_checkpointer returns without acquiring lock."""
        import app.core.agent.checkpoint_manager as cm

        sentinel = MagicMock()
        cm._checkpointer_instance = sentinel

        result = cm.get_checkpointer()
        assert result is sentinel


@pytest.mark.unit
class TestGetSqliteConn:
    """Tests for _get_sqlite_conn()."""

    def setup_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def teardown_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    @patch("sqlite3.connect")
    def test_creates_connection_with_wal_mode(self, mock_connect):
        """_get_sqlite_conn creates a WAL-mode connection."""
        from app.core.agent.checkpoint_manager import _get_sqlite_conn

        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        result = _get_sqlite_conn("/tmp/test.db")

        mock_connect.assert_called_once_with("/tmp/test.db", check_same_thread=False)
        calls = [str(c) for c in mock_conn.execute.call_args_list]
        assert any("WAL" in c for c in calls)
        assert any("NORMAL" in c for c in calls)
        assert result is mock_conn

    def test_real_sqlite_conn_with_temp_db(self, tmp_path):
        """Integration: _get_sqlite_conn with a real temp database."""
        from app.core.agent.checkpoint_manager import _get_sqlite_conn

        db_file = str(tmp_path / "test_wal.sqlite")
        conn = _get_sqlite_conn(db_file)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"
        finally:
            conn.close()


@pytest.mark.unit
class TestCheckpointManagerListCheckpoints:
    """Tests for CheckpointManager.list_checkpoints()."""

    def setup_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def teardown_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def test_list_checkpoints_success(self):
        """list_checkpoints returns formatted list from checkpointer."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        mock_cp = MagicMock()
        cm._checkpointer_instance = mock_cp

        snap1 = MagicMock()
        snap1.config = {"configurable": {"checkpoint_id": "cp-1"}}
        snap1.metadata = {"step": 1, "source": "loop", "writes": {"agent": "data"}}

        snap2 = MagicMock()
        snap2.config = {"configurable": {"checkpoint_id": "cp-2"}}
        snap2.metadata = {"step": 2, "source": "loop", "writes": None}

        mock_cp.list.return_value = [snap1, snap2]

        results = CheckpointManager.list_checkpoints("thread-123")

        assert len(results) == 2
        assert results[0]["checkpoint_id"] == "cp-1"
        assert results[0]["step"] == 1
        assert results[0]["writes"] == ["agent"]
        assert results[1]["checkpoint_id"] == "cp-2"
        assert results[1]["writes"] == []

        mock_cp.list.assert_called_once_with(
            {"configurable": {"thread_id": "thread-123"}}
        )

    def test_list_checkpoints_with_empty_metadata(self):
        """list_checkpoints handles snap with metadata=None."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        mock_cp = MagicMock()
        cm._checkpointer_instance = mock_cp

        snap = MagicMock()
        snap.config = {"configurable": {}}
        snap.metadata = None

        mock_cp.list.return_value = [snap]

        results = CheckpointManager.list_checkpoints("thread-x")

        assert len(results) == 1
        assert results[0]["checkpoint_id"] == ""
        assert results[0]["step"] == 0
        assert results[0]["writes"] == []

    def test_list_checkpoints_handles_exception(self):
        """list_checkpoints returns [] on exception."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        mock_cp = MagicMock()
        mock_cp.list.side_effect = RuntimeError("db locked")
        cm._checkpointer_instance = mock_cp

        results = CheckpointManager.list_checkpoints("thread-err")
        assert results == []


@pytest.mark.unit
class TestCheckpointManagerDeleteThread:
    """Tests for CheckpointManager.delete_thread()."""

    def setup_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def teardown_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def test_delete_thread_non_sqlite_returns_true(self):
        """When type is not 'sqlite', delete_thread returns True immediately."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        cm._checkpointer_type = "memory"
        assert CheckpointManager.delete_thread("thread-1") is True

    def test_delete_thread_type_none_returns_true(self):
        """When type is 'none', delete_thread returns True immediately."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        cm._checkpointer_type = "none"
        assert CheckpointManager.delete_thread("thread-1") is True

    def test_delete_thread_sqlite_success(self):
        """delete_thread with sqlite type deletes from all tables and commits."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        mock_conn = MagicMock()
        mock_cp = MagicMock()
        mock_cp.conn = mock_conn

        cm._checkpointer_instance = mock_cp
        cm._checkpointer_type = "sqlite"

        fake_sqlite_module = MagicMock()

        with patch.dict(
            "sys.modules", {"langgraph.checkpoint.sqlite": fake_sqlite_module}
        ):
            result = CheckpointManager.delete_thread("thread-42")

        assert result is True
        # Should attempt DELETE on all three tables
        assert mock_conn.execute.call_count == 3
        mock_conn.commit.assert_called_once()

        # Verify tables targeted
        executed_sqls = [call.args[0] for call in mock_conn.execute.call_args_list]
        assert any("checkpoint_blobs" in sql for sql in executed_sqls)
        assert any("checkpoint_writes" in sql for sql in executed_sqls)
        assert any("checkpoints" in sql for sql in executed_sqls)

    def test_delete_thread_sqlite_no_conn_attr(self):
        """delete_thread returns False when checkpointer has no conn attribute."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        mock_cp = MagicMock(spec=[])  # no attributes at all
        cm._checkpointer_instance = mock_cp
        cm._checkpointer_type = "sqlite"

        fake_sqlite_module = MagicMock()

        with patch.dict(
            "sys.modules", {"langgraph.checkpoint.sqlite": fake_sqlite_module}
        ):
            result = CheckpointManager.delete_thread("thread-x")

        assert result is False

    def test_delete_thread_handles_exception(self):
        """delete_thread returns False on unexpected exception."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        cm._checkpointer_type = "sqlite"
        cm._checkpointer_instance = None  # force get_checkpointer to be called

        # Patch get_checkpointer to raise
        with patch.object(cm, "get_checkpointer", side_effect=RuntimeError("boom")):
            fake_sqlite_module = MagicMock()
            with patch.dict(
                "sys.modules", {"langgraph.checkpoint.sqlite": fake_sqlite_module}
            ):
                result = CheckpointManager.delete_thread("thread-bad")

        assert result is False

    def test_delete_thread_partial_table_failure(self):
        """delete_thread succeeds even if some table DELETEs fail."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        mock_conn = MagicMock()
        # First execute succeeds, second raises, third succeeds
        mock_conn.execute.side_effect = [
            None,
            sqlite3.OperationalError("no such table"),
            None,
        ]
        mock_cp = MagicMock()
        mock_cp.conn = mock_conn

        cm._checkpointer_instance = mock_cp
        cm._checkpointer_type = "sqlite"

        fake_sqlite_module = MagicMock()

        with patch.dict(
            "sys.modules", {"langgraph.checkpoint.sqlite": fake_sqlite_module}
        ):
            result = CheckpointManager.delete_thread("thread-partial")

        assert result is True
        mock_conn.commit.assert_called_once()


@pytest.mark.unit
class TestCheckpointManagerGetDbInfo:
    """Tests for CheckpointManager.get_db_info()."""

    def setup_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def teardown_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def test_get_db_info_type_none(self):
        """get_db_info returns defaults when type is 'none'."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        info = CheckpointManager.get_db_info()

        assert info["type"] == "none"
        assert info["db_path"] is None
        assert info["thread_count"] == 0
        assert info["total_checkpoints"] == 0

    def test_get_db_info_type_memory(self):
        """get_db_info returns defaults when type is 'memory'."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        cm._checkpointer_type = "memory"
        info = CheckpointManager.get_db_info()

        assert info["type"] == "memory"
        assert info["db_path"] is None
        assert info["thread_count"] == 0
        assert info["total_checkpoints"] == 0

    def test_get_db_info_sqlite_with_checkpoints_table(self):
        """get_db_info queries checkpoints table when type is 'sqlite'."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        mock_conn = MagicMock()
        mock_cp = MagicMock()
        mock_cp.conn = mock_conn

        cm._checkpointer_instance = mock_cp
        cm._checkpointer_type = "sqlite"

        # sqlite_master query returns "checkpoints" table
        tables_cursor = MagicMock()
        tables_cursor.fetchall.return_value = [("checkpoints",), ("checkpoint_writes",)]

        # COUNT query
        count_cursor = MagicMock()
        count_cursor.fetchone.return_value = (5, 42)

        mock_conn.execute.side_effect = [tables_cursor, count_cursor]

        info = CheckpointManager.get_db_info()

        assert info["type"] == "sqlite"
        assert info["db_path"] is not None
        assert info["thread_count"] == 5
        assert info["total_checkpoints"] == 42

    def test_get_db_info_sqlite_missing_checkpoints_table(self):
        """get_db_info handles missing checkpoints table gracefully."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        mock_conn = MagicMock()
        mock_cp = MagicMock()
        mock_cp.conn = mock_conn

        cm._checkpointer_instance = mock_cp
        cm._checkpointer_type = "sqlite"

        # sqlite_master returns no checkpoints table
        tables_cursor = MagicMock()
        tables_cursor.fetchall.return_value = [("other_table",)]
        mock_conn.execute.return_value = tables_cursor

        info = CheckpointManager.get_db_info()

        assert info["type"] == "sqlite"
        assert info["thread_count"] == 0
        assert info["total_checkpoints"] == 0

    def test_get_db_info_sqlite_no_conn_attr(self):
        """get_db_info handles checkpointer without conn attribute."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        mock_cp = MagicMock(spec=[])  # no conn
        cm._checkpointer_instance = mock_cp
        cm._checkpointer_type = "sqlite"

        info = CheckpointManager.get_db_info()

        assert info["type"] == "sqlite"
        assert info["thread_count"] == 0
        assert info["total_checkpoints"] == 0

    def test_get_db_info_sqlite_query_exception(self):
        """get_db_info handles exception during SQL queries."""
        import app.core.agent.checkpoint_manager as cm
        from app.core.agent.checkpoint_manager import CheckpointManager

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("db corrupted")
        mock_cp = MagicMock()
        mock_cp.conn = mock_conn

        cm._checkpointer_instance = mock_cp
        cm._checkpointer_type = "sqlite"

        info = CheckpointManager.get_db_info()

        assert info["type"] == "sqlite"
        assert info["thread_count"] == 0
        assert info["total_checkpoints"] == 0


@pytest.mark.unit
class TestModuleGlobals:
    """Tests for module-level globals."""

    def setup_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def teardown_method(self):
        from app.core.agent.checkpoint_manager import reset_checkpointer

        reset_checkpointer()

    def test_lock_is_threading_lock(self):
        """_checkpointer_lock is a threading.Lock."""
        import app.core.agent.checkpoint_manager as cm

        assert isinstance(cm._checkpointer_lock, type(threading.Lock()))

    def test_default_db_path_ends_with_sqlite(self):
        """_DEFAULT_DB_PATH ends with .sqlite."""
        import app.core.agent.checkpoint_manager as cm

        assert cm._DEFAULT_DB_PATH.endswith(".sqlite")
