"""Append-only persistence for job lifecycle events (Slice 1).

The job_events table lives in the SAME database file as maintenance_jobs
and optimization_runs, and is created by JobRepository's own schema
bootstrap, so every isolated test database (and the runtime database,
when the application next starts) gets it automatically and additively.
Nothing here migrates, seeds or rewrites existing rows.

Immutability follows backend.app.audit.repository exactly:
  1. this module exposes no update or delete operation; and
  2. install_append_only_guards adds SQL triggers that abort any UPDATE
     or DELETE, from any connection.

Writing is only possible through append(conn, events), which takes the
caller's connection. That is deliberate: JobRepository appends events
inside the same transaction as the state change they describe, so a
transition and its history commit or roll back together.

Column layout mirrors JobEvent.to_dict(). Actor fields are separate
columns (not one JSON blob) so history can be queried by actor and role
without parsing, and so the stored actor kind can be checked against
the stored role on read.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from backend.app.jobs.events import JobEvent
from backend.app.persistence.append_only import install_append_only_guards


JOB_EVENTS_TABLE = "job_events"


@dataclass(frozen=True)
class StoredJobEvent:
    """An event as persisted: the event plus its storage sequence.

    sequence is the commit order (AUTOINCREMENT inside a write-locked
    transaction). It orders history; it is not part of the event's
    canonical content - see backend.app.jobs.events.
    """

    sequence: int
    event: JobEvent


def ensure_job_events_schema(conn: sqlite3.Connection) -> None:
    """Create job_events, its index and its append-only guards. Idempotent."""

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {JOB_EVENTS_TABLE} (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            schema_version INTEGER NOT NULL,
            entity_type TEXT NOT NULL,
            job_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,

            actor_id TEXT NOT NULL,
            actor_role TEXT NOT NULL,
            actor_kind TEXT NOT NULL,
            actor_assurance TEXT NOT NULL,

            reason TEXT,
            optimization_run_id TEXT,

            before_state_json TEXT,
            after_state_json TEXT,
            metadata_json TEXT NOT NULL
        )
        """
    )

    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {JOB_EVENTS_TABLE}_by_job "
        f"ON {JOB_EVENTS_TABLE} (job_id, sequence)"
    )

    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {JOB_EVENTS_TABLE}_by_run "
        f"ON {JOB_EVENTS_TABLE} (optimization_run_id)"
    )

    install_append_only_guards(conn, JOB_EVENTS_TABLE)


def _json_or_none(value) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True)


def append_events(conn: sqlite3.Connection, events: Iterable[JobEvent]) -> None:
    """Insert events using the caller's connection and transaction.

    Plain INSERT: a duplicate event_id raises IntegrityError rather than
    overwriting an existing record.
    """

    for event in events:
        record = event.to_dict()
        actor = record["actor"]

        conn.execute(
            f"""
            INSERT INTO {JOB_EVENTS_TABLE} (
                event_id,
                schema_version,
                entity_type,
                job_id,
                event_type,
                occurred_at,
                actor_id,
                actor_role,
                actor_kind,
                actor_assurance,
                reason,
                optimization_run_id,
                before_state_json,
                after_state_json,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["event_id"],
                record["schema_version"],
                record["entity_type"],
                record["job_id"],
                record["event_type"],
                record["occurred_at"],
                actor["actor_id"],
                actor["role"],
                actor["kind"],
                actor["assurance"],
                record["reason"],
                record["optimization_run_id"],
                _json_or_none(record["before_state"]),
                _json_or_none(record["after_state"]),
                json.dumps(record["metadata"], sort_keys=True),
            ),
        )


class JobHistoryRepository:
    """Read access to job lifecycle history. There is no write method.

    Writes happen only through append_events, inside a JobRepository
    transaction.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def list_for_job(self, job_id: str) -> list[StoredJobEvent]:
        """Every event for one job, in commit order."""

        return self._select("job_id", job_id)

    def list_for_run(self, optimization_run_id: str) -> list[StoredJobEvent]:
        """Every job event that references one optimization run."""

        return self._select("optimization_run_id", optimization_run_id)

    def _select(self, column: str, value: str) -> list[StoredJobEvent]:
        # column is one of two literals above, never caller input.
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                f"SELECT * FROM {JOB_EVENTS_TABLE} "
                f"WHERE {column} = ? ORDER BY sequence",
                (value,),
            ).fetchall()

        return [
            StoredJobEvent(
                sequence=int(row["sequence"]),
                event=self._row_to_event(row),
            )
            for row in rows
        ]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> JobEvent:
        before = row["before_state_json"]
        after = row["after_state_json"]

        return JobEvent.from_dict(
            {
                "schema_version": row["schema_version"],
                "event_id": row["event_id"],
                "job_id": row["job_id"],
                "event_type": row["event_type"],
                "occurred_at": row["occurred_at"],
                "actor": {
                    "actor_id": row["actor_id"],
                    "role": row["actor_role"],
                    "kind": row["actor_kind"],
                    "assurance": row["actor_assurance"],
                },
                "reason": row["reason"],
                "optimization_run_id": row["optimization_run_id"],
                "before_state": None if before is None else json.loads(before),
                "after_state": None if after is None else json.loads(after),
                "metadata": json.loads(row["metadata_json"]),
            }
        )


__all__ = [
    "JOB_EVENTS_TABLE",
    "JobHistoryRepository",
    "StoredJobEvent",
    "append_events",
    "ensure_job_events_schema",
]
