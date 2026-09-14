from __future__ import annotations

import copy
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

from contracts import BlockStatus

from backend.app.jobs.events import JobEvent
from backend.app.jobs.history import append_events, ensure_job_events_schema
from backend.app.jobs.lifecycle import (
    TERMINAL_STATUSES,
    OptimizationAttempt,
    Plan,
    TerminalJobError,
    plan_optimization_outcome,
    protect_committed_and_terminal_state,
    validate_mutation,
)


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


# TERMINAL_STATUSES and TerminalJobError are defined in
# backend.app.jobs.lifecycle (the lifecycle rules module) and re-exported
# here so every existing `from backend.app.jobs.repository import ...`
# keeps working. A terminal job is excluded from optimization candidate
# assembly AND can never be written again.


# Columns added after the original schema shipped. Applied additively
# and idempotently so an existing jobs.db keeps its rows.
_ADDED_COLUMNS = (
    ("updated_at", "TEXT"),
    ("last_solver_status", "TEXT"),
    ("last_refusal_reason", "TEXT"),
)


class JobRepository:
    """Persistence for maintenance jobs and their lifecycle history.

    TWO KINDS OF WRITE, DELIBERATELY SEPARATED
        mutate_jobs / create(job, events) / append_events
            The service path (Slice 1). Each call is ONE write-locked
            transaction (BEGIN IMMEDIATE) that reads the current rows,
            lets a lifecycle plan decide the new values and events,
            validates every mutation, writes the rows and appends the
            events. State and history commit or roll back together.

        update_status / update_schedule / record_refusal /
        apply_optimization_outcome
            Pre-Slice-1 primitives kept for compatibility (existing tests
            call them directly). NOT production mutation paths: no
            module under backend/app calls them (record_refusal has no
            caller at all), and test_production_code_never_calls_
            history_less_repository_writes fails if one ever does. They
            write NO lifecycle history, but they are not a way around the
            protected-state invariants: update_status, update_schedule
            and apply_optimization_outcome refuse to touch a terminal
            job, to weaken committed work, or to write a job whose
            committed state is already inconsistent
            (lifecycle.protect_committed_and_terminal_state).
            record_refusal refuses terminal jobs and writes only the
            last_solver_status / last_refusal_reason columns.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = str(db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        """One write-locked transaction, closed on exit.

        BEGIN IMMEDIATE takes SQLite's write lock before the first read,
        so a read-check-write sequence cannot interleave with another
        writer - including one in a different process, which the
        in-process lifecycle lock cannot see.
        """

        conn = self._connect()

        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

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

            # Additive: the lifecycle history table sits beside
            # maintenance_jobs in the same file. maintenance_jobs'
            # own columns are unchanged.
            ensure_job_events_schema(conn)

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

    # ------------------------------------------------------------------
    # Service write path
    # ------------------------------------------------------------------

    def create(
        self,
        job: dict[str, Any],
        events: Iterable[JobEvent] = (),
    ) -> dict[str, Any]:
        """Insert a new job and its creation events in one transaction."""

        with self._write_transaction() as conn:

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

            append_events(conn, events)

        return self.get(job["job_id"])

    def mutate_jobs(
        self,
        job_ids: Sequence[str],
        plan: Plan,
        *,
        reject_terminal: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """Apply one lifecycle plan to jobs atomically, with its events.

        Inside a single BEGIN IMMEDIATE transaction:
          1. read every job (KeyError if any is missing);
          2. optionally refuse the whole call if any job is terminal;
          3. call plan(rows) -> (mutations, events);
          4. validate every mutation against the lifecycle rules;
          5. write the mutations and append the events.

        Any exception - from the plan, a validation, or SQLite - rolls
        the whole transaction back: no row changes and no event is kept.
        """

        with self._write_transaction() as conn:
            rows: dict[str, dict[str, Any]] = {}

            for job_id in job_ids:
                row = conn.execute(
                    "SELECT * FROM maintenance_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()

                if row is None:
                    raise KeyError(f"Job '{job_id}' not found")

                rows[job_id] = self._row_to_dict(row)

            if reject_terminal:
                for job_id, job in rows.items():
                    if job["status"] in TERMINAL_STATUSES:
                        raise TerminalJobError(
                            f"Job '{job_id}' is in terminal status "
                            f"'{job['status']}' and cannot be included in "
                            "an optimization outcome"
                        )

            mutations, events = plan(rows)

            for mutation in mutations:
                if mutation.job_id not in rows:
                    raise KeyError(
                        f"Plan mutated job '{mutation.job_id}', which was "
                        "not read in this transaction"
                    )

                validate_mutation(rows[mutation.job_id], mutation)

                conn.execute(
                    """
                    UPDATE maintenance_jobs
                    SET
                        status = ?,
                        schedule_start_minute = ?,
                        schedule_end_minute = ?,
                        updated_at = ?,
                        last_solver_status = ?,
                        last_refusal_reason = ?,
                        block_candidate_json = ?
                    WHERE job_id = ?
                    """,
                    (
                        mutation.status,
                        mutation.schedule_start_minute,
                        mutation.schedule_end_minute,
                        mutation.updated_at,
                        mutation.last_solver_status,
                        mutation.last_refusal_reason,
                        json.dumps(mutation.block_candidate),
                        mutation.job_id,
                    ),
                )

            append_events(conn, events)

        return {job_id: self.get(job_id) for job_id in job_ids}

    def append_events(self, events: Iterable[JobEvent]) -> None:
        """Record events that accompany no state change (e.g. a refusal)."""

        with self._write_transaction() as conn:
            append_events(conn, events)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Pre-Slice-1 primitives (no lifecycle history; see class docstring)
    # ------------------------------------------------------------------

    def update_status(
        self,
        job_id: str,
        status: str,
        updated_at: str | None = None,
        block_status: str | None = None,
        is_committed: bool | None = None,
    ) -> Optional[dict[str, Any]]:
        """Move a job to a new status. Writes no lifecycle history.

        `block_status` / `is_committed` mirror the transition into
        block_candidate_json so the persisted BlockCandidate stays
        consistent with the job row.
        """

        with self._write_transaction() as conn:

            row = conn.execute(
                "SELECT * FROM maintenance_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()

            if row is None:
                return None

            current = self._row_to_dict(row)
            # A copy: the guard below must see the STORED block in
            # `current`, not one already edited toward the new value.
            block_candidate = copy.deepcopy(current["block_candidate"])

            if block_status is not None:
                block_candidate["status"] = block_status

            if is_committed is not None:
                block_candidate["is_committed"] = bool(is_committed)

            protect_committed_and_terminal_state(
                current,
                status,
                block_candidate,
                (
                    current["schedule_start_minute"],
                    current["schedule_end_minute"],
                ),
            )

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

        return self.get(job_id)

    def apply_optimization_outcome(
        self,
        scheduled: list[dict[str, Any]],
        refused: list[dict[str, Any]],
        updated_at: str,
        solver_status: str,
    ) -> None:
        """Persist one whole optimization batch in a single transaction.

        Compatibility wrapper, writing no lifecycle history. It applies
        exactly the same outcome rules as the service path
        (lifecycle.plan_optimization_outcome) - including withdrawing a
        stale uncommitted proposal and keeping committed blocks
        COMMITTED - so the two can never disagree about what an outcome
        means. JobOptimizationService does not call it.

        `scheduled` entries are {job_id, start_minute, end_minute};
        `refused` entries are {job_id, reason}. Terminal jobs are
        rejected before anything is written.
        """

        job_ids = [entry["job_id"] for entry in scheduled]
        job_ids += [entry["job_id"] for entry in refused]

        plan = plan_optimization_outcome(
            job_ids,
            attempt=OptimizationAttempt(
                corridor_id="",
                requester=None,
                requested_at=updated_at,
                run_id=None,
                horizon_start="",
            ),
            completed_at=updated_at,
            solver_status=solver_status,
            placements={entry["job_id"]: entry for entry in scheduled},
            refusals={entry["job_id"]: entry["reason"] for entry in refused},
            record_history=False,
        )

        self.mutate_jobs(job_ids, plan, reject_terminal=True)

    def update_schedule(
        self,
        job_id: str,
        start_minute: int,
        end_minute: int,
        updated_at: str,
        solver_status: str | None = None,
    ) -> Optional[dict[str, Any]]:
        """Persist a schedule for one job. Writes no lifecycle history.

        Writes the placement into BOTH representations, which must stay
        consistent: the schedule_* columns (what the API reports) and
        block_candidate_json (what a later optimization reconstructs a
        committed block from). Writing only the columns - the previous
        behaviour - left block_candidate_json claiming
        earliest_start_minute=0, so pinning the job as an existing
        committed block placed it at minute 0 instead of its real
        schedule, which is outside every possession window and made the
        whole re-optimization INFEASIBLE.

        Refuses a terminal job (TerminalJobError) and a committed job
        (CommittedJobError): forcing either back to 'scheduled' would
        resurrect terminal work or silently un-pin committed work.
        """

        with self._write_transaction() as conn:

            row = conn.execute(
                "SELECT * FROM maintenance_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()

            if row is None:
                return None

            current = self._row_to_dict(row)
            block_candidate = copy.deepcopy(current["block_candidate"])

            metadata = dict(block_candidate.get("metadata", {}))
            metadata["committed_start_minute"] = int(start_minute)
            metadata["committed_end_minute"] = int(end_minute)
            block_candidate["metadata"] = metadata

            block_candidate["status"] = BlockStatus.SCHEDULED.value

            protect_committed_and_terminal_state(
                current,
                "scheduled",
                block_candidate,
                (int(start_minute), int(end_minute)),
            )

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

        return self.get(job_id)

    def record_refusal(
        self,
        job_id: str,
        reason: str,
        updated_at: str,
        solver_status: str | None = None,
    ) -> Optional[dict[str, Any]]:
        """Record that optimization considered a job and did not schedule it.

        Writes no lifecycle history and does not change status. Not used
        by the service path, which withdraws a stale uncommitted proposal
        on refusal (see lifecycle.plan_optimization_outcome).
        """

        with self._write_transaction() as conn:

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


__all__ = [
    "DEFAULT_DB_PATH",
    "JobRepository",
    "TERMINAL_STATUSES",
    "TerminalJobError",
]
