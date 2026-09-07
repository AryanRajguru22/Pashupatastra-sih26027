from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional


DEFAULT_DB_PATH = (
    Path(__file__).resolve().parents[3] / "jobs.db"
)


class JobRepository:

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = str(db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS maintenance_jobs (
                    job_id TEXT PRIMARY KEY,
                    track_id TEXT NOT NULL,
                    work_type TEXT NOT NULL,

                    distance_start REAL NOT NULL,
                    distance_end REAL NOT NULL,

                    workers_min INTEGER NOT NULL,
                    workers_max INTEGER NOT NULL,

                    description TEXT NOT NULL,

                    status TEXT NOT NULL,

                    priority_score REAL NOT NULL DEFAULT 0.0,
                    risk_score REAL NOT NULL DEFAULT 0.0,

                    schedule_start_minute INTEGER,
                    schedule_end_minute INTEGER,

                    created_at TEXT NOT NULL,

                    block_candidate_json TEXT NOT NULL
                )
                """
            )

            conn.commit()

    def create(self, job: dict[str, Any]) -> dict[str, Any]:

        with self._connect() as conn:

            conn.execute(
                """
                INSERT INTO maintenance_jobs (
                    job_id,
                    track_id,
                    work_type,
                    distance_start,
                    distance_end,
                    workers_min,
                    workers_max,
                    description,
                    status,
                    priority_score,
                    risk_score,
                    schedule_start_minute,
                    schedule_end_minute,
                    created_at,
                    block_candidate_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["job_id"],
                    job["track_id"],
                    job["work_type"],
                    job["distance_start"],
                    job["distance_end"],
                    job["workers_min"],
                    job["workers_max"],
                    job["description"],
                    job["status"],
                    job["priority_score"],
                    job["risk_score"],
                    job.get("schedule_start_minute"),
                    job.get("schedule_end_minute"),
                    job["created_at"],
                    json.dumps(job["block_candidate"]),
                ),
            )

            conn.commit()

        return self.get(job["job_id"])

    def get(
        self,
        job_id: str,
    ) -> Optional[dict[str, Any]]:

        with self._connect() as conn:

            row = conn.execute(
                """
                SELECT *
                FROM maintenance_jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_dict(row)

    def list_all(self) -> list[dict[str, Any]]:

        with self._connect() as conn:

            rows = conn.execute(
                """
                SELECT *
                FROM maintenance_jobs
                ORDER BY created_at DESC
                """
            ).fetchall()

        return [
            self._row_to_dict(row)
            for row in rows
        ]

    def list_active(self) -> list[dict[str, Any]]:

        with self._connect() as conn:

            rows = conn.execute(
                """
                SELECT *
                FROM maintenance_jobs
                WHERE status != 'completed'
                ORDER BY created_at
                """
            ).fetchall()

        return [
            self._row_to_dict(row)
            for row in rows
        ]

    def update_status(
        self,
        job_id: str,
        status: str,
    ) -> Optional[dict[str, Any]]:

        with self._connect() as conn:

            conn.execute(
                """
                UPDATE maintenance_jobs
                SET status = ?
                WHERE job_id = ?
                """,
                (status, job_id),
            )

            conn.commit()

        return self.get(job_id)

    def update_schedule(
        self,
        job_id: str,
        start_minute: int,
        end_minute: int,
    ) -> Optional[dict[str, Any]]:

        with self._connect() as conn:

            conn.execute(
                """
                UPDATE maintenance_jobs
                SET
                    schedule_start_minute = ?,
                    schedule_end_minute = ?,
                    status = 'scheduled'
                WHERE job_id = ?
                """,
                (
                    start_minute,
                    end_minute,
                    job_id,
                ),
            )

            conn.commit()

        return self.get(job_id)

    @staticmethod
    def _row_to_dict(
        row: sqlite3.Row,
    ) -> dict[str, Any]:

        result = dict(row)

        result["block_candidate"] = json.loads(
            result.pop("block_candidate_json")
        )

        return result