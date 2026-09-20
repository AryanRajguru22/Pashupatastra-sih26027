"""Sprint 3 Slice 9: what must NOT happen, and what must NOT have moved.

Two families of property, both safety-critical:

  SAFETY    Time is not an actor. Reading an obligation, escalating one,
            deriving an intent or handing one to a channel must never
            change a job's status, its row, its history or the audit
            trail - not at any elapsed time, however extreme. The
            precedent is ExecutionDeviations: "recorded, never enforced".

  SECURITY  An authority decision - approve, reject, postpone - cannot be
            recorded against UNIDENTIFIED or SYSTEM. This is
            accountability, not authentication: the identity stays
            DECLARED_UNVERIFIED, and no AUTHENTICATED level is
            introduced.

Plus the boundary list Slice 9 was forbidden to cross: no new status, no
new event type, no new lifecycle transition, no schema change, no
scheduler.

Every test uses an isolated temporary database.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts import DEFAULT_HORIZON_START

from backend.app.identity.actor import (
    SYSTEM,
    ActorRole,
    human_actor,
    unidentified_actor,
)
from backend.app.jobs.events import JobEventType
from backend.app.jobs.lifecycle import InvalidTransitionError, proposal_run_id_of
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.notifications import (
    RecordingChannel,
    derive_notification_intents,
    record_all,
)
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService


WORKER = human_actor("WORKER-042", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-003", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-017", ActorRole.AUTHORITY)

CORRIDOR = "CORRIDOR_A"
E = JobEventType

START = datetime.fromisoformat(DEFAULT_HORIZON_START) + timedelta(days=3650)

JOBS_DIR = Path(__file__).resolve().parents[1] / "app" / "jobs"
SLICE9_MODULES = ("sla_policy.py", "obligations.py", "notifications.py")


class MovableClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now = self.now + timedelta(**kwargs)
        return self.now


@pytest.fixture()
def clock():
    return MovableClock(START)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "jobs.db"


@pytest.fixture()
def service(db_path, clock):
    return JobService(repository=JobRepository(db_path), clock=clock)


@pytest.fixture()
def optimizer(service):
    return JobOptimizationService(service)


def report(service, distance_start=1000.0):
    return service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=distance_start,
            distance_end=distance_start + 400,
            workers_min=2,
            workers_max=4,
            description="Rail head crack observed during foot patrol",
            severity="CRITICAL",
        ),
        actor=WORKER,
    )


def scheduled_job(service, optimizer, **kwargs):
    job = report(service, **kwargs)
    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    return service.repository.get(job["job_id"])


def raw_row(db_path, job_id):
    with closing(sqlite3.connect(db_path)) as conn:
        return tuple(
            conn.execute(
                "SELECT * FROM maintenance_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        )


def count_events(db_path):
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]


def count_runs(db_path):
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM optimization_runs").fetchone()[0]


def snapshot(service, db_path, job_id):
    return (raw_row(db_path, job_id), count_events(db_path), count_runs(db_path))


# ----------------------------------------------------------------------
# SAFETY: time is not an actor
# ----------------------------------------------------------------------


@pytest.mark.parametrize("days", [0, 1, 30, 365, 3650])
def test_however_late_an_obligation_gets_the_job_never_moves(
    service, optimizer, clock, db_path, days
):
    """The non-negotiable property: escalation has no lifecycle authority."""

    job = scheduled_job(service, optimizer)
    job_id = job["job_id"]
    before = snapshot(service, db_path, job_id)

    clock.advance(days=days)
    obligation = service.job_obligation(job_id)

    assert snapshot(service, db_path, job_id) == before
    assert service.repository.get(job_id)["status"] == "scheduled"
    # Escalating, specifically, approved nothing.
    assert E.BLOCK_COMMITTED not in [
        item.event.event_type for item in service.history.list_for_job(job_id)
    ]
    assert obligation is not None


def test_reading_obligations_repeatedly_writes_nothing(
    service, optimizer, clock, db_path
):
    job = scheduled_job(service, optimizer)
    job_id = job["job_id"]
    before = snapshot(service, db_path, job_id)

    for _ in range(10):
        clock.advance(hours=6)
        service.job_obligation(job_id)
        service.obligations_page(limit=50)

    assert snapshot(service, db_path, job_id) == before


def test_deriving_and_recording_intents_changes_nothing(
    service, optimizer, clock, db_path
):
    job = scheduled_job(service, optimizer)
    job_id = job["job_id"]

    clock.advance(days=10)
    before = snapshot(service, db_path, job_id)

    obligations, _, _ = service.obligations_page(limit=50)
    intents = derive_notification_intents(obligations)
    results = record_all(intents, RecordingChannel(clock=clock))

    assert intents and results  # the escalation really was raised
    assert snapshot(service, db_path, job_id) == before
    assert service.repository.get(job_id)["status"] == "scheduled"


def test_an_escalated_proposal_is_still_committable_afterwards(
    service, optimizer, clock
):
    """Nothing expires. A late decision is still a decision the system takes."""

    job = scheduled_job(service, optimizer)
    job_id = job["job_id"]

    clock.advance(days=100)
    assert service.job_obligation(job_id).state.value == "ESCALATED_L3"

    committed = service.approve_proposal(
        job_id,
        actor=AUTHORITY,
        expected_proposal_run_id=proposal_run_id_of(job),
    )

    assert committed["status"] == "notified"


@pytest.mark.parametrize("days", [1, 400, 10_000])
def test_a_moot_obligation_never_becomes_overdue_over_the_real_stack(
    service, optimizer, clock, days
):
    """A system withdrawal is never an authority failure - at any elapsed time."""

    job = scheduled_job(service, optimizer)
    job_id = job["job_id"]

    # Releasing an approved block is the authority withdrawing its own
    # commitment; the obligation ENDS rather than being breached.
    service.approve_proposal(
        job_id, actor=AUTHORITY, expected_proposal_run_id=proposal_run_id_of(job)
    )
    service.release_committed_block(
        job_id,
        actor=AUTHORITY,
        expected_proposal_run_id=proposal_run_id_of(job),
        reason="possession not granted by section controller",
    )

    clock.advance(days=days)
    obligation = service.job_obligation(job_id)

    assert obligation.is_past_due is False
    assert obligation.escalation_level == 0
    assert derive_notification_intents([obligation])[0].intent_type.value == (
        "REPLANNING_PENDING"
    )


def test_an_old_proposal_cannot_generate_a_current_obligation(
    service, optimizer, clock
):
    job = scheduled_job(service, optimizer)
    job_id = job["job_id"]
    first_run = proposal_run_id_of(job)

    clock.advance(days=5)
    stale = service.job_obligation(job_id)
    assert stale.optimization_run_id == first_run

    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    fresh = service.job_obligation(job_id)

    assert fresh.optimization_run_id != first_run
    assert fresh.is_past_due is False
    assert fresh.anchor_event_id != stale.anchor_event_id


# ----------------------------------------------------------------------
# SECURITY: an authority decision needs a named person
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "actor",
    [
        pytest.param(None, id="omitted"),
        pytest.param(unidentified_actor(), id="explicit-unidentified"),
        pytest.param(SYSTEM, id="system"),
    ],
)
@pytest.mark.parametrize("decision", ["approve", "notify", "reject", "postpone"])
def test_an_authority_decision_requires_an_identified_human(
    service, optimizer, db_path, actor, decision
):
    job = scheduled_job(service, optimizer)
    job_id = job["job_id"]
    run_id = proposal_run_id_of(job)
    before = snapshot(service, db_path, job_id)

    calls = {
        "approve": lambda: service.approve_proposal(
            job_id, actor=actor, expected_proposal_run_id=run_id
        ),
        "notify": lambda: service.notify(
            job_id, actor=actor, expected_proposal_run_id=run_id
        ),
        "reject": lambda: service.reject_proposal(
            job_id, actor=actor, expected_proposal_run_id=run_id, reason="unsafe"
        ),
        "postpone": lambda: service.postpone_proposal(
            job_id,
            actor=actor,
            expected_proposal_run_id=run_id,
            reason="crew unavailable",
            selected_date="2026-09-11",
        ),
    }

    with pytest.raises(InvalidTransitionError):
        calls[decision]()

    # The row and the audit trail are untouched; only the refusal itself
    # may have been recorded.
    row_before, events_before, runs_before = before
    assert raw_row(db_path, job_id) == row_before
    assert count_runs(db_path) == runs_before
    assert count_events(db_path) in (events_before, events_before + 1)
    assert service.repository.get(job_id)["status"] == "scheduled"


@pytest.mark.parametrize("role", [ActorRole.AUTHORITY, ActorRole.WORKER, ActorRole.ENGINEER])
def test_the_guard_is_accountability_not_role_authorization(
    service, optimizer, role
):
    """Any NAMED human may commit. Which role may is a policy question,
    and no policy that restricts anything ships - see
    backend.app.identity.authorization."""

    job = scheduled_job(service, optimizer)

    committed = service.approve_proposal(
        job["job_id"],
        actor=human_actor(f"{role.value}-001", role),
        expected_proposal_run_id=proposal_run_id_of(job),
    )

    assert committed["status"] == "notified"


def test_an_approval_by_a_named_human_stays_declared_unverified(service, optimizer):
    """The boundary does not move: naming yourself is not authenticating."""

    job = scheduled_job(service, optimizer)
    job_id = job["job_id"]

    service.approve_proposal(
        job_id, actor=AUTHORITY, expected_proposal_run_id=proposal_run_id_of(job)
    )

    committed = [
        item.event
        for item in service.history.list_for_job(job_id)
        if item.event.event_type is E.BLOCK_COMMITTED
    ][-1]

    assert committed.actor.actor_id == "AUTHORITY-017"
    assert committed.actor.assurance.value == "DECLARED_UNVERIFIED"


def test_no_authenticated_assurance_level_was_introduced():
    from backend.app.identity.actor import IdentityAssurance

    assert {level.value for level in IdentityAssurance} == {
        "SYSTEM_INTERNAL",
        "DECLARED_UNVERIFIED",
        "NONE",
    }


def test_reporting_a_defect_still_needs_no_identity(service):
    """Deliberately unguarded: an unidentified report beats a lost one."""

    job = service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="ROUTINE_INSPECTION",
            distance_start=5000.0,
            distance_end=5200.0,
            workers_min=1,
            workers_max=2,
            description="in-process caller with no actor",
        )
    )

    created = service.history.list_for_job(job["job_id"])[0].event

    assert created.actor.actor_id == "UNIDENTIFIED"
    assert created.actor.assurance.value == "NONE"


def test_an_escalation_cannot_be_injected_by_a_caller():
    """There is no endpoint that accepts "job X is overdue"."""

    from backend.app.api.main import app

    for path, item in app.openapi()["paths"].items():
        if "obligation" in path or "optimization-runs" in path:
            assert set(item) == {"get"}, path


# ----------------------------------------------------------------------
# BOUNDARIES: what Slice 9 was forbidden to change
# ----------------------------------------------------------------------


def test_no_new_job_status():
    from backend.app.jobs.models import JobStatus

    assert [s.value for s in JobStatus] == [
        "reported",
        "scheduled",
        "notified",
        "in_progress",
        "completed",
    ]


def test_no_new_job_event_type():
    assert len(list(JobEventType)) == 21
    assert not any(
        name in {e.name for e in JobEventType}
        for name in ("ESCALATED", "NOTIFICATION_SENT", "SLA_BREACHED", "OBLIGATION_RAISED")
    )


def test_no_new_lifecycle_transition():
    from backend.app.jobs.lifecycle import ALLOWED_TRANSITIONS

    assert ALLOWED_TRANSITIONS == {
        "reported": frozenset({"reported", "scheduled"}),
        "scheduled": frozenset({"scheduled", "reported", "notified"}),
        "notified": frozenset({"notified", "in_progress", "reported"}),
        "in_progress": frozenset({"in_progress", "completed", "reported"}),
        "completed": frozenset(),
    }


def test_the_slice9_modules_contain_no_ddl():
    ddl = re.compile(r"\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|TRIGGER|VIEW)\b", re.I)

    for name in SLICE9_MODULES:
        assert not ddl.search((JOBS_DIR / name).read_text(encoding="utf-8")), name


def test_the_storage_schema_gained_no_table_index_or_column(tmp_path):
    JobRepository(tmp_path / "jobs.db")

    with closing(sqlite3.connect(tmp_path / "jobs.db")) as conn:
        names = {
            row[1]
            for row in conn.execute("SELECT type, name FROM sqlite_master").fetchall()
        }

    for forbidden in (
        "notification_attempts",
        "job_obligations",
        "notifications",
        "sla_policies",
        "outbox",
        "users",
        "recipients",
    ):
        assert forbidden not in names, forbidden

    with closing(sqlite3.connect(tmp_path / "jobs.db")) as conn:
        jobs_columns = {r[1] for r in conn.execute("PRAGMA table_info(maintenance_jobs)")}

    assert "status_entered_at" not in jobs_columns


def test_no_scheduler_worker_or_task_queue_was_introduced():
    for name in SLICE9_MODULES:
        source = (JOBS_DIR / name).read_text(encoding="utf-8")

        for forbidden in (
            "apscheduler",
            "celery",
            "BackgroundTasks",
            "asyncio.create_task",
            "threading.Timer",
            "schedule.every",
            "crontab",
        ):
            assert forbidden not in source, (name, forbidden)


def test_the_slice9_modules_never_write():
    """Derivation is a read. None of these modules can mutate anything."""

    for name in SLICE9_MODULES:
        source = (JOBS_DIR / name).read_text(encoding="utf-8")

        for forbidden in (
            "mutate_jobs",
            "append_events",
            "INSERT INTO",
            "UPDATE ",
            "DELETE FROM",
        ):
            assert forbidden not in source, (name, forbidden)


def test_the_optimizer_and_contracts_do_not_import_any_slice9_module():
    root = Path(__file__).resolve().parents[2]
    modules = tuple(name[:-3] for name in SLICE9_MODULES)

    for directory in (root / "backend" / "app" / "optimizer", root / "contracts"):
        for path in directory.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert not any(f"jobs.{name}" in text for name in modules), path
