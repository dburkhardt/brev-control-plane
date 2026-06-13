from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any
import uuid

from .queue_protocol import QueueJob, QueueLease


@dataclass
class QueueStore:
    path: str | Path

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    lease_id TEXT,
                    worker_id TEXT,
                    lease_expires_at TEXT,
                    returncode INTEGER,
                    stdout TEXT,
                    stderr TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                )
                """
            )

    def submit_job(self, job: QueueJob, *, now: datetime | None = None) -> str:
        self.initialize()
        job_id = uuid.uuid4().hex
        timestamp = _timestamp(now)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, experiment_id, payload_json, status, attempts, max_attempts,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, 'queued', 0, ?, ?, ?)
                """,
                (
                    job_id,
                    job.experiment_id,
                    job.to_json(),
                    job.max_attempts,
                    timestamp,
                    timestamp,
                ),
            )
        return job_id

    def lease_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> QueueLease | None:
        self.initialize()
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        timestamp = _timestamp(now)
        expires_at = _coerce_now(now) + timedelta(seconds=lease_seconds)
        lease_id = uuid.uuid4().hex
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = 'queued'
                    ORDER BY created_at, id
                    LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                attempt = int(row["attempts"]) + 1
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'leased',
                        attempts = ?,
                        lease_id = ?,
                        worker_id = ?,
                        lease_expires_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        attempt,
                        lease_id,
                        worker_id,
                        expires_at.isoformat(),
                        timestamp,
                        row["id"],
                    ),
                )
                connection.commit()
                return QueueLease(
                    job_id=row["id"],
                    lease_id=lease_id,
                    worker_id=worker_id,
                    attempt=attempt,
                    expires_at=expires_at,
                    job=QueueJob.from_json(row["payload_json"]),
                )
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def heartbeat(
        self,
        job_id: str,
        lease_id: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> bool:
        self.initialize()
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        timestamp = _timestamp(now)
        expires_at = _coerce_now(now) + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND lease_id = ? AND status = 'leased'
                """,
                (expires_at.isoformat(), timestamp, job_id, lease_id),
            )
            return cursor.rowcount == 1

    def complete_job(
        self,
        job_id: str,
        lease_id: str,
        *,
        artifacts: list[dict[str, Any]] | None = None,
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
        now: datetime | None = None,
    ) -> bool:
        self.initialize()
        timestamp = _timestamp(now)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'completed',
                    lease_id = NULL,
                    lease_expires_at = NULL,
                    returncode = ?,
                    stdout = ?,
                    stderr = ?,
                    error = NULL,
                    updated_at = ?,
                    completed_at = ?
                WHERE id = ? AND lease_id = ? AND status = 'leased'
                """,
                (returncode, stdout, stderr, timestamp, timestamp, job_id, lease_id),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute("DELETE FROM artifacts WHERE job_id = ?", (job_id,))
            for artifact in artifacts or []:
                connection.execute(
                    """
                    INSERT INTO artifacts (job_id, path, sha256, size_bytes, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        str(artifact["path"]),
                        str(artifact["sha256"]),
                        int(artifact["size_bytes"]),
                        timestamp,
                    ),
                )
            return True

    def fail_job(
        self,
        job_id: str,
        lease_id: str,
        *,
        error: str,
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
        now: datetime | None = None,
    ) -> bool:
        self.initialize()
        timestamp = _timestamp(now)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    lease_id = NULL,
                    lease_expires_at = NULL,
                    returncode = ?,
                    stdout = ?,
                    stderr = ?,
                    error = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE id = ? AND lease_id = ? AND status = 'leased'
                """,
                (returncode, stdout, stderr, error, timestamp, timestamp, job_id, lease_id),
            )
            return cursor.rowcount == 1

    def requeue_expired(self, *, now: datetime | None = None) -> dict[str, int]:
        self.initialize()
        timestamp = _timestamp(now)
        requeued = 0
        failed = 0
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = 'leased' AND lease_expires_at <= ?
                    ORDER BY lease_expires_at, id
                    """,
                    (timestamp,),
                ).fetchall()
                for row in rows:
                    if int(row["attempts"]) >= int(row["max_attempts"]):
                        connection.execute(
                            """
                            UPDATE jobs
                            SET status = 'failed',
                                lease_id = NULL,
                                lease_expires_at = NULL,
                                error = 'lease expired',
                                updated_at = ?,
                                completed_at = ?
                            WHERE id = ?
                            """,
                            (timestamp, timestamp, row["id"]),
                        )
                        failed += 1
                    else:
                        connection.execute(
                            """
                            UPDATE jobs
                            SET status = 'queued',
                                lease_id = NULL,
                                worker_id = NULL,
                                lease_expires_at = NULL,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (timestamp, row["id"]),
                        )
                        requeued += 1
                connection.commit()
                return {"failed": failed, "requeued": requeued}
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def status(self) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status ORDER BY status"
            ).fetchall()
        return {"counts": {row["status"]: int(row["count"]) for row in rows}}

    def list_jobs(
        self,
        *,
        experiment_id: str | None = None,
        job_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            where = []
            params: list[str] = []
            if experiment_id is not None:
                where.append("experiment_id = ?")
                params.append(experiment_id)
            if job_id is not None:
                where.append("id = ?")
                params.append(job_id)
            where_sql = f"WHERE {' AND '.join(where)}" if where else ""
            rows = connection.execute(
                f"SELECT * FROM jobs {where_sql} ORDER BY created_at, id",
                params,
            ).fetchall()
            job_ids = [row["id"] for row in rows]
            artifacts_by_job: dict[str, list[dict[str, Any]]] = {job_id: [] for job_id in job_ids}
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                artifact_rows = connection.execute(
                    f"""
                    SELECT job_id, path, sha256, size_bytes
                    FROM artifacts
                    WHERE job_id IN ({placeholders})
                    ORDER BY id
                    """,
                    job_ids,
                ).fetchall()
                for artifact in artifact_rows:
                    artifacts_by_job[artifact["job_id"]].append(
                        {
                            "path": artifact["path"],
                            "sha256": artifact["sha256"],
                            "size_bytes": int(artifact["size_bytes"]),
                        }
                    )
        return [
            {
                "artifacts": artifacts_by_job[row["id"]],
                "attempts": int(row["attempts"]),
                "completed_at": row["completed_at"],
                "created_at": row["created_at"],
                "error": row["error"],
                "experiment_id": row["experiment_id"],
                "id": row["id"],
                "job": json.loads(row["payload_json"]),
                "lease_expires_at": row["lease_expires_at"],
                "lease_id": row["lease_id"],
                "returncode": row["returncode"],
                "status": row["status"],
                "stderr": row["stderr"],
                "stdout": row["stdout"],
                "updated_at": row["updated_at"],
                "worker_id": row["worker_id"],
            }
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection


def _coerce_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _timestamp(now: datetime | None) -> str:
    return _coerce_now(now).isoformat()
