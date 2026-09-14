"""Append-only persistence for optimization audit records.

Additive to the existing jobs database: this module creates its own
`optimization_runs` table and never touches `maintenance_jobs`. It is
deliberately paired with whatever JobRepository db_path is already in
use (see JobOptimizationService in backend/app/jobs/optimization.py)
rather than resolving its own default independently, so a test that
points JobRepository at a tmp_path database gets its audit rows in
that same isolated file instead of a shared default.

Immutability is enforced twice, deliberately redundantly:

1. This class exposes no update or delete method. There is no code
   path in this repository that can alter or remove a row.
2. SQL triggers on the table itself reject UPDATE and DELETE even if
   something outside this class (a script, a future migration, a
   direct sqlite3 connection) tries. Belt and suspenders: (1) alone
   only constrains callers who go through this class.

`record()` uses plain INSERT against a TEXT PRIMARY KEY, so writing a
run_id that already exists raises sqlite3.IntegrityError rather than
silently overwriting the earlier record.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Optional

from backend.app.audit.models import OptimizationRunRecord
from backend.app.jobs.repository import DEFAULT_DB_PATH
from backend.app.persistence.append_only import install_append_only_guards


class AuditRepository:

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
                CREATE TABLE IF NOT EXISTS optimization_runs (
                    run_id TEXT PRIMARY KEY,
                    actor TEXT NOT NULL,
                    trigger_name TEXT NOT NULL,
                    corridor_id TEXT,

                    requested_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,

                    solver_status TEXT NOT NULL,
                    solve_time_seconds REAL,

                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    provenance_snapshot_json TEXT,

                    error TEXT
                )
                """
            )

            # Defense in depth: reject mutation at the SQL layer too,
            # independent of anything this class does or doesn't
            # expose as a method. Shared with the job lifecycle history
            # table (backend.app.jobs.history) so both are protected by
            # one mechanism.
            install_append_only_guards(conn, "optimization_runs")

            conn.commit()

    def record(self, run: OptimizationRunRecord) -> None:
        """Insert one immutable audit record.

        Plain INSERT, not INSERT OR REPLACE: a duplicate run_id raises
        sqlite3.IntegrityError instead of overwriting the existing row.
        Every optimization execution must mint its own run_id (see
        audit/service.py), so a collision here means a caller bug, not
        a legitimate re-run.
        """

        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO optimization_runs (
                    run_id,
                    actor,
                    trigger_name,
                    corridor_id,
                    requested_at,
                    completed_at,
                    solver_status,
                    solve_time_seconds,
                    request_json,
                    result_json,
                    provenance_snapshot_json,
                    error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.actor,
                    run.trigger,
                    run.corridor_id,
                    run.requested_at,
                    run.completed_at,
                    run.solver_status,
                    run.solve_time_seconds,
                    run.request_json,
                    run.result_json,
                    run.provenance_snapshot_json,
                    run.error,
                ),
            )

            conn.commit()

    def get(self, run_id: str) -> Optional[OptimizationRunRecord]:

        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT * FROM optimization_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_record(row)

    def list_all(self) -> list[OptimizationRunRecord]:

        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT * FROM optimization_runs ORDER BY requested_at"
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def list_by_corridor(
        self,
        corridor_id: str,
    ) -> list[OptimizationRunRecord]:

        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                """
                SELECT * FROM optimization_runs
                WHERE corridor_id = ?
                ORDER BY requested_at
                """,
                (corridor_id,),
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> OptimizationRunRecord:
        return OptimizationRunRecord(
            run_id=row["run_id"],
            actor=row["actor"],
            trigger=row["trigger_name"],
            corridor_id=row["corridor_id"],
            requested_at=row["requested_at"],
            completed_at=row["completed_at"],
            solver_status=row["solver_status"],
            solve_time_seconds=row["solve_time_seconds"],
            request_json=row["request_json"],
            result_json=row["result_json"],
            provenance_snapshot_json=row["provenance_snapshot_json"],
            error=row["error"],
        )
