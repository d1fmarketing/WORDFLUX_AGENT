#!/usr/bin/env python3
"""SQLite ledger for tracking job history and artifacts."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default database location
DEFAULT_DB_PATH = "/var/lib/wordflux/ledger.db"


class JobLedger:
    """SQLite-based ledger for tracking jobs, events, and artifacts."""

    def __init__(self, db_path: str | None = None):
        """
        Initialize the job ledger.

        Args:
            db_path: Path to SQLite database file (defaults to /var/lib/wordflux/ledger.db)
        """
        self.db_path = db_path or os.getenv("LEDGER_DB_PATH", DEFAULT_DB_PATH)
        self._ensure_db_directory()
        self._init_database()

    def _ensure_db_directory(self) -> None:
        """Ensure the database directory exists."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _get_connection(self):
        """Get a database connection with proper cleanup."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()

    def _init_database(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Jobs table - main job tracking
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    agent TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT,
                    result TEXT,
                    error TEXT,
                    created_at TIMESTAMP NOT NULL,
                    started_at TIMESTAMP,
                    ended_at TIMESTAMP,
                    duration_seconds REAL,
                    retry_count INTEGER DEFAULT 0
                )
            """)

            # Job events table - detailed event log
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_data TEXT,
                    created_at TIMESTAMP NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs (job_id)
                )
            """)

            # Artifacts table - track generated artifacts
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    artifact_url TEXT,
                    file_size INTEGER,
                    metadata TEXT,
                    created_at TIMESTAMP NOT NULL,
                    expires_at TIMESTAMP,
                    FOREIGN KEY (job_id) REFERENCES jobs (job_id)
                )
            """)

            # Create indexes for common queries
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_agent ON jobs(agent)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_events_job_id ON job_events(job_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_job_id ON artifacts(job_id)")

            logger.info(f"Ledger database initialized at {self.db_path}")

    def record_job_enqueued(
        self,
        job_id: str,
        agent: str,
        payload: Dict[str, Any]
    ) -> None:
        """Record that a job has been enqueued."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO jobs (job_id, agent, status, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                job_id,
                agent,
                "enqueued",
                json.dumps(payload),
                datetime.now(timezone.utc)
            ))

            self._add_event(cursor, job_id, "enqueued", {"agent": agent})

    def record_job_started(self, job_id: str) -> None:
        """Record that a job has started processing."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc)

            cursor.execute("""
                UPDATE jobs
                SET status = 'processing',
                    started_at = ?
                WHERE job_id = ?
            """, (now, job_id))

            self._add_event(cursor, job_id, "started", {})

    def record_job_completed(
        self,
        job_id: str,
        result: Optional[Dict[str, Any]] = None,
        duration_seconds: Optional[float] = None
    ) -> None:
        """Record that a job has completed successfully."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc)

            cursor.execute("""
                UPDATE jobs
                SET status = 'completed',
                    ended_at = ?,
                    result = ?,
                    duration_seconds = ?
                WHERE job_id = ?
            """, (
                now,
                json.dumps(result) if result else None,
                duration_seconds,
                job_id
            ))

            self._add_event(cursor, job_id, "completed", {"success": True})

    def record_job_failed(
        self,
        job_id: str,
        error: str,
        duration_seconds: Optional[float] = None
    ) -> None:
        """Record that a job has failed."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc)

            cursor.execute("""
                UPDATE jobs
                SET status = 'failed',
                    ended_at = ?,
                    error = ?,
                    duration_seconds = ?
                WHERE job_id = ?
            """, (
                now,
                error,
                duration_seconds,
                job_id
            ))

            self._add_event(cursor, job_id, "failed", {"error": error})

    def record_artifact(
        self,
        job_id: str,
        artifact_type: str,
        artifact_url: str,
        file_size: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        expires_at: Optional[datetime] = None
    ) -> None:
        """Record an artifact generated by a job."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO artifacts (
                    job_id, artifact_type, artifact_url, file_size,
                    metadata, created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id,
                artifact_type,
                artifact_url,
                file_size,
                json.dumps(metadata) if metadata else None,
                datetime.now(timezone.utc),
                expires_at
            ))

            self._add_event(cursor, job_id, "artifact_created", {
                "type": artifact_type,
                "url": artifact_url
            })

    def _add_event(
        self,
        cursor: sqlite3.Cursor,
        job_id: str,
        event_type: str,
        event_data: Dict[str, Any]
    ) -> None:
        """Add an event to the job events table."""
        cursor.execute("""
            INSERT INTO job_events (job_id, event_type, event_data, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            job_id,
            event_type,
            json.dumps(event_data),
            datetime.now(timezone.utc)
        ))

    def get_recent_jobs(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent jobs."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT job_id, agent, status, created_at, started_at, ended_at,
                       duration_seconds, error
                FROM jobs
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))

            return [dict(row) for row in cursor.fetchall()]

    def get_job_details(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a job."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Get job info
            cursor.execute("""
                SELECT * FROM jobs WHERE job_id = ?
            """, (job_id,))
            job = cursor.fetchone()

            if not job:
                return None

            job_dict = dict(job)

            # Get events
            cursor.execute("""
                SELECT event_type, event_data, created_at
                FROM job_events
                WHERE job_id = ?
                ORDER BY created_at
            """, (job_id,))
            job_dict["events"] = [dict(row) for row in cursor.fetchall()]

            # Get artifacts
            cursor.execute("""
                SELECT artifact_type, artifact_url, file_size, metadata, created_at
                FROM artifacts
                WHERE job_id = ?
            """, (job_id,))
            job_dict["artifacts"] = [dict(row) for row in cursor.fetchall()]

            return job_dict

    def get_job_stats(
        self,
        agent: Optional[str] = None,
        since: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Get aggregate statistics about jobs."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            where_clauses = []
            params = []

            if agent:
                where_clauses.append("agent = ?")
                params.append(agent)

            if since:
                where_clauses.append("created_at >= ?")
                params.append(since)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            # Get counts by status
            cursor.execute(f"""
                SELECT status, COUNT(*) as count
                FROM jobs
                {where_sql}
                GROUP BY status
            """, params)

            status_counts = dict(cursor.fetchall())

            # Get average duration
            cursor.execute(f"""
                SELECT AVG(duration_seconds) as avg_duration,
                       MIN(duration_seconds) as min_duration,
                       MAX(duration_seconds) as max_duration
                FROM jobs
                {where_sql}
                AND duration_seconds IS NOT NULL
            """, params)

            duration_stats = dict(cursor.fetchone())

            return {
                "status_counts": status_counts,
                "duration_stats": duration_stats,
                "total_jobs": sum(status_counts.values())
            }


# Global ledger instance
_ledger: Optional[JobLedger] = None


def get_ledger() -> JobLedger:
    """Get the global ledger instance."""
    global _ledger
    if _ledger is None:
        _ledger = JobLedger()
    return _ledger


__all__ = ["JobLedger", "get_ledger"]