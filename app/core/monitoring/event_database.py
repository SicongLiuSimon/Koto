"""
Phase 5a: Persistent Event Storage

SQLite-based persistence for monitoring events.
Enables historical analysis and long-term tracking.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventDatabase:
    """
    SQLite-based persistent storage for monitoring events.
    Supports queries, filtering, and historical analysis.
    """

    DB_PATH = Path(__file__).parent.parent.parent / "data" / "monitoring_events.db"

    def __init__(self):
        """Initialize database with schema."""
        self.lock = Lock()
        self._local = threading.local()  # per-thread persistent connection
        self._ensure_db_exists()

    def _get_conn(self) -> sqlite3.Connection:
        """Return a persistent per-thread SQLite connection."""
        if not getattr(self._local, "conn", None):
            conn = sqlite3.connect(str(self.DB_PATH), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _ensure_db_exists(self) -> None:
        """Create database and tables if they don't exist."""
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(str(self.DB_PATH)) as conn:
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

            # Create indices for common queries
            try:
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
            except sqlite3.OperationalError:
                pass  # Index already exists

            conn.commit()

    def save_event(self, event_data: Dict[str, Any]) -> int:
        """
        Save a monitoring event to database.

        Args:
            event_data: Event dict with timestamp, event_type, severity, etc.

        Returns:
            Event ID in database
        """
        with self.lock:
            try:
                conn = self._get_conn()
                cursor = conn.execute(
                    """
                        INSERT INTO events (
                            timestamp, event_type, severity, metric_name,
                            metric_value, threshold, description, data_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_data.get("timestamp"),
                        event_data.get("event_type"),
                        event_data.get("severity"),
                        event_data.get("metric_name"),
                        event_data.get("metric_value"),
                        event_data.get("threshold"),
                        event_data.get("description"),
                        json.dumps(event_data),
                    ),
                )
                conn.commit()

                # Update daily stats
                self._update_daily_stats(event_data)

                return cursor.lastrowid
            except Exception as e:
                logger.error(f"Error saving event: {e}")
                return -1

    def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
        hours_back: int = 24,
    ) -> List[Dict[str, Any]]:
        """
        Query events from database.

        Args:
            limit: Max events to return
            offset: Pagination offset
            event_type: Filter by event type
            severity: Filter by severity
            hours_back: Only return events from last N hours

        Returns:
            List of event dicts
        """
        with self.lock:
            try:
                conn = self._get_conn()
                conn.row_factory = sqlite3.Row

                # Build query
                query = "SELECT * FROM events WHERE 1=1"
                params = []

                # Add time filter
                cutoff = (datetime.now() - timedelta(hours=hours_back)).isoformat()
                query += " AND timestamp > ?"
                params.append(cutoff)

                # Add type filter
                if event_type:
                    query += " AND event_type = ?"
                    params.append(event_type)

                    # Add severity filter
                if severity:
                    query += " AND severity = ?"
                    params.append(severity)

                    # Order and limit
                query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                cursor = conn.execute(query, params)
                events = []

                for row in cursor.fetchall():
                    event_dict = dict(row)
                    # Try to parse data_json for additional fields
                    try:
                        if event_dict.get("data_json"):
                            event_dict["data"] = json.loads(event_dict["data_json"])
                    except json.JSONDecodeError:
                        pass
                    events.append(event_dict)

                return events
            except Exception as e:
                logger.error(f"Error querying events: {e}")
                return []

    def get_stats(self, days_back: int = 30) -> Dict[str, Any]:
        """
        Get historical statistics for the last N days.

        Args:
            days_back: Number of days to analyze

        Returns:
            Stats dict with daily breakdown
        """
        with self.lock:
            try:
                conn = self._get_conn()
                conn.row_factory = sqlite3.Row

                cutoff = (datetime.now() - timedelta(days=days_back)).date()

                cursor = conn.execute(
                    "SELECT * FROM event_stats WHERE date >= ? ORDER BY date DESC",
                    (str(cutoff),),
                )

                stats = []
                total_events = 0
                severity_breakdown = {"high": 0, "medium": 0, "low": 0}

                for row in cursor.fetchall():
                    stat_dict = dict(row)
                    stats.append(stat_dict)
                    total_events += stat_dict["total_events"]
                    severity_breakdown["high"] += stat_dict["high_count"]
                    severity_breakdown["medium"] += stat_dict["medium_count"]
                    severity_breakdown["low"] += stat_dict["low_count"]

                return {
                    "days": days_back,
                    "total_events": total_events,
                    "daily_stats": stats,
                    "severity_breakdown": severity_breakdown,
                    "avg_daily": total_events / days_back if days_back > 0 else 0,
                }
            except Exception as e:
                logger.error(f"Error getting stats: {e}")
                return {}

    def _update_daily_stats(self, event_data: Dict[str, Any]) -> None:
        """Update daily statistics for the event."""
        try:
            timestamp = event_data.get("timestamp", datetime.now().isoformat())
            date_str = (
                timestamp.split("T")[0]
                if "T" in timestamp
                else str(datetime.now().date())
            )
            severity = event_data.get("severity", "low").lower()
            event_type = event_data.get("event_type", "unknown").lower()

            conn = (
                self._get_conn()
            )  # reuse per-thread connection (called under self.lock)
            # Get or create daily record
            cursor = conn.execute(
                "SELECT id FROM event_stats WHERE date = ?", (date_str,)
            )
            row = cursor.fetchone()

            if row:
                # Update existing
                stat_id = row[0]
                update_col = f"{severity}_count"

                conn.execute(
                    f"""
                    UPDATE event_stats
                    SET total_events = total_events + 1,
                        {update_col} = {update_col} + 1
                    WHERE id = ?
                """,
                    (stat_id,),
                )
            else:
                # Create new
                severity_dict = {
                    "high": 1 if severity == "high" else 0,
                    "medium": 1 if severity == "medium" else 0,
                    "low": 1 if severity == "low" else 0,
                }

                type_dict = {
                    "cpu": 1 if "cpu" in event_type else 0,
                    "memory": 1 if "memory" in event_type else 0,
                    "disk": 1 if "disk" in event_type else 0,
                }

                conn.execute(
                    """
                    INSERT INTO event_stats (
                        date, total_events, high_count, medium_count, low_count,
                        cpu_count, memory_count, disk_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        date_str,
                        1,
                        severity_dict["high"],
                        severity_dict["medium"],
                        severity_dict["low"],
                        type_dict["cpu"],
                        type_dict["memory"],
                        type_dict["disk"],
                    ),
                )

            conn.commit()
        except Exception as e:
            logger.error(f"Error updating daily stats: {e}")

    def save_remediation_action(
        self,
        event_id: int,
        action_type: str,
        status: str = "pending",
        result: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Save a remediation action for an event."""
        with self.lock:
            try:
                conn = self._get_conn()
                cursor = conn.execute(
                    """
                        INSERT INTO remediation_actions (
                            event_id, action_type, status, result_json
                        ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        action_type,
                        status,
                        json.dumps(result) if result else None,
                    ),
                )
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"Error saving remediation action: {e}")
                return -1

    def update_remediation_status(
        self, action_id: int, status: str, result: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Update remediation action status."""
        with self.lock:
            try:
                conn = self._get_conn()
                conn.execute(
                    """
                        UPDATE remediation_actions
                        SET status = ?, executed_at = CURRENT_TIMESTAMP,
                            result_json = ?
                        WHERE id = ?
                    """,
                    (status, json.dumps(result) if result else None, action_id),
                )
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error updating remediation status: {e}")
                return False

    def clear_old_events(self, days_old: int = 90) -> int:
        """
        Delete events older than N days.

        Args:
            days_old: Delete events older than this many days

        Returns:
            Number of events deleted
        """
        with self.lock:
            try:
                cutoff = (datetime.now() - timedelta(days=days_old)).isoformat()
                conn = self._get_conn()
                cursor = conn.execute(
                    "DELETE FROM events WHERE timestamp < ?", (cutoff,)
                )
                conn.commit()
                return cursor.rowcount
            except Exception as e:
                logger.error(f"Error clearing old events: {e}")
                return 0


# Global instance
_db_instance: Optional[EventDatabase] = None
_db_lock = Lock()


def get_event_database() -> EventDatabase:
    """Get or create the singleton EventDatabase instance."""
    global _db_instance

    if _db_instance is None:
        with _db_lock:
            if _db_instance is None:
                _db_instance = EventDatabase()

    return _db_instance
