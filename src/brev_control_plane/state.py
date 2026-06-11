from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


@dataclass
class StateStore:
    path: str | Path

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS instances (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def upsert_instances(self, instances: list[dict[str, Any]]) -> None:
        self.initialize()
        now = _utc_now()
        with self._connect() as connection:
            for instance in instances:
                instance_id = str(instance.get("id", "")).strip()
                if not instance_id:
                    continue
                connection.execute(
                    """
                    INSERT INTO instances (id, name, status, raw_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        status = excluded.status,
                        raw_json = excluded.raw_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        instance_id,
                        str(instance.get("name", "")),
                        str(instance.get("status", "")),
                        json.dumps(instance, sort_keys=True),
                        now,
                    ),
                )

    def list_instances(self) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, name, status FROM instances ORDER BY id"
            ).fetchall()
        return [
            {"id": row["id"], "name": row["name"], "status": row["status"]}
            for row in rows
        ]

    def record_event(self, event_type: str, payload: dict[str, Any]) -> int:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events (event_type, payload_json, created_at)
                VALUES (?, ?, ?)
                """,
                (event_type, json.dumps(payload, sort_keys=True), _utc_now()),
            )
            return int(cursor.lastrowid)

    def list_events(self) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, event_type, payload_json, created_at FROM events ORDER BY id"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
