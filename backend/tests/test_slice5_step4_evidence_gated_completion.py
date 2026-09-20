"""Sprint 3 Slice 5, Step 4: completion is evidence-gated, on every path.

Pins, against the real service, repository, lifecycle guard and HTTP API:

  GRAPH       notified -> in_progress -> completed is the only way to
              complete; in_progress -> reported releases; completed -> {}.
  CLOSED      the legacy direct completion (plan_completion,
              JobService.complete, POST /jobs/{job_id}/complete) is gone,
              and every write path that could still attempt it -
              update_status, a hand-built plan through mutate_jobs - is
              refused with nothing written.
  GATE PROOF  the guard itself refuses entry into completed without a
              COMPLETE token from in_progress, so re-widening the gate
              fails these tests.
  JOB_COMPLETED is readable legacy history only; it can never be written.

Every test uses an isolated temporary database.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.jobs.lifecycle as lifecycle
from contracts import DEFAULT_HORIZON_START

from backend.app.identity.actor import ActorRole, human_actor
from backend.app.identity.authorization import JobAction
from backend.app.jobs.events import JobEventType, JobStateSnapshot, event_timestamp, make_event
from backend.app.jobs.execution import EXECUTION_ID_KEY, new_execution_id
from backend.app.jobs.lifecycle import (
    ALLOWED_TRANSITIONS,
    CommittedJobError,
    ExecutionTokenError,
    ExecutionTransition,
    ExecutionTransitionKind,
    InvalidTransitionError,
    JobMutation,
    StaleExecutionError,
    StaleProposalError,
    TerminalJobError,
    protect_committed_and_terminal_state,
    proposal_run_id_of,
    validate_execution_events,
)
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService
from backend.tests.execution_helpers import (
    complete_execution_body,
    evidence,
    observed_at,
    start_execution_body,
)


WORKER = human_actor("WORKER-042", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-003", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-017", ActorRole.AUTHORITY)

CORRIDOR = "CORRIDOR_A"
E = JobEventType
K = ExecutionTransitionKind
EXECUTION_TYPES = {E.EXECUTION_STARTED, E.EXECUTION_COMPLETED, E.EXECUTION_NOT_COMPLETED}

# Injected "now", well after every in-horizon observation these tests use.
FIXED_NOW = datetime.fromisoformat(DEFAULT_HORIZON_START) + timedelta(days=3650)


# ----------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "jobs.db"


@pytest.fixture()
def service(db_path: Path) -> JobService:
    return JobService(repository=JobRepository(db_path), clock=lambda: FIXED_NOW)


@pytest.fixture()
def optimizer(service: JobService) -> JobOptimizationService:
    return JobOptimizationService(service)


def report(service, track_id="UP-1", distance_start=1000.0) -> dict:
    return service.create_job(
        JobCreateRequest(
            track_id=track_id,
            job_type="BALLAST_TAMPING",
            distance_start=distance_start,
            distance_end=distance_start + 400,
            workers_min=2,
            workers_max=4,
            description="Rail head crack observed during foot patrol",
        ),
        actor=WORKER,
    )


def optimize(optimizer) -> str:
    return optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)["optimization_run_id"]


def approve(service, job_id) -> dict:
    run_id = proposal_run_id_of(service.repository.get(job_id))
    service.approve_proposal(job_id, actor=AUTHORITY, expected_proposal_run_id=run_id)
    return service.repository.get(job_id)


def notified_job(service, optimizer, **kwargs) -> dict:
    job = report(service, **kwargs)
    optimize(optimizer)
    stored = approve(service, job["job_id"])
    assert stored["status"] == "notified"
    return stored


def start(service, job):
    return service.start_execution(job["job_id"], actor=WORKER, **start_execution_body(job))


def complete(service, job, execution_id, **overrides):
    body = {**complete_execution_body(job, execution_id), **overrides}
    return service.complete_execution(job["job_id"], actor=WORKER, **body)


def raw_row(db_path, job_id) -> tuple:
    with closing(sqlite3.connect(db_path)) as conn:
        return tuple(
            conn.execute("SELECT * FROM maintenance_jobs WHERE job_id = ?", (job_id,)).fetchone()
        )


def count_events(db_path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]


def count_runs(db_path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM optimization_runs").fetchone()[0]


def event_types(service, job_id) -> list:
    return [item.event.event_type for item in service.history.list_for_job(job_id)]


def legacy_completion_plan(job_id, *, token=None, event_type=E.JOB_COMPLETED, execution_id=None):
    """The retired plan_completion's shape, optionally dressed up."""

    def plan(rows):
        job = rows[job_id]
        mutation = replace(JobMutation.unchanged(job), status="completed", execution=token)
        metadata = {EXECUTION_ID_KEY: execution_id} if execution_id else {}
        event = make_event(
            job_id,
            event_type,
            WORKER,
            occurred_at=event_timestamp(FIXED_NOW),
            optimization_run_id=proposal_run_id_of(job),
            before_state=JobStateSnapshot.from_job(job),
            after_state=JobStateSnapshot.from_job(mutation.as_job(job)),
            metadata=metadata,
        )
        return [mutation], [event]

    return plan


def assert_nothing_changed(service, db_path, job_id, row_before, events_before, runs_before):
    assert raw_row(db_path, job_id) == row_before
    assert count_events(db_path) == events_before
    assert count_runs(db_path) == runs_before

    types = event_types(service, job_id)
    assert E.JOB_COMPLETED not in types
    assert not EXECUTION_TYPES & set(types)
    assert service.get_execution(job_id, actor=WORKER) == []
    assert EXECUTION_ID_KEY not in service.repository.get(job_id)["block_candidate"]["metadata"]


# ----------------------------------------------------------------------
# Graph
# ----------------------------------------------------------------------


def test_the_transition_graph_has_exactly_one_route_to_completed():
    # notified -> reported is the Sprint 3 Slice 6 authority release of a
    # committed block that cannot proceed. It is gated by an
    # AuthorityRelease token exactly as in_progress -> reported is gated by
    # a NOT_COMPLETED execution token (test_slice6_authority_release.py),
    # and it adds no route into 'completed' - which is what this test
    # exists to pin.
    assert ALLOWED_TRANSITIONS == {
        "reported": frozenset({"reported", "scheduled"}),
        "scheduled": frozenset({"scheduled", "reported", "notified"}),
        "notified": frozenset({"notified", "in_progress", "reported"}),
        "in_progress": frozenset({"in_progress", "completed", "reported"}),
        "completed": frozenset(),
    }
    assert lifecycle._COMMITTED_TARGETS == {
        "notified": ("notified", "in_progress", "reported"),
        "in_progress": ("in_progress", "completed", "reported"),
    }
    assert [s for s, targets in ALLOWED_TRANSITIONS.items() if "completed" in targets] == [
        "in_progress"
    ]


# ----------------------------------------------------------------------
# 1-4, 18. The legacy direct completion is gone and cannot be attempted
# ----------------------------------------------------------------------


def test_legacy_completion_machinery_is_retired():
    assert not hasattr(JobService, "complete")
    assert not hasattr(lifecycle, "plan_completion")
    assert "plan_completion" not in lifecycle.__all__

    from backend.app.api.main import app

    paths = app.openapi()["paths"]
    assert "/v1/jobs/{job_id}/complete" not in paths
    assert [p for p in paths if p.endswith("/complete")] == ["/v1/jobs/{job_id}/execution/complete"]
    assert set(paths["/v1/jobs/{job_id}/execution"]) == {"get"}


def test_a_job_committed_without_an_optimization_run_cannot_be_executed(
    service, db_path
):
    """set_schedule (no HTTP route) commits a placement with no approved run.

    With no direct completion left, such a job can never be completed:
    starting execution requires the approved run, and it has none. The
    refusal is recorded; nothing else is written.
    """

    job = report(service)
    job_id = job["job_id"]
    service.set_schedule(job_id, 100, 190, actor=ENGINEER)
    service.notify(job_id, actor=AUTHORITY)
    notified = service.repository.get(job_id)
    assert notified["status"] == "notified" and proposal_run_id_of(notified) is None

    for run_id, error in ((None, InvalidTransitionError), ("RUN-BOGUS", StaleProposalError)):
        before = (raw_row(db_path, job_id), count_events(db_path))
        body = {**start_execution_body(notified), "expected_proposal_run_id": run_id}

        with pytest.raises(error):
            service.start_execution(job_id, actor=WORKER, **body)

        assert raw_row(db_path, job_id) == before[0]
        assert count_events(db_path) == before[1] + 1
        assert event_types(service, job_id)[-1] is E.TRANSITION_REJECTED

    assert service.get_execution(job_id, actor=WORKER) == []


def test_legacy_completion_route_is_absent_over_http():
    from backend.app.api.main import app
    from backend.app.jobs.router import service as router_service

    client = TestClient(app)
    job = client.post(
        "/v1/jobs",
        json={
            "track_id": "UP-1",
            "job_type": "BALLAST_TAMPING",
            "distance_start": 1000.0,
            "distance_end": 1400.0,
            "workers_min": 2,
            "workers_max": 4,
            "description": "Legacy route check",
        },
        headers={"X-Actor-Id": "WORKER-042", "X-Actor-Role": "WORKER"},
    ).json()

    before = router_service.repository.get(job["job_id"])
    response = client.post(
        f"/v1/jobs/{job['job_id']}/complete",
        headers={"X-Actor-Id": "WORKER-042", "X-Actor-Role": "WORKER"},
    )

    assert response.status_code == 404
    assert router_service.repository.get(job["job_id"]) == before


def test_direct_completion_of_a_notified_job_is_refused_on_every_write_path(
    service, optimizer, db_path
):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    snapshot = (raw_row(db_path, job_id), count_events(db_path), count_runs(db_path))

    # History-less primitive.
    with pytest.raises(ExecutionTokenError):
        service.repository.update_status(job_id, "completed")

    # The retired plan, exactly as it was.
    with pytest.raises(ExecutionTokenError):
        service.repository.mutate_jobs([job_id], legacy_completion_plan(job_id))

    # A forged COMPLETE token with a matching EXECUTION_COMPLETED event: the
    # token is only valid from in_progress.
    forged = new_execution_id()
    with pytest.raises(ExecutionTokenError):
        service.repository.mutate_jobs(
            [job_id],
            legacy_completion_plan(
                job_id,
                token=ExecutionTransition(K.COMPLETE, forged),
                event_type=E.EXECUTION_COMPLETED,
                execution_id=forged,
            ),
        )

    # The completion API itself, with valid evidence but no started execution.
    with pytest.raises(InvalidTransitionError):
        complete(service, job, forged)

    assert event_types(service, job_id)[-1] is E.TRANSITION_REJECTED

    row_before, events_before, runs_before = snapshot
    assert raw_row(db_path, job_id) == row_before
    assert count_events(db_path) == events_before + 1  # only the recorded refusal
    assert count_runs(db_path) == runs_before
    types = event_types(service, job_id)
    assert E.JOB_COMPLETED not in types
    assert not EXECUTION_TYPES & set(types)
    assert service.get_execution(job_id, actor=WORKER) == []


@pytest.mark.parametrize("status", ["reported", "scheduled"])
def test_uncommitted_jobs_cannot_be_completed_by_the_primitive_either(
    service, optimizer, db_path, status
):
    job = report(service)
    if status == "scheduled":
        optimize(optimizer)
    job_id = job["job_id"]
    assert service.repository.get(job_id)["status"] == status

    before = (raw_row(db_path, job_id), count_events(db_path), count_runs(db_path))

    with pytest.raises(ExecutionTokenError):
        service.repository.update_status(job_id, "completed")

    with pytest.raises(ExecutionTokenError):
        service.repository.mutate_jobs([job_id], legacy_completion_plan(job_id))

    assert_nothing_changed(service, db_path, job_id, *before)


def test_job_completed_can_never_be_written(service, optimizer, db_path):
    """Even alongside a state change it does not describe."""

    job = report(service)
    job_id = job["job_id"]
    before = (raw_row(db_path, job_id), count_events(db_path), count_runs(db_path))

    def unchanged_row_with_legacy_event(rows):
        current = rows[job_id]
        state = JobStateSnapshot.from_job(current)
        return [JobMutation.unchanged(current)], [
            make_event(job_id, E.JOB_COMPLETED, WORKER, occurred_at=event_timestamp(FIXED_NOW),
                       before_state=state, after_state=state)
        ]

    with pytest.raises(ExecutionTokenError, match="retired legacy event"):
        service.repository.mutate_jobs([job_id], unchanged_row_with_legacy_event)

    assert_nothing_changed(service, db_path, job_id, *before)

    # Still a readable event type for stored pre-Step-4 history.
    assert JobEventType("JOB_COMPLETED") is E.JOB_COMPLETED


# ----------------------------------------------------------------------
# 20. Gate proof: the guard itself, below every planner
# ----------------------------------------------------------------------


def _row(status, *, committed, execution_id=None) -> dict:
    metadata = {"committed_start_minute": 60, "committed_end_minute": 120, "proposal_run_id": "RUN-1"}
    if execution_id:
        metadata[EXECUTION_ID_KEY] = execution_id
    return {
        "job_id": "JOB-GATE",
        "status": status,
        "schedule_start_minute": 60 if committed or status == "scheduled" else None,
        "schedule_end_minute": 120 if committed or status == "scheduled" else None,
        "block_candidate": {
            "status": "COMMITTED" if committed else "SCHEDULED",
            "is_committed": committed,
            "metadata": metadata,
        },
    }


@pytest.mark.parametrize(
    "row",
    [
        _row("notified", committed=True),
        _row("scheduled", committed=False),
        _row("reported", committed=False),
    ],
    ids=["notified", "scheduled", "reported"],
)
def test_guard_refuses_entering_completed_from_anything_but_in_progress(row):
    schedule = (row["schedule_start_minute"], row["schedule_end_minute"])

    with pytest.raises(ExecutionTokenError, match="requires a COMPLETE execution token"):
        protect_committed_and_terminal_state(row, "completed", row["block_candidate"], schedule)

    with pytest.raises(ExecutionTokenError, match="only complete execution from 'in_progress'"):
        protect_committed_and_terminal_state(
            row,
            "completed",
            row["block_candidate"],
            schedule,
            execution=ExecutionTransition(K.COMPLETE, new_execution_id()),
        )


def test_guard_admits_completion_only_with_the_open_executions_complete_token():
    execution_id = new_execution_id()
    row = _row("in_progress", committed=True, execution_id=execution_id)
    schedule = (60, 120)

    for token in (None, ExecutionTransition(K.START, execution_id),
                  ExecutionTransition(K.COMPLETE, new_execution_id())):
        with pytest.raises(ExecutionTokenError):
            protect_committed_and_terminal_state(
                row, "completed", row["block_candidate"], schedule, execution=token
            )

    protect_committed_and_terminal_state(
        row, "completed", row["block_candidate"], schedule,
        execution=ExecutionTransition(K.COMPLETE, execution_id),
    )


def test_event_cross_check_refuses_job_completed_on_its_own():
    event = make_event("JOB-GATE", E.JOB_COMPLETED, WORKER, occurred_at=event_timestamp(FIXED_NOW))

    with pytest.raises(ExecutionTokenError):
        validate_execution_events([], [event])


# ----------------------------------------------------------------------
# 5-7. The evidence-gated path
# ----------------------------------------------------------------------


def test_only_the_start_token_enters_in_progress(service, optimizer, db_path):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    row_before = raw_row(db_path, job_id)

    with pytest.raises(CommittedJobError):
        service.repository.update_status(job_id, "in_progress")

    assert raw_row(db_path, job_id) == row_before

    started, execution = start(service, job)

    assert started["status"] == "in_progress"
    assert started["block_candidate"]["status"] == "COMMITTED"
    assert started["block_candidate"]["metadata"][EXECUTION_ID_KEY] == execution.execution_id
    assert (started["schedule_start_minute"], started["schedule_end_minute"]) == (
        job["schedule_start_minute"],
        job["schedule_end_minute"],
    )
    assert proposal_run_id_of(started) == proposal_run_id_of(job)
    assert event_types(service, job_id)[-1] is E.EXECUTION_STARTED


@pytest.mark.parametrize("before_work_evidence", [[], None])
def test_start_without_valid_before_work_evidence_fails(
    service, optimizer, db_path, before_work_evidence
):
    job = notified_job(service, optimizer)
    body = start_execution_body(job)
    body["before_work_evidence"] = (
        [evidence("before/photo-1.jpg", job["schedule_start_minute"] + 5)]  # after start
        if before_work_evidence is None
        else before_work_evidence
    )
    row_before = raw_row(db_path, job["job_id"])

    with pytest.raises(InvalidTransitionError):
        service.start_execution(job["job_id"], actor=WORKER, **body)

    assert raw_row(db_path, job["job_id"]) == row_before
    assert service.get_execution(job["job_id"], actor=WORKER) == []


def test_completion_requires_valid_after_work_evidence_then_succeeds(service, optimizer, db_path):
    job = notified_job(service, optimizer)
    _, execution = start(service, job)
    row_before = raw_row(db_path, job["job_id"])

    for bad in (
        {"after_work_evidence": []},
        {"after_work_evidence": [evidence("before/photo-1.jpg", job["schedule_end_minute"])]},
    ):
        with pytest.raises(InvalidTransitionError):
            complete(service, job, execution.execution_id, **bad)

    assert raw_row(db_path, job["job_id"]) == row_before

    completed, record = complete(service, job, execution.execution_id)

    assert completed["status"] == "completed"
    assert completed["block_candidate"]["status"] == "COMMITTED"
    assert completed["block_candidate"]["metadata"][EXECUTION_ID_KEY] == execution.execution_id
    assert record.status.value == "COMPLETED"
    assert record.after_work_evidence
    assert event_types(service, job["job_id"])[-1] is E.EXECUTION_COMPLETED


# ----------------------------------------------------------------------
# 8. COMPLETED is terminal against every operation
# ----------------------------------------------------------------------


def test_completed_job_is_terminal_against_every_operation(service, optimizer, db_path):
    job = notified_job(service, optimizer)
    _, execution = start(service, job)
    complete(service, job, execution.execution_id)
    job_id = job["job_id"]
    run_id = proposal_run_id_of(job)

    report(service, track_id="DOWN-1", distance_start=2000.0)
    row_before = raw_row(db_path, job_id)

    outcome = optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    touched = {s["job_id"] for s in outcome["scheduled"]} | {u["job_id"] for u in outcome["unscheduled"]}
    assert job_id not in touched

    refusals = [
        (InvalidTransitionError, lambda: service.notify(job_id, actor=AUTHORITY, expected_proposal_run_id=run_id)),
        (InvalidTransitionError, lambda: service.approve_proposal(job_id, actor=AUTHORITY, expected_proposal_run_id=run_id)),
        (InvalidTransitionError, lambda: service.reject_proposal(job_id, actor=AUTHORITY, expected_proposal_run_id=run_id, reason="x")),
        (InvalidTransitionError, lambda: service.postpone_proposal(job_id, actor=AUTHORITY, expected_proposal_run_id=run_id, reason="x", selected_date="2026-09-11")),
        (InvalidTransitionError, lambda: start(service, job)),
        (InvalidTransitionError, lambda: complete(service, job, execution.execution_id)),
        (InvalidTransitionError, lambda: service.report_execution_not_completed(
            job_id, actor=WORKER, execution_id=execution.execution_id,
            actual_end_at=observed_at(job["schedule_end_minute"] + 20), reason="x")),
        (TerminalJobError, lambda: service.set_schedule(job_id, 100, 160, actor=ENGINEER)),
        (TerminalJobError, lambda: service.repository.update_status(job_id, "completed")),
        (TerminalJobError, lambda: service.repository.update_status(job_id, "reported")),
    ]

    for error, attempt in refusals:
        with pytest.raises(error):
            attempt()
        assert raw_row(db_path, job_id) == row_before

    assert service.repository.get(job_id)["status"] == "completed"


# ----------------------------------------------------------------------
# 9-16. Release, replanning, stale and cross-job protection
# ----------------------------------------------------------------------


def test_not_completed_releases_and_a_new_generation_executes_independently(
    service, optimizer, db_path
):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    run_1 = proposal_run_id_of(job)
    _, first = start(service, job)

    released, _ = service.report_execution_not_completed(
        job_id,
        actor=WORKER,
        execution_id=first.execution_id,
        actual_end_at=observed_at(job["schedule_start_minute"] + 30),
        reason="Rail temperature too high",
    )

    # 9. Released.
    assert released["status"] == "reported"
    assert (released["schedule_start_minute"], released["schedule_end_minute"]) == (None, None)
    metadata = released["block_candidate"]["metadata"]
    assert EXECUTION_ID_KEY not in metadata and "proposal_run_id" not in metadata
    assert released["block_candidate"]["earliest_start_minute"] >= job["schedule_end_minute"]
    assert released["block_candidate"]["latest_end_minute"] >= service.horizon_minutes
    assert event_types(service, job_id)[-1] is E.EXECUTION_NOT_COMPLETED

    # 13. The released execution can never be completed.
    with pytest.raises(InvalidTransitionError):
        complete(service, job, first.execution_id)

    # 10. A new generation.
    optimize(optimizer)
    rescheduled = service.repository.get(job_id)
    run_2 = proposal_run_id_of(rescheduled)
    assert rescheduled["status"] == "scheduled"
    assert run_2 and run_2 != run_1

    # 14. Stale proposal: the old run can be neither approved nor started.
    with pytest.raises(StaleProposalError):
        service.approve_proposal(job_id, actor=AUTHORITY, expected_proposal_run_id=run_1)

    notified_again = approve(service, job_id)

    with pytest.raises(StaleProposalError):
        service.start_execution(
            job_id, actor=WORKER, **{**start_execution_body(notified_again), "expected_proposal_run_id": run_1}
        )

    # 12. New execution identity and attempt.
    _, second = start(service, notified_again)
    assert second.execution_id != first.execution_id
    assert second.attempt_number == 2
    assert second.optimization_run_id == run_2

    # 15. Stale execution: the old execution cannot complete the new one.
    with pytest.raises(StaleExecutionError):
        complete(service, notified_again, first.execution_id)

    complete(service, notified_again, second.execution_id)

    # 11. The first execution is preserved as history.
    history = service.get_execution(job_id, actor=WORKER)
    assert [(r.execution_id, r.status.value, r.optimization_run_id) for r in history] == [
        (first.execution_id, "NOT_COMPLETED", run_1),
        (second.execution_id, "COMPLETED", run_2),
    ]


def test_an_execution_id_from_another_job_is_rejected(service, optimizer, db_path):
    job_a = notified_job(service, optimizer)
    _, execution_a = start(service, job_a)
    job_b = report(service, track_id="DOWN-1", distance_start=2000.0)
    optimize(optimizer)
    job_b = approve(service, job_b["job_id"])
    start(service, job_b)
    row_before = raw_row(db_path, job_b["job_id"])

    with pytest.raises(StaleExecutionError):
        complete(service, job_b, execution_a.execution_id)

    assert raw_row(db_path, job_b["job_id"]) == row_before


# ----------------------------------------------------------------------
# 17. Authorization exactly once per operation
# ----------------------------------------------------------------------


class CountingPolicy:
    enforcing = True

    def __init__(self):
        self.calls = []

    def authorize(self, actor, action):
        self.calls.append(action)


def test_each_execution_operation_authorizes_exactly_once(service, optimizer):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    policy = CountingPolicy()
    service.authorization = policy

    def once(action, operation):
        policy.calls.clear()
        result = operation()
        assert policy.calls == [action]
        return result

    _, execution = once(JobAction.START_EXECUTION, lambda: start(service, job))
    once(JobAction.READ_JOB_EXECUTION, lambda: service.get_execution(job_id, actor=WORKER))
    once(JobAction.COMPLETE_JOB, lambda: complete(service, job, execution.execution_id))

    other = report(service, track_id="DOWN-1", distance_start=2000.0)
    optimize(optimizer)
    other = approve(service, other["job_id"])
    _, other_execution = start(service, other)

    once(
        JobAction.REPORT_EXECUTION_NOT_COMPLETED,
        lambda: service.report_execution_not_completed(
            other["job_id"],
            actor=WORKER,
            execution_id=other_execution.execution_id,
            actual_end_at=observed_at(other["schedule_start_minute"] + 30),
            reason="Rail temperature too high",
        ),
    )
