"""Lightweight SQLite migration manager for Koto.

Tracks applied migrations in a _migrations table.
Migrations are Python files in the migrations/ directory.
Each migration must define an upgrade(conn) function.
An optional downgrade(conn) function can be provided for rollbacks.
"""

import importlib.util
import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


class MigrationManager:
    """Manages schema migrations for a SQLite database.

    Args:
        db_path: Path to the SQLite database file.
        migrations_dir: Path to the directory containing migration scripts.
    """

    def __init__(self, db_path: str, migrations_dir: str):
        self.db_path = db_path
        self.migrations_dir = migrations_dir

    def _ensure_migrations_table(self, conn: sqlite3.Connection) -> None:
        """Create the _migrations tracking table if it doesn't exist."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def get_applied(self, conn: sqlite3.Connection) -> set:
        """Return set of migration names already applied."""
        self._ensure_migrations_table(conn)
        rows = conn.execute("SELECT name FROM _migrations").fetchall()
        return {r[0] for r in rows}

    def get_pending(self) -> list:
        """Return sorted list of migration filenames not yet applied."""
        if not os.path.isdir(self.migrations_dir):
            return []
        conn = sqlite3.connect(self.db_path)
        try:
            applied = self.get_applied(conn)
            all_migrations = sorted(
                f
                for f in os.listdir(self.migrations_dir)
                if f.endswith(".py") and not f.startswith("_")
            )
            return [m for m in all_migrations if m not in applied]
        finally:
            conn.close()

    def apply_all(self) -> list:
        """Apply all pending migrations. Returns list of applied migration names."""
        pending = self.get_pending()
        if not pending:
            logger.info("No pending migrations for %s", self.db_path)
            return []

        conn = sqlite3.connect(self.db_path)
        applied = []
        try:
            for migration_file in pending:
                path = os.path.join(self.migrations_dir, migration_file)
                spec = importlib.util.spec_from_file_location(migration_file, path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                if hasattr(module, "upgrade"):
                    logger.info(
                        "Applying migration: %s to %s",
                        migration_file,
                        self.db_path,
                    )
                    module.upgrade(conn)
                    conn.execute(
                        "INSERT INTO _migrations (name) VALUES (?)",
                        (migration_file,),
                    )
                    conn.commit()
                    applied.append(migration_file)
                    logger.info("Applied: %s", migration_file)
                else:
                    logger.warning(
                        "Migration %s has no upgrade() function, skipping",
                        migration_file,
                    )

            return applied
        except Exception as e:
            conn.rollback()
            logger.error("Migration failed: %s", e)
            raise
        finally:
            conn.close()
