"""Initial schema snapshot — documents existing tables.

This migration is a no-op for existing databases (tables already exist
due to CREATE TABLE IF NOT EXISTS). For new databases, it creates the
base schema matching the state of training_db.py and event_database.py.
"""


def upgrade(conn):
    # ── Training DB tables (from app/core/learning/training_db.py) ──

    conn.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_hash     TEXT    NOT NULL UNIQUE,
            user_input      TEXT    NOT NULL,
            task_type       TEXT    NOT NULL,
            confidence      REAL    DEFAULT 0.90,
            source          TEXT    DEFAULT 'synthetic',
            quality         REAL    DEFAULT 0.90,
            corrected_task  TEXT,
            corrected_by    TEXT    DEFAULT '',
            notes           TEXT    DEFAULT '',
            created_at      TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL,
            exported_at     TEXT,
            active          INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS build_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            built_at        TEXT    NOT NULL,
            total_samples   INTEGER DEFAULT 0,
            new_samples     INTEGER DEFAULT 0,
            export_path     TEXT,
            ollama_success  INTEGER DEFAULT 0,
            notes           TEXT    DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_corrections (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_input      TEXT    NOT NULL,
            predicted_task  TEXT    NOT NULL,
            correct_task    TEXT,
            session_id      TEXT    DEFAULT '',
            created_at      TEXT    NOT NULL,
            resolved        INTEGER DEFAULT 0
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_samples_hash   ON samples(sample_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_samples_source ON samples(source)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_samples_task   ON samples(task_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_samples_active ON samples(active)"
    )

    # ── Event monitoring tables (from app/core/monitoring/event_database.py) ──

    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            metric_name TEXT,
            metric_value REAL,
            threshold REAL,
            description TEXT,
            data_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            total_events INTEGER DEFAULT 0,
            high_count INTEGER DEFAULT 0,
            medium_count INTEGER DEFAULT 0,
            low_count INTEGER DEFAULT 0,
            cpu_count INTEGER DEFAULT 0,
            memory_count INTEGER DEFAULT 0,
            disk_count INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS remediation_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER,
            action_type TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            executed_at DATETIME,
            result_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(event_id) REFERENCES events(id)
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_timestamp ON events(timestamp DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_event_type ON events(event_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_severity ON events(severity)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_date ON event_stats(date DESC)"
    )


def downgrade(conn):
    # Intentionally not dropping tables — this is a baseline snapshot.
    pass
