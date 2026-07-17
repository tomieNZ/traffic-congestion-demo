"""SQLite-backed audit logging for the local demonstration.

SQLite keeps the project self-contained: no database server is required, and a
reviewer can inspect the resulting ``traffic_demo.sqlite3`` file after a run.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLog:
    """Persist security and classification events in a tiny SQLite table."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with row-style results for readable API output."""

        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialise(self) -> None:
        """Create the audit table once; SQLite handles repeat starts safely."""

        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    road_id TEXT,
                    outcome TEXT NOT NULL,
                    details_json TEXT NOT NULL
                )
                """
            )

    def record(
        self,
        *,
        actor: str,
        event_type: str,
        outcome: str,
        road_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append one immutable event with JSON details for later review."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events
                    (created_at, actor, event_type, road_id, outcome, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    actor,
                    event_type,
                    road_id,
                    outcome,
                    json.dumps(details or {}, sort_keys=True),
                ),
            )

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the newest audit events in display-friendly dictionaries."""

        safe_limit = max(1, min(int(limit), 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, actor, event_type, road_id, outcome, details_json
                FROM audit_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()

        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "actor": row["actor"],
                "event_type": row["event_type"],
                "road_id": row["road_id"],
                "outcome": row["outcome"],
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

