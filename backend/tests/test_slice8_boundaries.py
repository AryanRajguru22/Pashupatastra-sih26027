"""Sprint 3 Slice 8: what must NOT have moved.

Slice 8 is intake hardening only. These tests pin the surfaces the slice
is forbidden to change - lifecycle vocabulary, storage schema, the v1
request's required fields, authorization actions - so a later change to
any of them is a deliberate act with a failing test, not a side effect.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path

from backend.app.identity.authorization import JobAction
from backend.app.jobs.events import JobEventType
from backend.app.jobs.lifecycle import ALLOWED_TRANSITIONS
from backend.app.jobs.models import JobCreateRequest, JobStatus
from backend.app.jobs.repository import JobRepository


def test_job_event_types_are_unchanged():
    assert [e.name for e in JobEventType] == [
        "JOB_CREATED",
        "JOB_SCORED",
        "OPTIMIZATION_REQUESTED",
        "OPTIMIZATION_COMPLETED",
        "OPTIMIZATION_FAILED",
        "BLOCK_PROPOSED",
        "BLOCK_REPROPOSED",
        "OPTIMIZATION_REFUSED",
        "PROPOSAL_INVALIDATED",
        "COMMITTED_BLOCK_PRESERVED",
        "COMMITTED_BLOCK_CONFLICT",
        "SCHEDULE_ASSIGNED",
        "BLOCK_COMMITTED",
        "JOB_COMPLETED",
        "PROPOSAL_REJECTED",
        "PROPOSAL_POSTPONED",
        "TRANSITION_REJECTED",
        "EXECUTION_STARTED",
        "EXECUTION_COMPLETED",
        "EXECUTION_NOT_COMPLETED",
        "BLOCK_RELEASED",
    ]


def test_job_statuses_and_transitions_are_unchanged():
    assert [s.value for s in JobStatus] == [
        "reported",
        "scheduled",
        "notified",
        "in_progress",
        "completed",
    ]

    assert {
        (k.value if hasattr(k, "value") else k): sorted(
            v.value if hasattr(v, "value") else v for v in targets
        )
        for k, targets in ALLOWED_TRANSITIONS.items()
    } == {
        "reported": ["reported", "scheduled"],
        "scheduled": ["notified", "reported", "scheduled"],
        "notified": ["in_progress", "notified", "reported"],
        "in_progress": ["completed", "in_progress", "reported"],
        "completed": [],
    }


def test_no_new_authorization_action():
    assert [a.name for a in JobAction] == [
        "REPORT_JOB",
        "REQUEST_OPTIMIZATION",
        "ASSIGN_SCHEDULE",
        "COMMIT_BLOCK",
        "COMPLETE_JOB",
        "READ_JOB_HISTORY",
        "READ_BLOCK_PROPOSAL",
        "REJECT_PROPOSAL",
        "POSTPONE_PROPOSAL",
        "START_EXECUTION",
        "REPORT_EXECUTION_NOT_COMPLETED",
        "READ_JOB_EXECUTION",
        "RELEASE_COMMITTED_BLOCK",
        # Slice 9 added exactly two, both READS, both passing the
        # same unenforced seam as every other read. No write action
        # was added, renamed or removed.
        "READ_JOB_OBLIGATIONS",
        "READ_OPTIMIZATION_RUN",
    ]


def test_the_storage_schema_is_unchanged(tmp_path):
    JobRepository(tmp_path / "jobs.db")

    with closing(sqlite3.connect(tmp_path / "jobs.db")) as conn:
        objects = set(
            conn.execute("SELECT type, name FROM sqlite_master").fetchall()
        )
        jobs_columns = [r[1] for r in conn.execute("PRAGMA table_info(maintenance_jobs)")]
        events_columns = [r[1] for r in conn.execute("PRAGMA table_info(job_events)")]

    assert objects == {
        ("index", "job_events_by_job"),
        ("index", "job_events_by_run"),
        ("index", "sqlite_autoindex_job_events_1"),
        ("index", "sqlite_autoindex_maintenance_jobs_1"),
        ("table", "job_events"),
        ("table", "maintenance_jobs"),
        ("table", "sqlite_sequence"),
        ("trigger", "job_events_no_delete"),
        ("trigger", "job_events_no_update"),
    }
    assert jobs_columns == [
        "job_id",
        "track_id",
        "work_type",
        "distance_start",
        "distance_end",
        "workers_min",
        "workers_max",
        "description",
        "status",
        "priority_score",
        "risk_score",
        "schedule_start_minute",
        "schedule_end_minute",
        "created_at",
        "block_candidate_json",
        "updated_at",
        "last_solver_status",
        "last_refusal_reason",
    ]
    assert events_columns == [
        "sequence",
        "event_id",
        "schema_version",
        "entity_type",
        "job_id",
        "event_type",
        "occurred_at",
        "actor_id",
        "actor_role",
        "actor_kind",
        "actor_assurance",
        "reason",
        "optimization_run_id",
        "before_state_json",
        "after_state_json",
        "metadata_json",
    ]


def test_slice8_modules_contain_no_ddl():
    """No CREATE/ALTER/DROP anywhere in the new intake modules."""

    jobs_dir = Path(__file__).resolve().parents[1] / "app" / "jobs"
    ddl = re.compile(r"\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|TRIGGER|VIEW)\b", re.I)

    for name in (
        "asset_association.py",
        "duplicate_detection.py",
        "field_location.py",
        "idempotency.py",
    ):
        assert not ddl.search((jobs_dir / name).read_text(encoding="utf-8")), name


def test_the_v1_request_required_fields_are_unchanged():
    assert sorted(JobCreateRequest.model_json_schema()["required"]) == [
        "description",
        "distance_end",
        "distance_start",
        "job_type",
        "track_id",
        "workers_max",
        "workers_min",
    ]


def test_slice8_leaves_the_optimizer_and_contracts_alone():
    """The optimizer and shared contracts do not import any Slice 8 module."""

    root = Path(__file__).resolve().parents[2]
    slice8 = (
        "asset_association",
        "duplicate_detection",
        "field_location",
        "idempotency",
    )

    for directory in (root / "backend" / "app" / "optimizer", root / "contracts"):
        for path in directory.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert not any(f"jobs.{name}" in text for name in slice8), path
