"""Slice 5 Step 3: service methods, authorization, and HTTP routes.

Wires the already-reviewed Step 1 (lifecycle guard) and Step 2 (evidence
model + execution plans) machinery into JobService and the four HTTP
routes:

    POST /jobs/{job_id}/execution/start
    POST /jobs/{job_id}/execution/complete
    POST /jobs/{job_id}/execution/not-completed
    GET  /jobs/{job_id}/execution

The core invariant under test throughout: APPROVAL is permission to
execute, never completion. Every service method here is a thin wrapper
- authorize, pre-read the job's own execution history under
lifecycle_lock, delegate the transition to the Step 2 lifecycle plan,
persist through the existing mutate_jobs machinery. Nothing here
duplicates a transition rule the plans already enforce.

Every test uses an isolated temporary database (db_path/service
fixtures) or the module-level router service pointed at the per-session
scratch PASHUPAT_JOBS_DB (see conftest.py) - never the repository-root
jobs.db.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts import DEFAULT_HORIZON_START

from backend.app.identity.actor import ActorRole, human_actor
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.jobs.events import JobEventType
from backend.app.jobs.execution import ExecutionIntegrityError, ExecutionStatus
from backend.app.jobs.history import StoredJobEvent
from backend.app.jobs.lifecycle import (
    CommittedStateIntegrityError,
    InvalidTransitionError,
    StaleExecutionError,
    StaleProposalError,
    proposal_run_id_of,
)
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService


WORKER = human_actor("WORKER-042", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-003", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-017", ActorRole.AUTHORITY)

CORRIDOR = "CORRIDOR_A"
E = JobEventType
HORIZON = datetime.fromisoformat(DEFAULT_HORIZON_START)

# Fixed, injected "now" for every service-level test: well after every
# observation timestamp these tests use, so the future-skew rule never
# depends on when the test happens to run.
FIXED_NOW = HORIZON + timedelta(days=3650)


def local(minute: float) -> str:
    """An observation at a horizon-relative minute, stated in +05:30."""

    return (HORIZON + timedelta(minutes=minute)).isoformat()


def _evidence(reference: str, minute: float, **kwargs) -> dict:
    return {
        "evidence_reference": reference,
        "evidence_kind": kwargs.pop("evidence_kind", "PHOTO"),
        "captured_at": local(minute),
        **kwargs,
    }


# ----------------------------------------------------------------------
# Fixtures and service-level helpers
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


def commit_job(service, optimizer, **kwargs) -> dict:
    """report -> optimize -> approve. Returns the stored, notified row."""

    job = report(service, **kwargs)
    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    run_id = proposal_run_id_of(service.repository.get(job["job_id"]))
    service.notify(job["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_id)
    return service.repository.get(job["job_id"])


def start(service, job, **kwargs):
    start_minute = kwargs.pop("start_minute", job["schedule_start_minute"] + 1)
    return service.start_execution(
        job["job_id"],
        actor=kwargs.pop("actor", WORKER),
        expected_proposal_run_id=kwargs.pop(
            "expected_proposal_run_id", proposal_run_id_of(job)
        ),
        actual_start_at=kwargs.pop("actual_start_at", local(start_minute)),
        before_work_evidence=kwargs.pop(
            "before_work_evidence",
            [_evidence("before/photo-1.jpg", start_minute - 1)],
        ),
    )


def in_progress(service, optimizer, **kwargs):
    """commit -> start. Returns (job, execution) with job in_progress."""

    job = commit_job(service, optimizer, **kwargs)
    return start(service, job)


def complete(service, job, execution, **kwargs):
    end_minute = kwargs.pop("end_minute", job["schedule_end_minute"] + 10)
    return service.complete_execution(
        job["job_id"],
        actor=kwargs.pop("actor", WORKER),
        execution_id=kwargs.pop("execution_id", execution.execution_id),
        actual_end_at=kwargs.pop("actual_end_at", local(end_minute)),
        after_work_evidence=kwargs.pop(
            "after_work_evidence", [_evidence("after/photo-1.jpg", end_minute - 1)]
        ),
    )


def not_completed(service, job, execution, **kwargs):
    end_minute = kwargs.pop("end_minute", job["schedule_start_minute"] + 30)
    return service.report_execution_not_completed(
        job["job_id"],
        actor=kwargs.pop("actor", WORKER),
        execution_id=kwargs.pop("execution_id", execution.execution_id),
        actual_end_at=kwargs.pop("actual_end_at", local(end_minute)),
        reason=kwargs.pop("reason", "Rail temperature too high"),
        failure_evidence=kwargs.pop("failure_evidence", ()),
    )


def raw_row(db_path, job_id) -> tuple:
    with closing(sqlite3.connect(db_path)) as conn:
        return tuple(
            conn.execute(
                "SELECT * FROM maintenance_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        )


def count_events(db_path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]


def history(service, job_id):
    return [stored.event for stored in service.history.list_for_job(job_id)]


# ----------------------------------------------------------------------
# Authorization (1-5)
# ----------------------------------------------------------------------


class DenyPolicy:
    enforcing = True

    def __init__(self, *denied: JobAction):
        self.denied = set(denied)

    def authorize(self, actor, action):
        if action in self.denied:
            raise AuthorizationDenied(actor, action, "denied by test policy")


def test_start_execution_action_is_checked_and_denial_changes_nothing(
    service, optimizer, db_path
):
    job = commit_job(service, optimizer)
    before_row = raw_row(db_path, job["job_id"])
    before_events = count_events(db_path)

    service.authorization = DenyPolicy(JobAction.START_EXECUTION)

    with pytest.raises(AuthorizationDenied):
        start(service, job)

    assert raw_row(db_path, job["job_id"]) == before_row
    assert count_events(db_path) == before_events


def test_complete_job_action_is_checked_and_denial_changes_nothing(
    service, optimizer, db_path
):
    job, execution = in_progress(service, optimizer)
    before_row = raw_row(db_path, job["job_id"])
    before_events = count_events(db_path)

    service.authorization = DenyPolicy(JobAction.COMPLETE_JOB)

    with pytest.raises(AuthorizationDenied):
        complete(service, job, execution)

    assert raw_row(db_path, job["job_id"]) == before_row
    assert count_events(db_path) == before_events


def test_report_execution_not_completed_action_is_checked_and_denial_changes_nothing(
    service, optimizer, db_path
):
    job, execution = in_progress(service, optimizer)
    before_row = raw_row(db_path, job["job_id"])
    before_events = count_events(db_path)

    service.authorization = DenyPolicy(JobAction.REPORT_EXECUTION_NOT_COMPLETED)

    with pytest.raises(AuthorizationDenied):
        not_completed(service, job, execution)

    assert raw_row(db_path, job["job_id"]) == before_row
    assert count_events(db_path) == before_events


def test_read_job_execution_action_is_checked_and_denial_returns_nothing(
    service, optimizer
):
    job, execution = in_progress(service, optimizer)

    service.authorization = DenyPolicy(JobAction.READ_JOB_EXECUTION)

    with pytest.raises(AuthorizationDenied):
        service.get_execution(job["job_id"], actor=WORKER)


# ----------------------------------------------------------------------
# Start (6-14)
# ----------------------------------------------------------------------


def test_successful_start_moves_notified_to_in_progress(service, optimizer):
    job = commit_job(service, optimizer)

    updated, execution = start(service, job)

    assert updated["status"] == "in_progress"
    assert execution.status is ExecutionStatus.STARTED
    assert (
        updated["block_candidate"]["metadata"]["execution_id"]
        == execution.execution_id
    )


def test_start_requires_before_evidence(service, optimizer, db_path):
    job = commit_job(service, optimizer)

    with pytest.raises(ValueError):
        start(service, job, before_work_evidence=[])

    assert service.repository.get(job["job_id"])["status"] == "notified"
    # A well-formed-but-empty list is refused by the plan itself (inside
    # the lock/transaction), unlike a structurally invalid item - see
    # test_start_rejects_structurally_invalid_evidence.
    assert history(service, job["job_id"])[-1].event_type is E.TRANSITION_REJECTED


def test_start_rejects_structurally_invalid_evidence(service, optimizer, db_path):
    job = commit_job(service, optimizer)
    before_events = count_events(db_path)

    with pytest.raises(ValueError):
        start(
            service,
            job,
            before_work_evidence=[
                {
                    "evidence_reference": "",
                    "evidence_kind": "PHOTO",
                    "captured_at": local(0),
                }
            ],
        )

    # Rejected at EvidenceItem construction, before the lock/transaction:
    # no TRANSITION_REJECTED either, unlike a well-formed-but-empty list.
    assert count_events(db_path) == before_events


def test_start_with_wrong_expected_run_is_stale(service, optimizer):
    job = commit_job(service, optimizer)

    with pytest.raises(StaleProposalError):
        start(service, job, expected_proposal_run_id="RUN-DOES-NOT-EXIST")


def test_complete_before_any_start_is_rejected_as_missing_execution(
    service, optimizer
):
    """No execution has ever started: a made-up execution_id names nothing."""

    job = commit_job(service, optimizer)

    with pytest.raises(InvalidTransitionError):
        service.complete_execution(
            job["job_id"],
            actor=WORKER,
            execution_id=f"EXE-{'0' * 32}",
            actual_end_at=local(10),
            after_work_evidence=[_evidence("after/photo-1.jpg", 9)],
        )


def test_second_start_is_rejected(service, optimizer):
    job, _ = in_progress(service, optimizer)

    with pytest.raises(InvalidTransitionError):
        start(service, job)


def test_start_preserves_committed_placement_except_execution_id_metadata(
    service, optimizer
):
    job = commit_job(service, optimizer)
    before_block = copy.deepcopy(job["block_candidate"])

    updated, execution = start(service, job)
    after_block = updated["block_candidate"]

    before_meta = dict(before_block.get("metadata") or {})
    after_meta = dict(after_block.get("metadata") or {})
    assert after_meta.pop("execution_id") == execution.execution_id
    assert after_meta == before_meta

    for key in ("status", "is_committed", "committed_start_minute", "committed_end_minute"):
        assert after_block.get(key) == before_block.get(key)

    assert updated["schedule_start_minute"] == job["schedule_start_minute"]
    assert updated["schedule_end_minute"] == job["schedule_end_minute"]
    assert proposal_run_id_of(updated) == proposal_run_id_of(job)


def test_start_writes_execution_started_event(service, optimizer):
    job = commit_job(service, optimizer)

    updated, execution = start(service, job)

    started = [e for e in history(service, job["job_id"]) if e.event_type is E.EXECUTION_STARTED]
    assert len(started) == 1
    assert started[0].metadata["execution_id"] == execution.execution_id
    assert started[0].actor == WORKER
    assert started[0].optimization_run_id == proposal_run_id_of(job)


def test_start_execution_record_reconstructs(service, optimizer):
    job = commit_job(service, optimizer)

    _, execution = start(service, job)

    fetched = service.get_execution(job["job_id"], actor=WORKER)
    assert len(fetched) == 1
    assert fetched[0].execution_id == execution.execution_id
    assert fetched[0].status is ExecutionStatus.STARTED
    assert fetched[0].before_work_evidence[0].evidence_reference == "before/photo-1.jpg"


def test_reconstructed_execution_fails_closed_when_id_is_absent(service):
    """Direct unit test of the guard clause itself, not just its callers.

    start/complete_execution/report_execution_not_completed only reach
    this branch when a just-committed execution_id is somehow absent
    from the freshly rebuilt records - not reachable through the public
    API without tampering already covered by the get_execution
    fail-closed tests above. Calling the helper directly exercises the
    branch without touching production code or the transition machinery.
    """

    with pytest.raises(ExecutionIntegrityError) as excinfo:
        service._reconstructed_execution((), "JOB-MISSING", "EXE-MISSING")

    assert "JOB-MISSING" in str(excinfo.value)
    assert "EXE-MISSING" in str(excinfo.value)


# ----------------------------------------------------------------------
# Complete (15-22)
# ----------------------------------------------------------------------


def test_successful_completion_moves_in_progress_to_completed(service, optimizer):
    job, execution = in_progress(service, optimizer)

    updated, final = complete(service, job, execution)

    assert updated["status"] == "completed"
    assert final.status is ExecutionStatus.COMPLETED
    assert updated["block_candidate"]["metadata"]["execution_id"] == execution.execution_id


def test_complete_requires_after_evidence(service, optimizer):
    job, execution = in_progress(service, optimizer)

    with pytest.raises(ValueError):
        complete(service, job, execution, after_work_evidence=[])

    assert service.repository.get(job["job_id"])["status"] == "in_progress"
    assert history(service, job["job_id"])[-1].event_type is E.TRANSITION_REJECTED


def test_complete_rejects_before_reference_reused_as_after(service, optimizer):
    job, execution = in_progress(service, optimizer)
    reused_reference = execution.before_work_evidence[0].evidence_reference

    with pytest.raises(ValueError):
        complete(
            service,
            job,
            execution,
            after_work_evidence=[
                _evidence(reused_reference, job["schedule_end_minute"] + 1)
            ],
        )


def test_complete_rejects_a_changed_committed_block_digest(service, optimizer, db_path):
    """work_type feeds committed_block_digest but not the planned-minute

    identity check _open_execution_checks does first, so tampering with
    it alone isolates the digest mismatch this test targets rather than
    tripping the (separate) ExecutionIntegrityError a placement change
    would.
    """

    job, execution = in_progress(service, optimizer)

    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute(
            "UPDATE maintenance_jobs SET work_type = 'RAIL_GRINDING' WHERE job_id = ?",
            (job["job_id"],),
        )

    with pytest.raises(CommittedStateIntegrityError):
        complete(service, job, execution)


def test_complete_rejects_wrong_execution_id(service, optimizer):
    job, execution = in_progress(service, optimizer)

    with pytest.raises(StaleExecutionError):
        complete(service, job, execution, execution_id=f"EXE-{'F' * 32}")


def test_completed_job_is_excluded_from_reoptimization(service, optimizer):
    job, execution = in_progress(service, optimizer)

    updated, _ = complete(service, job, execution)

    assert updated["status"] == "completed"
    active_ids = {row["job_id"] for row in service.repository.list_active()}
    assert job["job_id"] not in active_ids


def test_complete_writes_execution_completed_event(service, optimizer):
    job, execution = in_progress(service, optimizer)

    updated, final = complete(service, job, execution)

    completed = [
        e for e in history(service, job["job_id"]) if e.event_type is E.EXECUTION_COMPLETED
    ]
    assert len(completed) == 1
    assert completed[0].metadata["execution_id"] == execution.execution_id
    assert completed[0].actor == WORKER


def test_complete_execution_record_reconstructs_as_completed(service, optimizer):
    job, execution = in_progress(service, optimizer)

    complete(service, job, execution)

    fetched = service.get_execution(job["job_id"], actor=WORKER)
    assert len(fetched) == 1
    assert fetched[0].status is ExecutionStatus.COMPLETED
    assert fetched[0].after_work_evidence[0].evidence_reference == "after/photo-1.jpg"


# ----------------------------------------------------------------------
# Not completed (23-31)
# ----------------------------------------------------------------------


def test_successful_not_completed_releases_the_job(service, optimizer):
    job, execution = in_progress(service, optimizer)

    updated, final = not_completed(service, job, execution)

    assert updated["status"] == "reported"
    assert final.status is ExecutionStatus.NOT_COMPLETED


def test_not_completed_requires_a_reason(service, optimizer):
    job, execution = in_progress(service, optimizer)

    with pytest.raises(ValueError):
        not_completed(service, job, execution, reason="")

    assert service.repository.get(job["job_id"])["status"] == "in_progress"


def test_not_completed_clears_placement(service, optimizer):
    job, execution = in_progress(service, optimizer)

    updated, _ = not_completed(service, job, execution)

    assert updated["schedule_start_minute"] is None
    assert updated["schedule_end_minute"] is None


def test_not_completed_clears_proposal_run_id(service, optimizer):
    job, execution = in_progress(service, optimizer)

    updated, _ = not_completed(service, job, execution)

    assert proposal_run_id_of(updated) is None


def test_not_completed_clears_execution_id_from_current_block(service, optimizer):
    job, execution = in_progress(service, optimizer)

    updated, _ = not_completed(service, job, execution)

    assert "execution_id" not in (updated["block_candidate"].get("metadata") or {})


def test_not_completed_raises_earliest_start_minute_safely(service, optimizer):
    """earliest_start_minute = max(stored, planned_end, actual_end_minute).

    Asserts equality against that computed max, not a loose inequality,
    so the test would fail if the implementation silently dropped one
    of the three terms.
    """

    job, execution = in_progress(service, optimizer)
    stored_earliest = job["block_candidate"]["earliest_start_minute"]
    planned_end = job["schedule_end_minute"]
    end_minute = planned_end + 20

    updated, _ = not_completed(service, job, execution, end_minute=end_minute)

    expected = max(stored_earliest, planned_end, end_minute)
    assert updated["block_candidate"]["earliest_start_minute"] == expected


def test_not_completed_history_remains_reconstructable(service, optimizer):
    job, execution = in_progress(service, optimizer)

    _, final = not_completed(service, job, execution)

    fetched = service.get_execution(job["job_id"], actor=WORKER)
    assert len(fetched) == 1
    assert fetched[0].execution_id == execution.execution_id
    assert fetched[0].status is ExecutionStatus.NOT_COMPLETED
    assert fetched[0].not_completed_reason == "Rail temperature too high"


def test_not_completed_writes_execution_not_completed_event(service, optimizer):
    job, execution = in_progress(service, optimizer)

    not_completed(service, job, execution)

    events = [
        e
        for e in history(service, job["job_id"])
        if e.event_type is E.EXECUTION_NOT_COMPLETED
    ]
    assert len(events) == 1
    assert events[0].metadata["execution_id"] == execution.execution_id
    assert events[0].reason == "Rail temperature too high"


def test_not_completed_job_can_be_reoptimized(service, optimizer):
    job, execution = in_progress(service, optimizer)

    not_completed(service, job, execution)

    outcome = optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    considered = {item["job_id"] for item in outcome["scheduled"]} | {
        item["job_id"] for item in outcome["unscheduled"]
    }
    assert job["job_id"] in considered


# ----------------------------------------------------------------------
# Retrieval (32-35)
# ----------------------------------------------------------------------


def test_get_execution_returns_reconstructed_record(service, optimizer):
    job, execution = in_progress(service, optimizer)

    fetched = service.get_execution(job["job_id"], actor=WORKER)

    assert [record.execution_id for record in fetched] == [execution.execution_id]


def test_get_execution_with_no_history_returns_empty_list(service, optimizer):
    job = commit_job(service, optimizer)

    assert service.get_execution(job["job_id"], actor=WORKER) == []


def test_get_execution_fails_closed_when_row_disagrees_with_history(
    service, optimizer, db_path
):
    """The row's execution_id names an execution its own history never opened."""

    job, execution = in_progress(service, optimizer)

    with closing(sqlite3.connect(db_path)) as conn, conn:
        stored = json.loads(
            conn.execute(
                "SELECT block_candidate_json FROM maintenance_jobs WHERE job_id = ?",
                (job["job_id"],),
            ).fetchone()[0]
        )
        stored["metadata"]["execution_id"] = f"EXE-{'9' * 32}"
        conn.execute(
            "UPDATE maintenance_jobs SET block_candidate_json = ? WHERE job_id = ?",
            (json.dumps(stored), job["job_id"]),
        )

    with pytest.raises(ExecutionIntegrityError):
        service.get_execution(job["job_id"], actor=WORKER)


def test_get_execution_fails_closed_on_malformed_history(service, optimizer, monkeypatch):
    """The history ITSELF is incoherent (not just row-vs-history disagreement).

    job_events rejects UPDATE/DELETE at the SQL layer by design, so this
    tampers at the read seam (JobHistoryRepository.list_for_job) instead
    - the only way to hand build_execution_records an EXECUTION_STARTED
    event whose own metadata does not match its declared attempt_number.
    """

    job, execution = in_progress(service, optimizer)
    original = service.history.list_for_job(job["job_id"])

    def tampered(job_id):
        return [
            item
            if item.event.event_type is not E.EXECUTION_STARTED
            else StoredJobEvent(
                sequence=item.sequence,
                event=replace(
                    item.event,
                    metadata={**item.event.metadata, "attempt_number": 99},
                ),
            )
            for item in original
        ]

    monkeypatch.setattr(service.history, "list_for_job", tampered)

    with pytest.raises(ExecutionIntegrityError):
        service.get_execution(job["job_id"], actor=WORKER)


def test_get_execution_authorization_is_enforced(service, optimizer):
    job, execution = in_progress(service, optimizer)
    service.authorization = DenyPolicy(JobAction.READ_JOB_EXECUTION)

    with pytest.raises(AuthorizationDenied):
        service.get_execution(job["job_id"], actor=WORKER)


# ----------------------------------------------------------------------
# Cross-generation regression (Step 3 remediation, item A)
# ----------------------------------------------------------------------


def test_cross_generation_execution_history_and_stale_run_rejection(
    service, optimizer, db_path
):
    """Generation 1 fails, generation 2 replaces it; R1/E1 never leak into it."""

    job, e1 = in_progress(service, optimizer)
    r1 = proposal_run_id_of(job)

    not_completed(service, job, e1)

    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    scheduled = service.repository.get(job["job_id"])
    r2 = proposal_run_id_of(scheduled)

    assert r2 is not None
    assert r2 != r1

    # R1 can no longer approve the current (generation-2) proposal.
    with pytest.raises(StaleProposalError):
        service.notify(job["job_id"], actor=AUTHORITY, expected_proposal_run_id=r1)

    service.notify(job["job_id"], actor=AUTHORITY, expected_proposal_run_id=r2)
    notified = service.repository.get(job["job_id"])

    # R1 can no longer start the current (generation-2) approved block either.
    with pytest.raises(StaleProposalError):
        start(service, notified, expected_proposal_run_id=r1)

    updated2, e2 = start(service, notified, expected_proposal_run_id=r2)

    assert e2.execution_id != e1.execution_id
    assert e2.attempt_number == 2
    assert e2.optimization_run_id == r2

    fetched = service.get_execution(job["job_id"], actor=WORKER)
    assert [record.execution_id for record in fetched] == [
        e1.execution_id,
        e2.execution_id,
    ]


def test_late_generation_one_outcome_after_generation_two_is_stale(
    service, optimizer, db_path
):
    """A late COMPLETE/NOT_COMPLETED for E1 must not touch generation 2's E2."""

    job, e1 = in_progress(service, optimizer)
    not_completed(service, job, e1)

    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    scheduled = service.repository.get(job["job_id"])
    r2 = proposal_run_id_of(scheduled)
    service.notify(job["job_id"], actor=AUTHORITY, expected_proposal_run_id=r2)
    notified = service.repository.get(job["job_id"])
    updated2, e2 = start(service, notified)

    before_row = raw_row(db_path, job["job_id"])

    with pytest.raises(StaleExecutionError):
        complete(service, updated2, e1)

    with pytest.raises(StaleExecutionError):
        not_completed(service, updated2, e1)

    assert raw_row(db_path, job["job_id"]) == before_row

    final = service.repository.get(job["job_id"])
    assert final["status"] == "in_progress"
    assert final["block_candidate"]["metadata"]["execution_id"] == e2.execution_id


def test_cross_job_execution_id_is_rejected_as_stale(service, optimizer, db_path):
    """Job A's execution_id used against job B must fail; B stays untouched."""

    job_a, exec_a = in_progress(service, optimizer, distance_start=1000.0)
    job_b, exec_b = in_progress(service, optimizer, distance_start=5000.0)

    before_row_b = raw_row(db_path, job_b["job_id"])

    with pytest.raises(StaleExecutionError):
        complete(service, job_b, exec_a)

    with pytest.raises(StaleExecutionError):
        not_completed(service, job_b, exec_a)

    assert raw_row(db_path, job_b["job_id"]) == before_row_b

    final_b = service.repository.get(job_b["job_id"])
    assert final_b["status"] == "in_progress"
    assert final_b["block_candidate"]["metadata"]["execution_id"] == exec_b.execution_id


def test_execution_actions_on_completed_job_are_rejected_as_invalid_transition(
    service, optimizer
):
    """The plan's status check precedes the terminal guard: 400, not 409.

    Intentional current behaviour - see SLICE5_EXECUTION_EVIDENCE_DESIGN.md
    "Completed-job execution routes". Not changed by this remediation.
    """

    job, execution = in_progress(service, optimizer)
    completed_job, _ = complete(service, job, execution)
    assert completed_job["status"] == "completed"

    with pytest.raises(InvalidTransitionError):
        start(service, completed_job)

    with pytest.raises(InvalidTransitionError):
        complete(service, completed_job, execution)

    with pytest.raises(InvalidTransitionError):
        not_completed(service, completed_job, execution)


def test_not_completed_rejects_whitespace_only_reason(service, optimizer):
    job, execution = in_progress(service, optimizer)

    with pytest.raises(InvalidTransitionError):
        not_completed(service, job, execution, reason="   ")

    assert service.repository.get(job["job_id"])["status"] == "in_progress"


def test_get_execution_never_observes_a_mutation_mid_flight(
    service, optimizer, monkeypatch
):
    """A GET racing a START must observe wholly-before or wholly-after state.

    mutate_jobs is delayed while start_execution still holds
    lifecycle_lock (the P1-1 fix), so a concurrent get_execution
    contending for the SAME lock is forced to wait for the whole
    transition to finish - proving the lock, not luck, is what prevents
    a false ExecutionIntegrityError.
    """

    job = commit_job(service, optimizer)

    entered = threading.Event()
    release = threading.Event()
    original_mutate = service.repository.mutate_jobs

    def delayed_mutate(job_ids, plan):
        entered.set()
        release.wait(timeout=5)
        return original_mutate(job_ids, plan)

    monkeypatch.setattr(service.repository, "mutate_jobs", delayed_mutate)

    start_result = {}

    def do_start():
        start_result["value"] = start(service, job)

    starter = threading.Thread(target=do_start)
    starter.start()
    assert entered.wait(timeout=5)

    get_done = threading.Event()
    get_outcome = {}

    def do_get():
        try:
            get_outcome["records"] = service.get_execution(job["job_id"], actor=WORKER)
        except Exception as exc:  # noqa: BLE001
            get_outcome["error"] = exc
        finally:
            get_done.set()

    getter = threading.Thread(target=do_get)
    getter.start()

    # The getter contends for the SAME lock start_execution still holds
    # mid-write: it must not be able to finish yet.
    assert not get_done.wait(timeout=0.3)

    release.set()
    starter.join(timeout=5)
    getter.join(timeout=5)

    assert "error" not in get_outcome, get_outcome.get("error")

    _, execution = start_result["value"]
    records = get_outcome["records"]
    assert [record.execution_id for record in records] == [execution.execution_id]
    assert records[0].status is ExecutionStatus.STARTED


# ----------------------------------------------------------------------
# Concurrency (36-38)
# ----------------------------------------------------------------------


def test_execution_transition_pins_against_reoptimization(service, optimizer):
    job, execution = in_progress(service, optimizer)

    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)

    updated = service.repository.get(job["job_id"])
    assert updated["status"] == "in_progress"
    assert updated["schedule_start_minute"] == job["schedule_start_minute"]
    assert updated["schedule_end_minute"] == job["schedule_end_minute"]
    assert updated["block_candidate"]["metadata"]["execution_id"] == execution.execution_id


def test_concurrent_starts_of_one_job_succeed_exactly_once(service, optimizer):
    job = commit_job(service, optimizer)
    barrier = threading.Barrier(2)
    results = []
    errors = []
    lock = threading.Lock()

    def attempt():
        barrier.wait()
        try:
            outcome = start(service, job)
        except Exception as exc:  # noqa: BLE001 - the loser's exact type is asserted below
            with lock:
                errors.append(exc)
        else:
            with lock:
                results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], InvalidTransitionError)

    started = [
        e for e in history(service, job["job_id"]) if e.event_type is E.EXECUTION_STARTED
    ]
    assert len(started) == 1


def test_complete_and_not_completed_racing_the_same_execution_cannot_both_succeed(
    service, optimizer
):
    job, execution = in_progress(service, optimizer)
    barrier = threading.Barrier(2)
    results = []
    errors = []
    lock = threading.Lock()

    def do_complete():
        barrier.wait()
        try:
            outcome = complete(service, job, execution)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        else:
            with lock:
                results.append(("complete", outcome))

    def do_not_completed():
        barrier.wait()
        try:
            outcome = not_completed(service, job, execution)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        else:
            with lock:
                results.append(("not_completed", outcome))

    threads = [threading.Thread(target=do_complete), threading.Thread(target=do_not_completed)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 1
    assert len(errors) == 1

    final_status = service.repository.get(job["job_id"])["status"]
    assert final_status in ("completed", "reported")

    outcome_events = [
        e
        for e in history(service, job["job_id"])
        if e.event_type in (E.EXECUTION_COMPLETED, E.EXECUTION_NOT_COMPLETED)
    ]
    assert len(outcome_events) == 1


# ----------------------------------------------------------------------
# HTTP routes
# ----------------------------------------------------------------------


HTTP_WORKER = {"X-Actor-Id": "WORKER-042", "X-Actor-Role": "WORKER"}
HTTP_ENGINEER = {"X-Actor-Id": "ENGINEER-003", "X-Actor-Role": "ENGINEER"}
HTTP_AUTHORITY = {"X-Actor-Id": "AUTHORITY-017", "X-Actor-Role": "AUTHORITY"}


@pytest.fixture()
def client():
    from backend.app.api.main import app
    from backend.app.jobs.router import service as router_service

    def wipe_jobs():
        with closing(sqlite3.connect(router_service.repository.db_path)) as conn, conn:
            conn.execute("DELETE FROM maintenance_jobs")

    wipe_jobs()
    yield TestClient(app)
    wipe_jobs()


def _http_commit_job(client) -> dict:
    created = client.post(
        "/v1/jobs",
        json={
            "track_id": "UP-1",
            "job_type": "BALLAST_TAMPING",
            "distance_start": 1000.0,
            "distance_end": 1400.0,
            "workers_min": 2,
            "workers_max": 4,
            "description": "HTTP execution flow",
        },
        headers=HTTP_WORKER,
    ).json()
    job_id = created["job_id"]

    run_id = client.post(
        "/v1/corridors/CORRIDOR_A/optimize-jobs", headers=HTTP_ENGINEER
    ).json()["optimization_run_id"]
    client.post(
        f"/v1/jobs/{job_id}/notify",
        json={"expected_proposal_run_id": run_id},
        headers=HTTP_AUTHORITY,
    )
    return client.get(f"/v1/jobs/{job_id}").json()


def test_http_start_and_complete_flow_returns_reconstructed_records(client):
    job = _http_commit_job(client)
    job_id = job["job_id"]
    start_minute = job["schedule_start_minute"] + 1

    start_response = client.post(
        f"/v1/jobs/{job_id}/execution/start",
        json={
            "expected_proposal_run_id": job["proposal_run_id"],
            "actual_start_at": local(start_minute),
            "before_work_evidence": [_evidence("before/1.jpg", start_minute - 1)],
        },
        headers=HTTP_WORKER,
    )
    assert start_response.status_code == 200, start_response.text
    body = start_response.json()
    assert body["job"]["status"] == "in_progress"
    execution_id = body["execution"]["execution_id"]

    get_response = client.get(f"/v1/jobs/{job_id}/execution", headers=HTTP_WORKER)
    assert get_response.status_code == 200
    executions = get_response.json()["executions"]
    assert [item["execution_id"] for item in executions] == [execution_id]
    assert executions[0]["status"] == "STARTED"

    end_minute = job["schedule_end_minute"] + 5
    complete_response = client.post(
        f"/v1/jobs/{job_id}/execution/complete",
        json={
            "execution_id": execution_id,
            "actual_end_at": local(end_minute),
            "after_work_evidence": [_evidence("after/1.jpg", end_minute - 1)],
        },
        headers=HTTP_WORKER,
    )
    assert complete_response.status_code == 200, complete_response.text
    assert complete_response.json()["job"]["status"] == "completed"


def test_http_get_execution_with_no_history_is_200_with_empty_list(client):
    job = _http_commit_job(client)

    response = client.get(f"/v1/jobs/{job['job_id']}/execution", headers=HTTP_WORKER)

    assert response.status_code == 200
    assert response.json() == {"job_id": job["job_id"], "executions": []}


def test_http_get_execution_for_missing_job_is_404(client):
    response = client.get("/v1/jobs/JOB-DOES-NOT-EXIST/execution", headers=HTTP_WORKER)

    assert response.status_code == 404


def test_http_not_completed_flow_returns_job_to_reported(client):
    job = _http_commit_job(client)
    job_id = job["job_id"]
    start_minute = job["schedule_start_minute"] + 1

    start_response = client.post(
        f"/v1/jobs/{job_id}/execution/start",
        json={
            "expected_proposal_run_id": job["proposal_run_id"],
            "actual_start_at": local(start_minute),
            "before_work_evidence": [_evidence("before/1.jpg", start_minute - 1)],
        },
        headers=HTTP_WORKER,
    )
    execution_id = start_response.json()["execution"]["execution_id"]

    end_minute = job["schedule_start_minute"] + 30
    response = client.post(
        f"/v1/jobs/{job_id}/execution/not-completed",
        json={
            "execution_id": execution_id,
            "actual_end_at": local(end_minute),
            "reason": "Rail temperature too high",
        },
        headers=HTTP_WORKER,
    )

    assert response.status_code == 200, response.text
    assert response.json()["job"]["status"] == "reported"


def test_http_start_requires_at_least_one_before_work_evidence_item(client):
    job = _http_commit_job(client)
    job_id = job["job_id"]
    start_minute = job["schedule_start_minute"] + 1

    response = client.post(
        f"/v1/jobs/{job_id}/execution/start",
        json={
            "expected_proposal_run_id": job["proposal_run_id"],
            "actual_start_at": local(start_minute),
            "before_work_evidence": [],
        },
        headers=HTTP_WORKER,
    )

    assert response.status_code == 422


def test_http_unauthenticated_system_role_is_refused_on_execution_routes(client):
    job = _http_commit_job(client)
    job_id = job["job_id"]
    headers = {"X-Actor-Id": "SYSTEM:OPTIMIZER", "X-Actor-Role": "SYSTEM"}

    response = client.post(
        f"/v1/jobs/{job_id}/execution/start",
        json={
            "expected_proposal_run_id": job["proposal_run_id"],
            "actual_start_at": local(1),
            "before_work_evidence": [_evidence("before/1.jpg", 0)],
        },
        headers=headers,
    )

    assert response.status_code == 403


# ----------------------------------------------------------------------
# HTTP regression: completed job (item D), authorization (item F),
# stale/integrity error mapping (item G), whitespace reason (item H)
# ----------------------------------------------------------------------


def test_http_execution_actions_on_completed_job_return_400(client):
    job = _http_commit_job(client)
    job_id = job["job_id"]
    start_minute = job["schedule_start_minute"] + 1

    start_response = client.post(
        f"/v1/jobs/{job_id}/execution/start",
        json={
            "expected_proposal_run_id": job["proposal_run_id"],
            "actual_start_at": local(start_minute),
            "before_work_evidence": [_evidence("before/1.jpg", start_minute - 1)],
        },
        headers=HTTP_WORKER,
    )
    execution_id = start_response.json()["execution"]["execution_id"]

    end_minute = job["schedule_end_minute"] + 5
    complete_response = client.post(
        f"/v1/jobs/{job_id}/execution/complete",
        json={
            "execution_id": execution_id,
            "actual_end_at": local(end_minute),
            "after_work_evidence": [_evidence("after/1.jpg", end_minute - 1)],
        },
        headers=HTTP_WORKER,
    )
    assert complete_response.json()["job"]["status"] == "completed"

    retry_start = client.post(
        f"/v1/jobs/{job_id}/execution/start",
        json={
            "expected_proposal_run_id": job["proposal_run_id"],
            "actual_start_at": local(start_minute + 1),
            "before_work_evidence": [_evidence("before/2.jpg", start_minute)],
        },
        headers=HTTP_WORKER,
    )
    assert retry_start.status_code == 400, retry_start.text

    retry_complete = client.post(
        f"/v1/jobs/{job_id}/execution/complete",
        json={
            "execution_id": execution_id,
            "actual_end_at": local(end_minute + 1),
            "after_work_evidence": [_evidence("after/2.jpg", end_minute)],
        },
        headers=HTTP_WORKER,
    )
    assert retry_complete.status_code == 400, retry_complete.text

    retry_not_completed = client.post(
        f"/v1/jobs/{job_id}/execution/not-completed",
        json={
            "execution_id": execution_id,
            "actual_end_at": local(end_minute + 1),
            "reason": "late report",
        },
        headers=HTTP_WORKER,
    )
    assert retry_not_completed.status_code == 400, retry_not_completed.text


def test_http_authorization_denies_all_four_execution_routes(client):
    from backend.app.jobs.router import service as router_service

    job = _http_commit_job(client)
    job_id = job["job_id"]

    before = router_service.repository.get(job_id)
    original_policy = router_service.authorization
    router_service.authorization = DenyPolicy(
        JobAction.START_EXECUTION,
        JobAction.COMPLETE_JOB,
        JobAction.REPORT_EXECUTION_NOT_COMPLETED,
        JobAction.READ_JOB_EXECUTION,
    )

    try:
        start_response = client.post(
            f"/v1/jobs/{job_id}/execution/start",
            json={
                "expected_proposal_run_id": job["proposal_run_id"],
                "actual_start_at": local(1),
                "before_work_evidence": [_evidence("before/1.jpg", 0)],
            },
            headers=HTTP_WORKER,
        )
        assert start_response.status_code == 403

        complete_response = client.post(
            f"/v1/jobs/{job_id}/execution/complete",
            json={
                "execution_id": f"EXE-{'0' * 32}",
                "actual_end_at": local(10),
                "after_work_evidence": [_evidence("after/1.jpg", 9)],
            },
            headers=HTTP_WORKER,
        )
        assert complete_response.status_code == 403

        not_completed_response = client.post(
            f"/v1/jobs/{job_id}/execution/not-completed",
            json={
                "execution_id": f"EXE-{'0' * 32}",
                "actual_end_at": local(10),
                "reason": "denied",
            },
            headers=HTTP_WORKER,
        )
        assert not_completed_response.status_code == 403

        get_response = client.get(f"/v1/jobs/{job_id}/execution", headers=HTTP_WORKER)
        assert get_response.status_code == 403
    finally:
        router_service.authorization = original_policy

    assert router_service.repository.get(job_id) == before


def test_http_stale_and_integrity_error_mappings(client):
    from backend.app.jobs.router import service as router_service

    job = _http_commit_job(client)
    job_id = job["job_id"]
    start_minute = job["schedule_start_minute"] + 1

    stale_start = client.post(
        f"/v1/jobs/{job_id}/execution/start",
        json={
            "expected_proposal_run_id": "RUN-DOES-NOT-EXIST",
            "actual_start_at": local(start_minute),
            "before_work_evidence": [_evidence("before/1.jpg", start_minute - 1)],
        },
        headers=HTTP_WORKER,
    )
    assert stale_start.status_code == 409, stale_start.text

    start_response = client.post(
        f"/v1/jobs/{job_id}/execution/start",
        json={
            "expected_proposal_run_id": job["proposal_run_id"],
            "actual_start_at": local(start_minute),
            "before_work_evidence": [_evidence("before/1.jpg", start_minute - 1)],
        },
        headers=HTTP_WORKER,
    )
    assert start_response.status_code == 200, start_response.text

    end_minute = job["schedule_end_minute"] + 5
    stale_complete = client.post(
        f"/v1/jobs/{job_id}/execution/complete",
        json={
            "execution_id": f"EXE-{'F' * 32}",
            "actual_end_at": local(end_minute),
            "after_work_evidence": [_evidence("after/1.jpg", end_minute - 1)],
        },
        headers=HTTP_WORKER,
    )
    assert stale_complete.status_code == 409, stale_complete.text

    with closing(sqlite3.connect(router_service.repository.db_path)) as conn, conn:
        stored = json.loads(
            conn.execute(
                "SELECT block_candidate_json FROM maintenance_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
        )
        stored["metadata"]["execution_id"] = f"EXE-{'9' * 32}"
        conn.execute(
            "UPDATE maintenance_jobs SET block_candidate_json = ? WHERE job_id = ?",
            (json.dumps(stored), job_id),
        )

    integrity_get = client.get(f"/v1/jobs/{job_id}/execution", headers=HTTP_WORKER)
    assert integrity_get.status_code == 409, integrity_get.text


def test_http_not_completed_rejects_whitespace_only_reason(client):
    job = _http_commit_job(client)
    job_id = job["job_id"]
    start_minute = job["schedule_start_minute"] + 1

    start_response = client.post(
        f"/v1/jobs/{job_id}/execution/start",
        json={
            "expected_proposal_run_id": job["proposal_run_id"],
            "actual_start_at": local(start_minute),
            "before_work_evidence": [_evidence("before/1.jpg", start_minute - 1)],
        },
        headers=HTTP_WORKER,
    )
    execution_id = start_response.json()["execution"]["execution_id"]

    end_minute = job["schedule_start_minute"] + 30
    response = client.post(
        f"/v1/jobs/{job_id}/execution/not-completed",
        json={
            "execution_id": execution_id,
            "actual_end_at": local(end_minute),
            "reason": "   ",
        },
        headers=HTTP_WORKER,
    )

    assert response.status_code == 400, response.text
