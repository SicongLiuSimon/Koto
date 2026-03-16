"""Tests for app.core.db.migration_manager.MigrationManager."""

import os
import sqlite3
import tempfile
import textwrap

import pytest

from app.core.db.migration_manager import MigrationManager


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_env(tmp_path):
    """Provide a temp db path + migrations directory."""
    db_path = str(tmp_path / "test.db")
    migrations_dir = str(tmp_path / "migrations")
    os.makedirs(migrations_dir, exist_ok=True)
    return db_path, migrations_dir


def _write_migration(migrations_dir, name, body):
    """Helper to write a migration .py file."""
    path = os.path.join(migrations_dir, name)
    with open(path, "w") as f:
        f.write(textwrap.dedent(body))


# ── Tests ─────────────────────────────────────────────────────────────


class TestMigrationManagerInit:
    def test_create_instance(self, tmp_env):
        db_path, migrations_dir = tmp_env
        mgr = MigrationManager(db_path, migrations_dir)
        assert mgr.db_path == db_path
        assert mgr.migrations_dir == migrations_dir


class TestEnsureMigrationsTable:
    def test_creates_table(self, tmp_env):
        db_path, migrations_dir = tmp_env
        mgr = MigrationManager(db_path, migrations_dir)
        conn = sqlite3.connect(db_path)
        try:
            mgr._ensure_migrations_table(conn)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'"
            ).fetchall()
            assert len(tables) == 1
        finally:
            conn.close()

    def test_idempotent(self, tmp_env):
        db_path, migrations_dir = tmp_env
        mgr = MigrationManager(db_path, migrations_dir)
        conn = sqlite3.connect(db_path)
        try:
            mgr._ensure_migrations_table(conn)
            mgr._ensure_migrations_table(conn)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'"
            ).fetchall()
            assert len(tables) == 1
        finally:
            conn.close()


class TestGetPending:
    def test_empty_dir(self, tmp_env):
        db_path, migrations_dir = tmp_env
        mgr = MigrationManager(db_path, migrations_dir)
        assert mgr.get_pending() == []

    def test_nonexistent_dir(self, tmp_env):
        db_path, _ = tmp_env
        mgr = MigrationManager(db_path, "/nonexistent/dir")
        assert mgr.get_pending() == []

    def test_returns_sorted_migrations(self, tmp_env):
        db_path, migrations_dir = tmp_env
        _write_migration(
            migrations_dir,
            "002_second.py",
            "def upgrade(conn): pass",
        )
        _write_migration(
            migrations_dir,
            "001_first.py",
            "def upgrade(conn): pass",
        )
        mgr = MigrationManager(db_path, migrations_dir)
        assert mgr.get_pending() == ["001_first.py", "002_second.py"]

    def test_ignores_underscore_files(self, tmp_env):
        db_path, migrations_dir = tmp_env
        _write_migration(
            migrations_dir,
            "__init__.py",
            "",
        )
        _write_migration(
            migrations_dir,
            "001_first.py",
            "def upgrade(conn): pass",
        )
        mgr = MigrationManager(db_path, migrations_dir)
        assert mgr.get_pending() == ["001_first.py"]

    def test_ignores_non_py_files(self, tmp_env):
        db_path, migrations_dir = tmp_env
        with open(os.path.join(migrations_dir, "README.md"), "w") as f:
            f.write("ignore me")
        _write_migration(
            migrations_dir,
            "001_first.py",
            "def upgrade(conn): pass",
        )
        mgr = MigrationManager(db_path, migrations_dir)
        assert mgr.get_pending() == ["001_first.py"]

    def test_excludes_already_applied(self, tmp_env):
        db_path, migrations_dir = tmp_env
        _write_migration(
            migrations_dir,
            "001_first.py",
            "def upgrade(conn): pass",
        )
        _write_migration(
            migrations_dir,
            "002_second.py",
            "def upgrade(conn): pass",
        )
        # Manually mark 001 as applied
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("INSERT INTO _migrations (name) VALUES (?)", ("001_first.py",))
        conn.commit()
        conn.close()

        mgr = MigrationManager(db_path, migrations_dir)
        assert mgr.get_pending() == ["002_second.py"]


class TestApplyAll:
    def test_applies_migrations_in_order(self, tmp_env):
        db_path, migrations_dir = tmp_env
        _write_migration(
            migrations_dir,
            "001_create_users.py",
            """\
            def upgrade(conn):
                conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")

            def downgrade(conn):
                conn.execute("DROP TABLE IF EXISTS users")
            """,
        )
        _write_migration(
            migrations_dir,
            "002_create_posts.py",
            """\
            def upgrade(conn):
                conn.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT)")

            def downgrade(conn):
                conn.execute("DROP TABLE IF EXISTS posts")
            """,
        )
        mgr = MigrationManager(db_path, migrations_dir)
        applied = mgr.apply_all()

        assert applied == ["001_create_users.py", "002_create_posts.py"]

        # Verify tables were actually created
        conn = sqlite3.connect(db_path)
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "users" in tables
            assert "posts" in tables
            assert "_migrations" in tables
        finally:
            conn.close()

    def test_already_applied_skipped(self, tmp_env):
        db_path, migrations_dir = tmp_env
        _write_migration(
            migrations_dir,
            "001_create_users.py",
            """\
            def upgrade(conn):
                conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
            """,
        )
        mgr = MigrationManager(db_path, migrations_dir)

        # First run
        first = mgr.apply_all()
        assert first == ["001_create_users.py"]

        # Second run — nothing new
        second = mgr.apply_all()
        assert second == []

    def test_no_pending_returns_empty(self, tmp_env):
        db_path, migrations_dir = tmp_env
        mgr = MigrationManager(db_path, migrations_dir)
        assert mgr.apply_all() == []

    def test_skips_migration_without_upgrade(self, tmp_env):
        db_path, migrations_dir = tmp_env
        _write_migration(
            migrations_dir,
            "001_no_upgrade.py",
            "# No upgrade function here\nx = 1\n",
        )
        mgr = MigrationManager(db_path, migrations_dir)
        applied = mgr.apply_all()
        assert applied == []

    def test_rollback_on_failure(self, tmp_env):
        db_path, migrations_dir = tmp_env
        _write_migration(
            migrations_dir,
            "001_good.py",
            """\
            def upgrade(conn):
                conn.execute("CREATE TABLE good_table (id INTEGER PRIMARY KEY)")
            """,
        )
        _write_migration(
            migrations_dir,
            "002_bad.py",
            """\
            def upgrade(conn):
                raise RuntimeError("intentional failure")
            """,
        )
        mgr = MigrationManager(db_path, migrations_dir)

        with pytest.raises(RuntimeError, match="intentional failure"):
            mgr.apply_all()

        # 001 was committed before 002 failed, so good_table exists
        conn = sqlite3.connect(db_path)
        try:
            applied = {
                r[0]
                for r in conn.execute("SELECT name FROM _migrations").fetchall()
            }
            assert "001_good.py" in applied
            assert "002_bad.py" not in applied

            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "good_table" in tables
        finally:
            conn.close()

    def test_records_applied_at_timestamp(self, tmp_env):
        db_path, migrations_dir = tmp_env
        _write_migration(
            migrations_dir,
            "001_simple.py",
            "def upgrade(conn): pass",
        )
        mgr = MigrationManager(db_path, migrations_dir)
        mgr.apply_all()

        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT name, applied_at FROM _migrations WHERE name = ?",
                ("001_simple.py",),
            ).fetchone()
            assert row is not None
            assert row[0] == "001_simple.py"
            assert row[1] is not None  # timestamp was recorded
        finally:
            conn.close()


class TestInitialMigration:
    """Verify the actual 001_initial_schema.py migration works."""

    def test_initial_schema_applies_cleanly(self, tmp_env):
        db_path, _ = tmp_env
        real_migrations = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "app",
            "core",
            "db",
            "migrations",
        )
        real_migrations = os.path.normpath(real_migrations)

        mgr = MigrationManager(db_path, real_migrations)
        applied = mgr.apply_all()

        assert "001_initial_schema.py" in applied

        conn = sqlite3.connect(db_path)
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            # Training DB tables
            assert "samples" in tables
            assert "build_history" in tables
            assert "pending_corrections" in tables
            # Event monitoring tables
            assert "events" in tables
            assert "event_stats" in tables
            assert "remediation_actions" in tables
        finally:
            conn.close()

    def test_initial_schema_idempotent(self, tmp_env):
        """Running twice should not fail (CREATE IF NOT EXISTS)."""
        db_path, _ = tmp_env
        real_migrations = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "app",
            "core",
            "db",
            "migrations",
        )
        real_migrations = os.path.normpath(real_migrations)

        mgr = MigrationManager(db_path, real_migrations)
        mgr.apply_all()

        # Applying again should report nothing pending
        assert mgr.apply_all() == []
