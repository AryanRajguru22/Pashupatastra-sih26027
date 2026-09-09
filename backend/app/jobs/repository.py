from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Optional

from contracts import BlockStatus


# The production default stays the repository-root jobs.db so existing
# deployments and the running API are unchanged. The environment
# override exists so tests (and any future multi-corridor deployment)
# can point at their own file instead of sharing this one - importing
# backend.app.api.main constructs a JobService at module import, so
# without an override every test run touches the real database.
DEFAULT_DB_PATH = Path(
    os.getenv(
        "PASHUPAT_JOBS_DB",
        Path(__file__).resolve().parents[3] / "jobs.db",
    )
)


# Statuses from which a job can never return. A terminal job is
# excluded from optimization candidate assembly AND cannot have a new
# schedule written over it (see update_schedule). Both barriers are
# deliberate: excluding terminal jobs from list_active() alone was not
# enough, because update_schedule() used to force status back to
# 'scheduled', resurrecting completed work into the candidate set.
TERMINAL_STATUSES = ("completed",)


# Columns added after the original schema shipped. Applied additively
# and idempotently so an existing jobs.db keeps its rows.
_ADDED_COLUMNS = (
    ("updated_at", "TEXT"),
    ("last_solver_status", "TEXT"),
    ("last_refusal_reason", "TEXT"),
)


class TerminalJobError(RuntimeError):
    """Raised when a mutation would alter a job in a terminal status."""


class JobRepository:

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = str(db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with closing(self._connect()) as conn, conn:
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

            self._migrate(conn)

            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Additive, idempotent schema upgrade.

        A database created before Sprint 2 has none of the columns in
        _ADDED_COLUMNS. Adding them with ALTER TABLE preserves every
        existing row; re-running is a no-op because the column list is
        re-read each time.
        """

        existing = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(maintenance_jobs)"
            )
        }

        for column, column_type in _ADDED_COLUMNS:
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE maintenance_jobs "
                    f"ADD COLUMN {column} {column_type}"
                )

    def create(self, job: dict[str, Any]) -> dict[str, Any]:

        with closing(self._connect()) as conn, conn:

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
                    updated_at,
                    block_candidate_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    job.get("updated_at", job["created_at"]),
                    json.dumps(job["block_candidate"]),
                ),
            )

            conn.commit()

        return self.get(job["job_id"])

    def get(
        self,
        job_id: str,
    ) -> Optional[dict[str, Any]]:

        with closing(self._connect()) as conn, conn:

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

        with closing(self._connect()) as conn, conn:

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
        """Jobs eligible to take part in optimization.

        Filters against the explicit TERMINAL_STATUSES set rather than a
        hard-coded != 'completed', so a status added later cannot
        silently become "active" by omission.
        """

        placeholders = ", ".join("?" for _ in TERMINAL_STATUSES)

        with closing(self._connect()) as conn, conn:

            rows = conn.execute(
                f"""
                SELECT *
                FROM maintenance_jobs
                WHERE status NOT IN ({placeholders})
                ORDER BY created_at
                """,
                TERMINAL_STATUSES,
            ).fetchall()

        return [
            self._row_to_dict(row)
            for row in rows
        ]

    def list_by_status(
        self,
        status: str,
    ) -> list[dict[str, Any]]:

        with closing(self._connect()) as conn, conn:

            rows = conn.execute(
                """
                SELECT *
                FROM maintenance_jobs
                WHERE status = ?
                ORDER BY created_at DESC
                """,
                (status,),
            ).fetchall()

        return [
            self._row_to_dict(row)
            for row in rows
        ]

    def update_status(
        self,
        job_id: str,
        status: str,
        updated_at: str | None = None,
        block_status: str | None = None,
        is_committed: bool | None = None,
    ) -> Optional[dict[str, Any]]:
        """Move a job to a new status.

        `block_status` / `is_committed` mirror the transition into
        block_candidate_json so the persisted BlockCandidate stays
        consistent with the job row. Notifying a job is what makes the
        work operationally committed, and a later optimization
        reconstructs its committed block straight from this JSON.
        """

        with closing(self._connect()) as conn, conn:

            row = conn.execute(
                "SELECT block_candidate_json "
                "FROM maintenance_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()

            if row is None:
                return None

            if block_status is None and is_committed is None:
                conn.execute(
                    """
                    UPDATE maintenance_jobs
                    SET status = ?, updated_at = COALESCE(?, updated_at)
                    WHERE job_id = ?
                    """,
                    (status, updated_at, job_id),
                )

            else:
                block_candidate = json.loads(
                    row["block_candidate_json"]
                )

                if block_status is not None:
                    block_candidate["status"] = block_status

                if is_committed is not None:
                    block_candidate["is_committed"] = bool(is_committed)

                conn.execute(
                    """
                    UPDATE maintenance_jobs
                    SET
                        status = ?,
                        updated_at = COALESCE(?, updated_at),
                        block_candidate_json = ?
                    WHERE job_id = ?
                    """,
                    (
                        status,
                        updated_at,
                        json.dumps(block_candidate),
                        job_id,
                    ),
                )

            conn.commit()

        return self.get(job_id)

    def apply_optimization_outcome(
        self,
        scheduled: list[dict[str, Any]],
        refused: list[dict[str, Any]],
        updated_at: str,
        solver_status: str,
    ) -> None:
        """Persist one whole optimization batch in a single transaction.

        Either every job in the batch records its outcome or none does.
        Writing each job in its own transaction (the shape the per-job
        helpers above use) would let a crash mid-batch leave some jobs
        scheduled against a plan that was never fully applied.

        `scheduled` entries are {job_id, start_minute, end_minute};
        `refused` entries are {job_id, reason}. Terminal jobs are
        rejected before anything is written.
        """

        with closing(self._connect()) as conn, conn:

            job_ids = [entry["job_id"] for entry in scheduled]
            job_ids += [entry["job_id"] for entry in refused]

            rows = {}

            for job_id in job_ids:
                row = conn.execute(
                    "SELECT status, block_candidate_json "
                    "FROM maintenance_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()

                if row is None:
                    raise KeyError(
                        f"Job '{job_id}' not found"
                    )

                if row["status"] in TERMINAL_STATUSES:
                    raise TerminalJobError(
                        f"Job '{job_id}' is in terminal status "
                        f"'{row['status']}' and cannot be included in "
                        "an optimization outcome"
                    )

                rows[job_id] = row

            for entry in scheduled:
                job_id = entry["job_id"]

                block_candidate = json.loads(
                    rows[job_id]["block_candidate_json"]
                )

                metadata = dict(
                    block_candidate.get("metadata", {})
                )
                metadata["committed_start_minute"] = int(
                    entry["start_minute"]
                )
                metadata["committed_end_minute"] = int(
                    entry["end_minute"]
                )
                block_candidate["metadata"] = metadata
                block_candidate["status"] = (
                    BlockStatus.SCHEDULED.value
                )

                # A job already notified stays notified: field crews
                # have been told, and the solver preserved its
                # placement as committed work.
                next_status = (
                    rows[job_id]["status"]
                    if rows[job_id]["status"] == "notified"
                    else "scheduled"
                )

                conn.execute(
                    """
                    UPDATE maintenance_jobs
                    SET
                        schedule_start_minute = ?,
                        schedule_end_minute = ?,
                        status = ?,
                        updated_at = ?,
                        last_solver_status = ?,
                        last_refusal_reason = NULL,
                        block_candidate_json = ?
                    WHERE job_id = ?
                    """,
                    (
                        int(entry["start_minute"]),
                        int(entry["end_minute"]),
                        next_status,
                        updated_at,
                        solver_status,
                        json.dumps(block_candidate),
                        job_id,
                    ),
                )

            for entry in refused:
                # Status is deliberately untouched: an unscheduled job
                # stays eligible for the next optimization run. Only
                # the honest solver reason is recorded.
                conn.execute(
                    """
                    UPDATE maintenance_jobs
                    SET
                        updated_at = ?,
                        last_solver_status = ?,
                        last_refusal_reason = ?
                    WHERE job_id = ?
                    """,
                    (
                        updated_at,
                        solver_status,
                        entry["reason"],
                        entry["job_id"],
                    ),
                )

            conn.commit()

    def update_schedule(
        self,
        job_id: str,
        start_minute: int,
        end_minute: int,
        updated_at: str,
        solver_status: str | None = None,
    ) -> Optional[dict[str, Any]]:
        """Persist a solver-assigned schedule for one job.

        Writes the placement into BOTH representations, which must stay
        consistent: the schedule_* columns (what the API reports) and
        block_candidate_json (what a later optimization reconstructs a
        committed block from). Writing only the columns - the previous
        behaviour - left block_candidate_json claiming
        earliest_start_minute=0, so pinning the job as an existing
        committed block placed it at minute 0 instead of its real
        schedule, which is outside every possession window and made the
        whole re-optimization INFEASIBLE.
        """

        with closing(self._connect()) as conn, conn:

            row = conn.execute(
                "SELECT status, block_candidate_json "
                "FROM maintenance_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()

            if row is None:
                return None

            # Terminal work is never rescheduled. Without this guard a
            # completed job was silently returned to 'scheduled' and
            # re-entered the optimization candidate set.
            if row["status"] in TERMINAL_STATUSES:
                raise TerminalJobError(
                    f"Job '{job_id}' is in terminal status "
                    f"'{row['status']}' and cannot be rescheduled"
                )

            block_candidate = json.loads(row["block_candidate_json"])

            metadata = dict(block_candidate.get("metadata", {}))
            metadata["committed_start_minute"] = int(start_minute)
            metadata["committed_end_minute"] = int(end_minute)
            block_candidate["metadata"] = metadata

            block_candidate["status"] = BlockStatus.SCHEDULED.value

            conn.execute(
                """
                UPDATE maintenance_jobs
                SET
                    schedule_start_minute = ?,
                    schedule_end_minute = ?,
                    status = 'scheduled',
                    updated_at = ?,
                    last_solver_status = ?,
                    last_refusal_reason = NULL,
                    block_candidate_json = ?
                WHERE job_id = ?
                """,
                (
                    int(start_minute),
                    int(end_minute),
                    updated_at,
                    solver_status,
                    json.dumps(block_candidate),
                    job_id,
                ),
            )

            conn.commit()

        return self.get(job_id)

    def record_refusal(
        self,
        job_id: str,
        reason: str,
        updated_at: str,
        solver_status: str | None = None,
    ) -> Optional[dict[str, Any]]:
        """Record that optimization considered a job and did not schedule it.

        The job's status is deliberately left alone: an unscheduled job
        stays eligible for the next optimization run. Only the honest
        reason from the solver is recorded, so the outcome is never
        reported as a success.
        """

        with closing(self._connect()) as conn, conn:

            row = conn.execute(
                "SELECT status FROM maintenance_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()

            if row is None:
                return None

            if row["status"] in TERMINAL_STATUSES:
                raise TerminalJobError(
                    f"Job '{job_id}' is in terminal status "
                    f"'{row['status']}'"
                )

            conn.execute(
                """
                UPDATE maintenance_jobs
                SET
                    updated_at = ?,
                    last_solver_status = ?,
                    last_refusal_reason = ?
                WHERE job_id = ?
                """,
                (updated_at, solver_status, reason, job_id),
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