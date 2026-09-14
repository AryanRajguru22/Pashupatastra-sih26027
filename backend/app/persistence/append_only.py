"""SQL-level append-only protection, shared by every audit/history table.

Extracted from backend.app.audit.repository (which introduced it for
optimization_runs) so the job lifecycle history table enforces
immutability the SAME way rather than through a second, subtly different
mechanism. The generated trigger names and messages are exactly the
ones optimization_runs has always had.

This lives outside both backend.app.audit and backend.app.jobs on
purpose: backend.app.audit.repository imports backend.app.jobs.repository
(for DEFAULT_DB_PATH), so a helper inside the audit package could not be
imported by the jobs package without a cycle.

What it guarantees: an UPDATE or DELETE against the table aborts, from
any connection, including one that bypasses every repository class. What
it does not guarantee: protection against someone replacing or editing
the database file offline, or dropping the trigger first. Detecting that
is the job of the future hash-chained audit, not of triggers.
"""

from __future__ import annotations

import re
import sqlite3

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def install_append_only_guards(conn: sqlite3.Connection, table: str) -> None:
    """Create BEFORE UPDATE / BEFORE DELETE abort triggers on `table`.

    Idempotent (CREATE TRIGGER IF NOT EXISTS). `table` is interpolated
    into DDL, so it is validated as a plain identifier - it only ever
    comes from a module constant, but DDL is not the place to trust that.
    """

    if not _IDENTIFIER.match(table):
        raise ValueError(f"Invalid table identifier {table!r}")

    conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS {table}_no_update
        BEFORE UPDATE ON {table}
        BEGIN
            SELECT RAISE(ABORT,
                '{table} is append-only: UPDATE is not permitted');
        END
        """
    )

    conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS {table}_no_delete
        BEFORE DELETE ON {table}
        BEGIN
            SELECT RAISE(ABORT,
                '{table} is append-only: DELETE is not permitted');
        END
        """
    )


__all__ = ["install_append_only_guards"]
