"""Sprint 3 Slice 5, Step 2: evidence, pure execution plans, derived history.

Pins, against the real repository, lifecycle guard and optimizer:

  EVIDENCE      EvidenceItem normalizes and validates one item; the
                collection rules (count, uniqueness, time bounds, no
                before-work reference reused as after-work) are enforced by
                the plans.
  START         notified -> in_progress changes nothing about the approved
                block except block metadata execution_id; EXECUTION_STARTED
                carries everything needed to reconstruct the execution.
  COMPLETE      in_progress -> completed requires after-work evidence, the
                current execution id and an unchanged committed block.
  NOT COMPLETED in_progress -> reported releases the commitment without
                rewriting history, with the replanning window
                earliest = max(stored, planned_end, actual_end) and
                latest = max(current, horizon).
  RECORDS       build_execution_records derives every attempt from history
                and fails closed on any incoherent history.
  REPLANNING    a released generation stays historical and cannot act; the
                next generation gets a new run, proposal and execution.
  POSTPONE      the helper extracted from plan_postpone keeps its semantics.

No service method exists yet (Step 3): plans are driven through
JobRepository.mutate_jobs exactly as JobService._transition will drive
them. Every test uses an isolated database.
"""

from __future__ import annotations

import copy
import json
import math
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from contracts import DEFAULT_HORIZON_START

from backend.app.identity.actor import (
    OPTIMIZER,
    ActorRole,
    human_actor,
    unidentified_actor,
)
from backend.app.jobs.events import (
    JobEvent,
    JobEventType,
    canonical_json,
    event_timestamp,
    new_event_id,
)
from backend.app.jobs.execution import (
    EXECUTION_ID_KEY,
    EvidenceItem,
    EvidenceKind,
    EvidencePhase,
    EvidenceValidationError,
    ExecutionIntegrityError,
    ExecutionStatus,
    RecordedEvidence,
    build_execution_records,
    committed_block_digest,
    new_execution_id,
    observed_minute,
    proposal_id_for,
)
from backend.app.jobs.lifecycle import (
    CommittedJobError,
    CommittedStateIntegrityError,
    ExecutionTokenError,
    ExecutionTransitionKind,
    InvalidTransitionError,
    StaleExecutionError,
    StaleProposalError,
    _raise_not_before,
    plan_execution_complete,
    plan_execution_not_completed,
    plan_execution_start,
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


# ----------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "jobs.db"


@pytest.fixture()
def service(db_path: Path) -> JobService:
    return JobService(repository=JobRepository(db_path))


@pytest.fixture()
def optimizer(service: JobService) -> JobOptimizationService:
    return JobOptimizationService(service)


def local(minute: float) -> str:
    """An observation at a horizon-relative minute, stated in +05:30."""

    return (HORIZON + timedelta(minutes=minute)).isoformat()


def recorded(minute: float) -> str:
    """A canonical recording time (event occurred_at) at a horizon-relative minute."""

    return event_timestamp(HORIZON + timedelta(minutes=minute))


def evidence(reference: str, minute: float, **kwargs) -> EvidenceItem:
    return EvidenceItem(
        evidence_reference=reference,
        evidence_kind=kwargs.pop("kind", EvidenceKind.PHOTO),
        captured_at=local(minute),
        **kwargs,
    )


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


def approve_current(service, job_id) -> tuple[dict, str]:
    """Approve the job's current proposal; return the row and the approved digest."""

    digest = service.current_proposal(job_id, actor=AUTHORITY).digest()
    run_id = proposal_run_id_of(service.repository.get(job_id))
    service.notify(job_id, actor=AUTHORITY, expected_proposal_run_id=run_id)
    stored = service.repository.get(job_id)
    assert stored["status"] == "notified"
    return stored, digest


def commit_job(service, optimizer, **kwargs) -> dict:
    job = report(service, **kwargs)
    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    stored, _ = approve_current(service, job["job_id"])
    return stored


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


def history(service, job_id) -> list[JobEvent]:
    return [stored.event for stored in service.history.list_for_job(job_id)]


def records(service, job_id):
    return build_execution_records(
        service.repository.get(job_id), service.history.list_for_job(job_id)
    )


def start_plan(job, *, execution_id=None, actor=WORKER, start_minute=None,
               at_minute=None, before=None, expected_run="current",
               previous=(), corridor_id=CORRIDOR):
    planned = job["schedule_start_minute"]
    start_minute = planned + 1 if start_minute is None else start_minute
    return plan_execution_start(
        job["job_id"],
        actor=actor,
        at=recorded(start_minute + 1 if at_minute is None else at_minute),
        execution_id=execution_id or new_execution_id(),
        expected_proposal_run_id=(
            proposal_run_id_of(job) if expected_run == "current" else expected_run
        ),
        actual_start_at=local(start_minute),
        before_work_evidence=(
            [evidence("before/photo-1.jpg", start_minute - 5)] if before is None else before
        ),
        corridor_id=corridor_id,
        previous_executions=previous,
    )


def start(service, job, **kwargs) -> dict:
    job_id = job["job_id"]
    return service.repository.mutate_jobs(
        [job_id], start_plan(job, previous=records(service, job_id), **kwargs)
    )[job_id]


def open_record(service, job_id):
    return records(service, job_id)[-1]


def complete_plan(job, record, *, execution_id=None, actor=WORKER, end_minute=None,
                  at_minute=None, after=None, corridor_id=CORRIDOR):
    end_minute = job["schedule_end_minute"] + 10.5 if end_minute is None else end_minute
    return plan_execution_complete(
        job["job_id"],
        actor=actor,
        at=recorded(end_minute + 1 if at_minute is None else at_minute),
        execution_id=execution_id or record.execution_id,
        actual_end_at=None if end_minute == "missing" else local(end_minute),
        after_work_evidence=(
            [evidence("after/photo-1.jpg", end_minute - 1)] if after is None else after
        ),
        execution=record,
        corridor_id=corridor_id,
    )


def not_completed_plan(job, record, *, execution_id=None, actor=WORKER,
                       end_minute=None, at_minute=None, reason="Rail temperature too high",
                       failure=(), horizon_minutes=2880, actual_end_at="default"):
    end_minute = job["schedule_start_minute"] + 45.25 if end_minute is None else end_minute
    return plan_execution_not_completed(
        job["job_id"],
        actor=actor,
        at=recorded(end_minute + 1 if at_minute is None else at_minute),
        execution_id=execution_id or record.execution_id,
        actual_end_at=local(end_minute) if actual_end_at == "default" else actual_end_at,
        reason=reason,
        execution=record,
        corridor_id=CORRIDOR,
        horizon_minutes=horizon_minutes,
        failure_evidence=failure,
    )


def in_progress(service, optimizer, **kwargs) -> dict:
    job = commit_job(service, optimizer, **kwargs)
    started = start(service, job)
    assert started["status"] == "in_progress"
    return started


def assert_refused_atomically(service, db_path, job_id, plan, error):
    row_before = raw_row(db_path, job_id)
    events_before = count_events(db_path)

    with pytest.raises(error):
        service.repository.mutate_jobs([job_id], plan)

    assert raw_row(db_path, job_id) == row_before
    assert count_events(db_path) == events_before


# ----------------------------------------------------------------------
# 1. Evidence value object
# ----------------------------------------------------------------------


def test_valid_evidence_is_normalized():
    item = EvidenceItem(
        evidence_reference="  s3://evidence/job-1/before.jpg  ",
        evidence_kind="MEASUREMENT",
        captured_at="2026-09-10T06:15:30.25+05:30",
        latitude=19,
        longitude=72.8777,
        note="Gauge 1676 mm",
    )

    assert item.evidence_reference == "s3://evidence/job-1/before.jpg"
    assert item.evidence_kind is EvidenceKind.MEASUREMENT
    assert item.captured_at == "2026-09-10T00:45:30.250000+00:00"
    assert (item.latitude, item.longitude) == (19.0, 72.8777)
    assert isinstance(item.latitude, float)

    minimal = EvidenceItem("ref", EvidenceKind.PHOTO, "2026-09-10T00:00:00Z")
    assert (minimal.latitude, minimal.longitude, minimal.note) == (None, None, None)


@pytest.mark.parametrize(
    "overrides",
    [
        {"evidence_reference": ""},
        {"evidence_reference": "   \t "},
        {"evidence_reference": "x" * 201},
        {"evidence_reference": None},
        {"evidence_kind": "SELFIE"},
        {"captured_at": "2026-09-10T06:15:00"},
        {"captured_at": "not a time"},
        {"captured_at": None},
        {"latitude": 90.5, "longitude": 10.0},
        {"latitude": -91, "longitude": 10.0},
        {"latitude": 10.0, "longitude": 180.01},
        {"latitude": 10.0, "longitude": -181},
        {"latitude": 10.0},
        {"longitude": 10.0},
        {"latitude": True, "longitude": 10.0},
        {"latitude": math.nan, "longitude": 10.0},
        {"latitude": "19.0", "longitude": "72.0"},
        {"note": "n" * 501},
        {"note": 42},
    ],
)
def test_invalid_evidence_is_refused(overrides):
    fields = {
        "evidence_reference": "ref",
        "evidence_kind": "PHOTO",
        "captured_at": "2026-09-10T06:15:00+05:30",
        **overrides,
    }

    with pytest.raises(EvidenceValidationError):
        EvidenceItem(**fields)


def test_evidence_boundaries_are_inclusive():
    EvidenceItem("x" * 200, "VIDEO", local(0), latitude=-90, longitude=180, note="n" * 500)
    EvidenceItem("y", "DOCUMENT", local(0), latitude=90, longitude=-180)


def test_evidence_accepts_no_arbitrary_metadata():
    with pytest.raises(TypeError):
        EvidenceItem("ref", "PHOTO", local(0), metadata={"device": "x"})


def test_stored_evidence_round_trips_strictly():
    stored = RecordedEvidence.record(
        evidence("ref", 10, latitude=1.5, longitude=2.5), EvidencePhase.AFTER_WORK,
        "EVD-" + "A" * 32,
    ).to_dict()

    assert RecordedEvidence.from_dict(stored).to_dict() == stored
    assert json.loads(canonical_json(stored)) == stored

    for broken in (
        {**stored, "extra": 1},
        {**stored, "phase": "DURING"},
        {**stored, "evidence_id": "EVD-1"},
        {**stored, "captured_at": "2026-09-10T00:10:00+05:30"},
    ):
        with pytest.raises(EvidenceValidationError):
            RecordedEvidence.from_dict(broken)


# ----------------------------------------------------------------------
# 2. Horizon arithmetic
# ----------------------------------------------------------------------


def test_actual_minutes_use_the_horizon_anchor_floor_and_ceil():
    at = lambda text: datetime.fromisoformat(text)  # noqa: E731

    # 03:00 UTC is 08:30 +05:30: minute 510, not 180.
    assert observed_minute(at("2026-09-10T03:00:00+00:00"), DEFAULT_HORIZON_START, ceil=False) == 510
    assert observed_minute(at("2026-09-10T08:30:00+05:30"), DEFAULT_HORIZON_START, ceil=True) == 510
    assert observed_minute(at("2026-09-10T08:30:00.000001+05:30"), DEFAULT_HORIZON_START, ceil=False) == 510
    assert observed_minute(at("2026-09-10T08:30:00.000001+05:30"), DEFAULT_HORIZON_START, ceil=True) == 511
    assert observed_minute(at("2026-09-10T08:30:59+05:30"), DEFAULT_HORIZON_START, ceil=False) == 510
    # Second day, and before the horizon: never clamped.
    assert observed_minute(at("2026-09-11T00:01:00+05:30"), DEFAULT_HORIZON_START, ceil=False) == 1441
    assert observed_minute(at("2026-09-09T23:59:30+05:30"), DEFAULT_HORIZON_START, ceil=False) == -1
    assert observed_minute(at("2026-09-09T23:59:30+05:30"), DEFAULT_HORIZON_START, ceil=True) == 0
    assert observed_minute(at("2026-09-13T00:00:00+05:30"), DEFAULT_HORIZON_START, ceil=True) == 4320


# ----------------------------------------------------------------------
# 3. Start
# ----------------------------------------------------------------------


def test_start_moves_notified_to_in_progress_without_touching_the_block(service, optimizer):
    job = report(service)
    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    notified, approved_digest = approve_current(service, job["job_id"])
    job_id = job["job_id"]
    run_id = proposal_run_id_of(notified)
    planned = (notified["schedule_start_minute"], notified["schedule_end_minute"])
    execution_id = new_execution_id()

    started = start(service, notified, execution_id=execution_id)

    assert started["status"] == "in_progress"
    assert (started["schedule_start_minute"], started["schedule_end_minute"]) == planned
    assert started["track_id"] == notified["track_id"]
    assert started["block_candidate"]["status"] == "COMMITTED"
    assert started["block_candidate"]["is_committed"] is True

    expected_block = copy.deepcopy(notified["block_candidate"])
    expected_block["metadata"][EXECUTION_ID_KEY] = execution_id
    assert started["block_candidate"] == expected_block

    for column in ("priority_score", "risk_score", "last_solver_status", "last_refusal_reason"):
        assert started[column] == notified[column]

    event = history(service, job_id)[-1]
    assert event.event_type is E.EXECUTION_STARTED
    assert event.actor == WORKER
    assert event.optimization_run_id == run_id
    assert event.before_state.status == "notified"
    assert event.after_state.status == "in_progress"
    assert event.after_state.proposal_run_id == run_id

    meta = event.metadata
    assert meta[EXECUTION_ID_KEY] == execution_id
    assert meta["attempt_number"] == 1
    assert meta["optimization_run_id"] == run_id
    assert meta["proposal_id"] == f"PROP-{run_id}-{job_id}"
    assert meta["track_id"] == notified["track_id"]
    assert meta["section_id"] == notified["block_candidate"]["section_id"]
    assert (meta["planned_start_minute"], meta["planned_end_minute"]) == planned
    assert meta["committed_block_digest"] == approved_digest
    assert meta["actual_start_at"] == recorded(planned[0] + 1)
    assert meta["actual_start_minute"] == planned[0] + 1
    assert meta["deviations"] == {"started_before_planned_start": False}

    (before,) = meta["before_work_evidence"]
    assert before["phase"] == "BEFORE_WORK"
    assert before["evidence_id"].startswith("EVD-")
    assert before["evidence_reference"] == "before/photo-1.jpg"
    assert before["captured_at"] == recorded(planned[0] - 4)
    assert "recorded_by" not in before and "recorded_at" not in before


def test_start_outside_the_planned_window_is_recorded_not_refused(service, optimizer):
    job = commit_job(service, optimizer)
    early = job["schedule_start_minute"] - 20

    start(service, job, start_minute=early)

    meta = history(service, job["job_id"])[-1].metadata
    assert meta["actual_start_minute"] == early
    assert meta["deviations"] == {"started_before_planned_start": True}


def test_start_accepts_up_to_ten_before_items_and_captured_at_equal_to_start(service, optimizer):
    job = commit_job(service, optimizer)
    s = job["schedule_start_minute"] + 1
    items = [evidence(f"before/{i}", s - i) for i in range(10)]

    start(service, job, before=items)

    stored = history(service, job["job_id"])[-1].metadata["before_work_evidence"]
    assert [item["evidence_reference"] for item in stored] == [f"before/{i}" for i in range(10)]
    assert len({item["evidence_id"] for item in stored}) == 10


@pytest.mark.parametrize(
    "case",
    [
        "no_evidence",
        "eleven_items",
        "duplicate_reference",
        "captured_after_start",
        "start_in_future",
        "naive_start",
        "missing_start",
        "unidentified_actor",
        "system_actor",
        "malformed_execution_id",
    ],
)
def test_start_refusals_are_invalid_transitions_and_atomic(service, optimizer, db_path, case):
    job = commit_job(service, optimizer)
    s = job["schedule_start_minute"] + 1
    kwargs = {}

    if case == "no_evidence":
        kwargs["before"] = []
    elif case == "eleven_items":
        kwargs["before"] = [evidence(f"b{i}", s - 1) for i in range(11)]
    elif case == "duplicate_reference":
        kwargs["before"] = [evidence("same", s - 2), evidence(" same ", s - 1)]
    elif case == "captured_after_start":
        kwargs["before"] = [evidence("late", s + 0.01)]
    elif case == "start_in_future":
        kwargs.update(start_minute=s + 6, at_minute=s, before=[evidence("b", s)])
    elif case == "unidentified_actor":
        kwargs["actor"] = unidentified_actor()
    elif case == "system_actor":
        kwargs["actor"] = OPTIMIZER
    elif case == "malformed_execution_id":
        kwargs["execution_id"] = "EXE-not-a-uuid"

    plan = start_plan(job, **kwargs)

    if case in ("naive_start", "missing_start"):
        plan = plan_execution_start(
            job["job_id"],
            actor=WORKER,
            at=recorded(s + 1),
            execution_id=new_execution_id(),
            expected_proposal_run_id=proposal_run_id_of(job),
            actual_start_at=(
                (HORIZON + timedelta(minutes=s)).replace(tzinfo=None).isoformat()
                if case == "naive_start"
                else None
            ),
            before_work_evidence=[evidence("b", s - 1)],
            corridor_id=CORRIDOR,
        )

    assert_refused_atomically(service, db_path, job["job_id"], plan, InvalidTransitionError)


def test_start_at_exactly_the_future_skew_is_accepted(service, optimizer):
    job = commit_job(service, optimizer)
    s = job["schedule_start_minute"]
    started = start(service, job, start_minute=s + 5, at_minute=s, before=[evidence("b", s + 5)])
    assert started["status"] == "in_progress"


def test_start_with_a_stale_run_is_refused(service, optimizer, db_path):
    job = commit_job(service, optimizer)
    assert_refused_atomically(
        service, db_path, job["job_id"],
        start_plan(job, expected_run="RUN-SOMETHING-OLDER"), StaleProposalError,
    )
    assert_refused_atomically(
        service, db_path, job["job_id"], start_plan(job, expected_run=""), InvalidTransitionError,
    )


@pytest.mark.parametrize("status", ["reported", "scheduled", "in_progress", "completed"])
def test_start_requires_notified(service, optimizer, db_path, status):
    job = report(service)
    job_id = job["job_id"]

    if status != "reported":
        optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    if status in ("in_progress", "completed"):
        approve_current(service, job_id)
    if status == "in_progress":
        start(service, service.repository.get(job_id))
    if status == "completed":
        service.complete(job_id, actor=WORKER)

    current = service.repository.get(job_id)
    assert current["status"] == status
    template = current if current["schedule_start_minute"] is not None else {
        **current, "schedule_start_minute": 30, "schedule_end_minute": 150,
    }
    assert_refused_atomically(
        service, db_path, job_id,
        start_plan(template, expected_run=proposal_run_id_of(current) or "RUN-X"),
        InvalidTransitionError,
    )


def test_start_refuses_a_corrupted_committed_row_as_integrity(service, optimizer, db_path):
    job = commit_job(service, optimizer)

    with closing(sqlite3.connect(db_path)) as conn, conn:
        block = dict(job["block_candidate"], status="SCHEDULED")
        conn.execute(
            "UPDATE maintenance_jobs SET block_candidate_json = ? WHERE job_id = ?",
            (json.dumps(block), job["job_id"]),
        )

    assert_refused_atomically(
        service, db_path, job["job_id"], start_plan(job), CommittedStateIntegrityError
    )


def test_start_refuses_history_that_already_executed_this_generation(service, optimizer):
    job = commit_job(service, optimizer)
    started = start(service, job)
    record = open_record(service, job["job_id"])

    with pytest.raises(ExecutionIntegrityError):
        start_plan(job, previous=[record])({job["job_id"]: job})

    done = replace(record, status=ExecutionStatus.NOT_COMPLETED)
    with pytest.raises(ExecutionIntegrityError):
        start_plan(job, previous=[done])({job["job_id"]: job})

    assert started["status"] == "in_progress"


def test_start_plan_is_pure(service, optimizer, db_path):
    job = commit_job(service, optimizer)
    rows = {job["job_id"]: copy.deepcopy(job)}
    frozen_rows = copy.deepcopy(rows)
    row_before = raw_row(db_path, job["job_id"])
    events_before = count_events(db_path)
    execution_id = new_execution_id()

    plan = start_plan(job, execution_id=execution_id)
    first = plan(rows)
    second = plan(rows)

    assert rows == frozen_rows
    assert raw_row(db_path, job["job_id"]) == row_before
    assert count_events(db_path) == events_before

    for mutations, events in (first, second):
        (mutation,) = mutations
        (event,) = events
        assert mutation.execution.kind is ExecutionTransitionKind.START
        assert mutation.execution.execution_id == execution_id
        assert mutation.block_candidate["metadata"][EXECUTION_ID_KEY] == execution_id
        assert event.metadata[EXECUTION_ID_KEY] == execution_id

    assert first[0][0] == second[0][0]
    strip = lambda ev: {k: v for k, v in ev.to_dict().items() if k != "event_id"}  # noqa: E731
    a, b = strip(first[1][0]), strip(second[1][0])
    for side in (a, b):
        for item in side["metadata"]["before_work_evidence"]:
            item.pop("evidence_id")
    assert a == b


# ----------------------------------------------------------------------
# 4. Complete
# ----------------------------------------------------------------------


def test_complete_moves_in_progress_to_completed_with_evidence(service, optimizer):
    job = in_progress(service, optimizer)
    job_id = job["job_id"]
    record = open_record(service, job_id)
    end = job["schedule_end_minute"] + 10.5

    done = service.repository.mutate_jobs([job_id], complete_plan(job, record))[job_id]

    assert done["status"] == "completed"
    assert done["block_candidate"] == job["block_candidate"]
    assert done["block_candidate"]["metadata"][EXECUTION_ID_KEY] == record.execution_id
    assert (done["schedule_start_minute"], done["schedule_end_minute"]) == (
        job["schedule_start_minute"], job["schedule_end_minute"],
    )

    event = history(service, job_id)[-1]
    assert event.event_type is E.EXECUTION_COMPLETED
    assert event.actor == WORKER
    assert event.optimization_run_id == record.optimization_run_id
    meta = event.metadata
    assert meta[EXECUTION_ID_KEY] == record.execution_id
    assert meta["committed_block_digest"] == record.committed_block_digest
    assert meta["actual_end_at"] == recorded(end)
    assert meta["actual_end_minute"] == math.ceil(end)
    assert meta["deviations"] == {
        "started_before_planned_start": False,
        "ended_after_planned_end": True,
    }
    (after,) = meta["after_work_evidence"]
    assert after["phase"] == "AFTER_WORK"


def test_complete_inside_the_window_records_no_end_deviation(service, optimizer):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])
    end = job["schedule_end_minute"] - 3

    service.repository.mutate_jobs([job["job_id"]], complete_plan(job, record, end_minute=end))

    assert history(service, job["job_id"])[-1].metadata["deviations"][
        "ended_after_planned_end"
    ] is False


@pytest.mark.parametrize(
    "case",
    [
        "no_after_evidence",
        "eleven_after_items",
        "after_before_actual_start",
        "after_reuses_before_reference",
        "end_before_start",
        "missing_end",
        "end_in_future",
        "after_evidence_in_future",
        "unidentified_actor",
    ],
)
def test_complete_refusals_are_invalid_transitions_and_atomic(service, optimizer, db_path, case):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])
    actual_start = record.actual_start_minute
    end = job["schedule_end_minute"]
    kwargs = {}

    if case == "no_after_evidence":
        kwargs["after"] = []
    elif case == "eleven_after_items":
        kwargs["after"] = [evidence(f"a{i}", end - 1) for i in range(11)]
    elif case == "after_before_actual_start":
        kwargs["after"] = [evidence("after/early", actual_start - 0.5)]
    elif case == "after_reuses_before_reference":
        kwargs["after"] = [evidence(record.before_work_evidence[0].evidence_reference, end - 1)]
    elif case == "end_before_start":
        kwargs.update(end_minute=actual_start - 1, after=[evidence("a", actual_start)])
    elif case == "missing_end":
        kwargs.update(end_minute="missing", at_minute=end + 1, after=[evidence("a", end)])
    elif case == "end_in_future":
        kwargs.update(end_minute=end + 10, at_minute=end + 4, after=[evidence("a", end)])
    elif case == "after_evidence_in_future":
        kwargs.update(end_minute=end, at_minute=end + 1, after=[evidence("a", end + 6.5)])
    elif case == "unidentified_actor":
        kwargs["actor"] = unidentified_actor()

    assert_refused_atomically(
        service, db_path, job["job_id"], complete_plan(job, record, **kwargs),
        InvalidTransitionError,
    )


def test_complete_with_another_execution_id_is_stale(service, optimizer, db_path):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])
    other = new_execution_id()

    assert_refused_atomically(
        service, db_path, job["job_id"],
        complete_plan(job, replace(record, execution_id=other), execution_id=other),
        StaleExecutionError,
    )


def test_complete_with_a_changed_committed_block_digest_fails_closed(service, optimizer, db_path):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])

    assert_refused_atomically(
        service, db_path, job["job_id"],
        complete_plan(job, replace(record, committed_block_digest="0" * 64)),
        CommittedStateIntegrityError,
    )
    assert_refused_atomically(
        service, db_path, job["job_id"],
        complete_plan(job, record, corridor_id="CORRIDOR_B"),
        CommittedStateIntegrityError,
    )

    # An out-of-band change to the committed block's identity.
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute(
            "UPDATE maintenance_jobs SET work_type = 'RAIL_GRINDING' WHERE job_id = ?",
            (job["job_id"],),
        )

    assert_refused_atomically(
        service, db_path, job["job_id"], complete_plan(job, record),
        CommittedStateIntegrityError,
    )


def test_complete_with_a_record_that_is_not_the_open_execution_fails_closed(
    service, optimizer, db_path
):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])

    for forged in (
        replace(record, status=ExecutionStatus.COMPLETED),
        replace(record, optimization_run_id="RUN-OTHER"),
        replace(record, planned_end_minute=record.planned_end_minute + 1),
        replace(record, job_id="JOB-OTHER"),
    ):
        assert_refused_atomically(
            service, db_path, job["job_id"], complete_plan(job, forged),
            ExecutionIntegrityError,
        )


def test_complete_requires_an_in_progress_job(service, optimizer, db_path):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])
    notified = commit_job(service, optimizer, track_id="DOWN-1", distance_start=2000.0)

    assert_refused_atomically(
        service, db_path, notified["job_id"],
        complete_plan(notified, replace(record, job_id=notified["job_id"])),
        InvalidTransitionError,
    )


def test_a_plan_whose_event_is_dropped_is_rolled_back_by_the_guard(service, optimizer, db_path):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])
    real = complete_plan(job, record)

    def without_event(rows):
        mutations, _ = real(rows)
        return mutations, []

    assert_refused_atomically(service, db_path, job["job_id"], without_event, ExecutionTokenError)


# ----------------------------------------------------------------------
# 5. Not completed: release and replanning window
# ----------------------------------------------------------------------


def test_not_completed_releases_the_commitment_and_preserves_history(service, optimizer):
    job = in_progress(service, optimizer)
    job_id = job["job_id"]
    record = open_record(service, job_id)
    run_id = record.optimization_run_id
    prior = [event.canonical_bytes() for event in history(service, job_id)]
    planned = (job["schedule_start_minute"], job["schedule_end_minute"])
    end = planned[0] + 45.25

    released = service.repository.mutate_jobs(
        [job_id], not_completed_plan(job, record, end_minute=end)
    )[job_id]

    assert released["status"] == "reported"
    assert (released["schedule_start_minute"], released["schedule_end_minute"]) == (None, None)
    block = released["block_candidate"]
    assert block["status"] == "PLANNED"
    assert block["is_committed"] is False
    for key in ("committed_start_minute", "committed_end_minute", "proposal_run_id", EXECUTION_ID_KEY):
        assert key not in block["metadata"]
    assert released["last_refusal_reason"] == "Rail temperature too high"
    assert block["earliest_start_minute"] == planned[1]
    assert block["latest_end_minute"] == 2880

    events = history(service, job_id)
    assert [event.canonical_bytes() for event in events[: len(prior)]] == prior
    event = events[-1]
    assert event.event_type is E.EXECUTION_NOT_COMPLETED
    assert event.reason == "Rail temperature too high"
    assert event.optimization_run_id == run_id
    assert event.after_state.status == "reported"
    assert event.after_state.proposal_run_id is None

    meta = event.metadata
    assert meta[EXECUTION_ID_KEY] == record.execution_id
    assert meta["actual_end_at"] == recorded(end)
    assert meta["actual_end_minute"] == math.ceil(end)
    assert meta["failure_evidence"] == []
    assert (meta["released_start_minute"], meta["released_end_minute"]) == planned
    assert meta["released_proposal_run_id"] == run_id
    assert meta["committed_block_digest"] == record.committed_block_digest
    assert meta["previous_earliest_start_minute"] == 0
    assert meta["not_before_minute"] == planned[1]
    assert meta["original_latest_end_minute"] == 2880
    assert meta["widened_latest_end_minute"] == 2880
    assert meta["deviations"] == {
        "started_before_planned_start": False,
        "ended_after_planned_end": False,
    }

    kinds = [event.event_type for event in events]
    assert E.BLOCK_PROPOSED in kinds and E.BLOCK_COMMITTED in kinds
    committed = next(ev for ev in events if ev.event_type is E.BLOCK_COMMITTED)
    assert committed.optimization_run_id == run_id
    assert (committed.metadata["start_minute"], committed.metadata["end_minute"]) == planned


@pytest.mark.parametrize(
    "stored_earliest, end_offset, expected",
    [
        (0, 45.25, "planned_end"),          # planned end dominates
        (0, 300.5, "actual_end"),           # actual end (ceil) dominates
        (2000, 45.25, 2000),                # a prior not-before is never lowered
    ],
)
def test_not_completed_earliest_start_is_the_max_rule(
    service, optimizer, stored_earliest, end_offset, expected
):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])
    row = copy.deepcopy(job)
    row["block_candidate"]["earliest_start_minute"] = stored_earliest
    end = job["schedule_start_minute"] + end_offset

    (mutation,), (event,) = not_completed_plan(row, record, end_minute=end)(
        {job["job_id"]: row}
    )

    wanted = {
        "planned_end": job["schedule_end_minute"],
        "actual_end": math.ceil(end),
    }.get(expected, expected)
    assert mutation.block_candidate["earliest_start_minute"] == wanted
    assert event.metadata["not_before_minute"] == wanted
    assert event.metadata["previous_earliest_start_minute"] == stored_earliest
    assert mutation.execution.kind is ExecutionTransitionKind.NOT_COMPLETED
    assert mutation.execution.execution_id == record.execution_id


def test_not_completed_widens_latest_end_and_records_both_values(service, optimizer):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])

    for stored_latest, horizon, widened in ((1440, 2880, 2880), (4320, 2880, 4320)):
        row = copy.deepcopy(job)
        row["block_candidate"]["latest_end_minute"] = stored_latest

        (mutation,), (event,) = not_completed_plan(row, record, horizon_minutes=horizon)(
            {job["job_id"]: row}
        )

        assert mutation.block_candidate["latest_end_minute"] == widened
        assert event.metadata["original_latest_end_minute"] == stored_latest
        assert event.metadata["widened_latest_end_minute"] == widened


def test_not_completed_accepts_optional_failure_evidence(service, optimizer):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])
    end = job["schedule_start_minute"] + 45
    failure = [
        evidence("fail/photo.jpg", end - 2, latitude=19.07, longitude=72.87, note="Buckled"),
        evidence("fail/log.pdf", end - 1, kind=EvidenceKind.DOCUMENT),
    ]

    service.repository.mutate_jobs(
        [job["job_id"]], not_completed_plan(job, record, end_minute=end, failure=failure)
    )

    stored = history(service, job["job_id"])[-1].metadata["failure_evidence"]
    assert [item["phase"] for item in stored] == ["NOT_COMPLETED", "NOT_COMPLETED"]
    assert stored[0]["latitude"] == 19.07 and stored[0]["note"] == "Buckled"
    assert stored[1]["evidence_kind"] == "DOCUMENT"


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_not_completed_requires_a_reason(service, optimizer, reason):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])

    with pytest.raises(InvalidTransitionError):
        not_completed_plan(job, record, reason=reason)


@pytest.mark.parametrize(
    "case", ["missing_end", "end_before_start", "duplicate_failure_reference", "eleven_items",
             "system_actor"],
)
def test_not_completed_refusals_are_atomic(service, optimizer, db_path, case):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])
    s = record.actual_start_minute
    kwargs = {}

    if case == "missing_end":
        kwargs["actual_end_at"] = None
    elif case == "end_before_start":
        kwargs["end_minute"] = s - 2
    elif case == "duplicate_failure_reference":
        kwargs["failure"] = [evidence("f", s + 1), evidence("f", s + 2)]
    elif case == "eleven_items":
        kwargs["failure"] = [evidence(f"f{i}", s + 1) for i in range(11)]
    elif case == "system_actor":
        kwargs["actor"] = OPTIMIZER

    assert_refused_atomically(
        service, db_path, job["job_id"], not_completed_plan(job, record, **kwargs),
        InvalidTransitionError,
    )


def test_release_that_keeps_the_execution_id_is_refused_by_the_guard(service, optimizer, db_path):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])
    real = not_completed_plan(job, record)

    def keeps_execution_id(rows):
        (mutation,), events = real(rows)
        block = copy.deepcopy(mutation.block_candidate)
        block["metadata"][EXECUTION_ID_KEY] = record.execution_id
        return [replace(mutation, block_candidate=block)], events

    assert_refused_atomically(service, db_path, job["job_id"], keeps_execution_id, CommittedJobError)


def test_not_before_at_the_horizon_is_recorded_and_the_optimizer_honestly_refuses(
    service, optimizer
):
    job = in_progress(service, optimizer)
    job_id = job["job_id"]
    record = open_record(service, job_id)

    released = service.repository.mutate_jobs(
        [job_id], not_completed_plan(job, record, end_minute=service.horizon_minutes)
    )[job_id]

    assert released["status"] == "reported"
    assert released["block_candidate"]["earliest_start_minute"] == service.horizon_minutes

    other = report(service, track_id="DOWN-1", distance_start=2000.0)
    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)

    after = service.repository.get(job_id)
    assert after["status"] == "reported"
    assert after["schedule_start_minute"] is None
    last = history(service, job_id)[-1]
    assert last.event_type is E.OPTIMIZATION_REFUSED
    assert "duration does not fit within" in last.reason

    # The refusal is the released job's own; unrelated work is still planned.
    assert service.repository.get(other["job_id"])["status"] == "scheduled"
    assert history(service, other["job_id"])[-1].event_type is E.BLOCK_PROPOSED


def test_not_before_beyond_the_horizon_is_recorded_unclamped_and_never_proposed(
    service, optimizer
):
    """The release is never refused or clamped because of the horizon.

    The corridor-wide effect of a not-before strictly beyond the horizon on
    the NEXT optimization was a solver defect reported with Slice 5 Step 2:
    the window-infeasible placeholder's unconditional buffered_end equality
    made the whole CP-SAT model INFEASIBLE, refusing every other block on
    every other track along with the released one. Fixed in
    backend/app/optimizer/solver.py by skipping window-infeasible blocks in
    the no-overlap loop - see backend/tests/
    test_solver_window_infeasible_regression.py for the solver-level pins.
    This test now pins the corridor-wide effect too: the released job gets
    its own window refusal, and unrelated work on another track is still
    proposed.
    """

    job = in_progress(service, optimizer)
    job_id = job["job_id"]
    record = open_record(service, job_id)
    late_end = service.horizon_minutes + 30.5

    released = service.repository.mutate_jobs(
        [job_id], not_completed_plan(job, record, end_minute=late_end)
    )[job_id]

    assert released["status"] == "reported"
    assert released["block_candidate"]["earliest_start_minute"] == service.horizon_minutes + 31
    assert history(service, job_id)[-1].metadata["not_before_minute"] == service.horizon_minutes + 31

    other = report(service, track_id="DOWN-1", distance_start=2000.0)
    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)

    after = service.repository.get(job_id)
    assert after["status"] == "reported"
    assert after["schedule_start_minute"] is None
    assert proposal_run_id_of(after) is None
    last = history(service, job_id)[-1]
    assert last.event_type is E.OPTIMIZATION_REFUSED
    assert "duration does not fit within" in last.reason

    # The refusal is the released job's own; unrelated work is still planned.
    assert service.repository.get(other["job_id"])["status"] == "scheduled"
    assert history(service, other["job_id"])[-1].event_type is E.BLOCK_PROPOSED


# ----------------------------------------------------------------------
# 6. Execution records
# ----------------------------------------------------------------------


def test_a_successful_execution_reconstructs(service, optimizer):
    job = report(service)
    job_id = job["job_id"]
    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    notified, approved_digest = approve_current(service, job_id)
    execution_id = new_execution_id()
    start(service, notified, execution_id=execution_id)
    started = service.repository.get(job_id)

    (open_,) = records(service, job_id)
    assert open_.status is ExecutionStatus.STARTED
    assert open_.ended_by is None and open_.actual_end_at is None

    service.repository.mutate_jobs([job_id], complete_plan(started, open_))
    events = history(service, job_id)
    start_event = next(ev for ev in events if ev.event_type is E.EXECUTION_STARTED)
    end_event = events[-1]

    (record,) = records(service, job_id)
    run_id = proposal_run_id_of(notified)

    assert record.execution_id == execution_id
    assert record.job_id == job_id
    assert record.attempt_number == 1
    assert record.optimization_run_id == run_id
    assert record.proposal_id == proposal_id_for(run_id, job_id)
    assert record.track_id == notified["track_id"]
    assert record.section_id == notified["block_candidate"]["section_id"]
    assert (record.planned_start_minute, record.planned_end_minute) == (
        notified["schedule_start_minute"], notified["schedule_end_minute"],
    )
    assert record.committed_block_digest == approved_digest
    assert record.committed_block_digest == committed_block_digest(
        started, corridor_id=CORRIDOR, optimization_run_id=run_id
    )
    assert record.status is ExecutionStatus.COMPLETED
    assert record.started_by == WORKER.to_dict() == record.ended_by
    assert record.started_recorded_at == start_event.occurred_at
    assert record.ended_recorded_at == end_event.occurred_at
    assert record.actual_start_at == start_event.metadata["actual_start_at"]
    assert record.actual_start_minute == start_event.metadata["actual_start_minute"]
    assert [e.to_dict() for e in record.before_work_evidence] == start_event.metadata[
        "before_work_evidence"
    ]
    assert record.actual_end_at == end_event.metadata["actual_end_at"]
    assert record.actual_end_minute == end_event.metadata["actual_end_minute"]
    assert [e.to_dict() for e in record.after_work_evidence] == end_event.metadata[
        "after_work_evidence"
    ]
    assert record.not_completed_reason is None
    assert record.failure_evidence == ()
    assert record.deviations.to_dict() == end_event.metadata["deviations"]

    as_dict = record.to_dict()
    assert json.loads(canonical_json(as_dict)) == as_dict
    assert "created_at" not in as_dict and "updated_at" not in as_dict


def test_a_failed_execution_reconstructs(service, optimizer):
    job = in_progress(service, optimizer)
    job_id = job["job_id"]
    record = open_record(service, job_id)
    failure = [evidence("fail/1", record.actual_start_minute + 2)]

    service.repository.mutate_jobs(
        [job_id], not_completed_plan(job, record, reason="Track not released", failure=failure)
    )
    (rebuilt,) = records(service, job_id)
    end_event = history(service, job_id)[-1]

    assert rebuilt.status is ExecutionStatus.NOT_COMPLETED
    assert rebuilt.not_completed_reason == "Track not released"
    assert [e.to_dict() for e in rebuilt.failure_evidence] == end_event.metadata["failure_evidence"]
    assert rebuilt.after_work_evidence == ()
    assert rebuilt.actual_end_minute == end_event.metadata["actual_end_minute"]
    assert rebuilt.ended_by == WORKER.to_dict()
    assert rebuilt.committed_block_digest == record.committed_block_digest


def test_execution_events_round_trip_canonically(service, optimizer):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])
    service.repository.mutate_jobs([job["job_id"]], complete_plan(job, record))

    execution_events = [
        ev for ev in history(service, job["job_id"]) if ev.event_type.value.startswith("EXECUTION_")
    ]
    assert len(execution_events) == 2

    for event in execution_events:
        assert JobEvent.from_dict(event.to_dict()).canonical_bytes() == event.canonical_bytes()


def test_a_job_never_executed_has_no_records(service, optimizer):
    job = commit_job(service, optimizer)
    assert records(service, job["job_id"]) == []

    service.complete(job["job_id"], actor=WORKER)  # legacy completion, untouched
    assert records(service, job["job_id"]) == []


def _rebuild(event: JobEvent, **changes) -> JobEvent:
    data = event.to_dict()
    metadata = copy.deepcopy(data["metadata"])
    metadata.update(changes.pop("metadata", {}))
    for key in changes.pop("drop", ()):
        metadata.pop(key)
    data.update(metadata=metadata, event_id=new_event_id(), **changes)
    return JobEvent.from_dict(data)


@pytest.fixture()
def completed_history(service, optimizer):
    job = in_progress(service, optimizer)
    record = open_record(service, job["job_id"])
    service.repository.mutate_jobs([job["job_id"]], complete_plan(job, record))
    events = history(service, job["job_id"])
    start_index = next(i for i, ev in enumerate(events) if ev.event_type is E.EXECUTION_STARTED)
    return service.repository.get(job["job_id"]), events, start_index


def test_the_unaltered_fixture_history_is_valid(completed_history):
    row, events, _ = completed_history
    assert len(build_execution_records(row, events)) == 1


@pytest.mark.parametrize(
    "corruption",
    [
        "completed_without_started",
        "not_completed_without_started",
        "outcome_for_unknown_execution",
        "two_outcomes",
        "completed_then_not_completed",
        "outcome_run_mismatch",
        "metadata_run_mismatch",
        "event_for_another_job",
        "proposal_id_mismatch",
        "attempt_number_wrong",
        "attempt_number_not_int",
        "digest_missing",
        "outcome_digest_differs",
        "before_evidence_wrong_phase",
        "before_evidence_empty",
        "after_evidence_empty",
        "malformed_execution_id",
        "duplicate_start",
        "second_start_while_open",
        "non_canonical_actual_start",
        "end_before_start",
        "missing_deviations",
    ],
)
def test_malformed_execution_history_fails_closed(completed_history, corruption):
    row, events, i = completed_history
    started, outcome = events[i], events[-1]
    before, after = events[:i], events[i + 1 : -1]
    bad = list(events)

    if corruption == "completed_without_started":
        bad = before + after + [outcome]
    elif corruption == "not_completed_without_started":
        bad = before + after + [
            _rebuild(outcome, event_type="EXECUTION_NOT_COMPLETED", reason="x",
                     metadata={"failure_evidence": []})
        ]
    elif corruption == "outcome_for_unknown_execution":
        bad[-1] = _rebuild(outcome, metadata={EXECUTION_ID_KEY: new_execution_id()})
    elif corruption == "two_outcomes":
        bad.append(_rebuild(outcome))
    elif corruption == "completed_then_not_completed":
        bad.append(_rebuild(outcome, event_type="EXECUTION_NOT_COMPLETED", reason="x",
                            metadata={"failure_evidence": []}))
    elif corruption == "outcome_run_mismatch":
        bad[-1] = _rebuild(outcome, optimization_run_id="RUN-OTHER",
                           metadata={"optimization_run_id": "RUN-OTHER",
                                     "proposal_id": proposal_id_for("RUN-OTHER", row["job_id"])})
    elif corruption == "metadata_run_mismatch":
        bad[i] = _rebuild(started, metadata={"optimization_run_id": "RUN-OTHER"})
    elif corruption == "event_for_another_job":
        bad[i] = _rebuild(started, job_id="JOB-SOMEONE-ELSE")
    elif corruption == "proposal_id_mismatch":
        bad[i] = _rebuild(started, metadata={"proposal_id": "PROP-RUN-X-JOB-Y"})
    elif corruption == "attempt_number_wrong":
        bad[i] = _rebuild(started, metadata={"attempt_number": 2})
    elif corruption == "attempt_number_not_int":
        bad[i] = _rebuild(started, metadata={"attempt_number": "1"})
    elif corruption == "digest_missing":
        bad[i] = _rebuild(started, drop=["committed_block_digest"])
    elif corruption == "outcome_digest_differs":
        bad[-1] = _rebuild(outcome, metadata={"committed_block_digest": "f" * 64})
    elif corruption == "before_evidence_wrong_phase":
        items = copy.deepcopy(started.metadata["before_work_evidence"])
        items[0]["phase"] = "AFTER_WORK"
        bad[i] = _rebuild(started, metadata={"before_work_evidence": items})
    elif corruption == "before_evidence_empty":
        bad[i] = _rebuild(started, metadata={"before_work_evidence": []})
    elif corruption == "after_evidence_empty":
        bad[-1] = _rebuild(outcome, metadata={"after_work_evidence": []})
    elif corruption == "malformed_execution_id":
        bad[i] = _rebuild(started, metadata={EXECUTION_ID_KEY: "EXE-1"})
    elif corruption == "duplicate_start":
        bad = before + [started, _rebuild(started)] + after + [outcome]
    elif corruption == "second_start_while_open":
        second = _rebuild(started, metadata={EXECUTION_ID_KEY: new_execution_id(),
                                             "attempt_number": 2})
        bad = before + [started, second] + after + [outcome]
    elif corruption == "non_canonical_actual_start":
        bad[i] = _rebuild(started, metadata={"actual_start_at": local(10)})
    elif corruption == "end_before_start":
        bad[-1] = _rebuild(outcome, metadata={"actual_end_at": recorded(-100)})
    elif corruption == "missing_deviations":
        bad[i] = _rebuild(started, drop=["deviations"])

    with pytest.raises(ExecutionIntegrityError):
        build_execution_records(row, bad)


def test_row_and_history_disagreement_fails_closed(service, optimizer):
    job = in_progress(service, optimizer)
    job_id = job["job_id"]
    events = service.history.list_for_job(job_id)
    started_index = next(
        i for i, s in enumerate(events) if s.event.event_type is E.EXECUTION_STARTED
    )

    # in_progress row, but no open execution in history.
    with pytest.raises(ExecutionIntegrityError):
        build_execution_records(job, events[:started_index])

    # in_progress row naming a different execution than the open one.
    other = copy.deepcopy(job)
    other["block_candidate"]["metadata"][EXECUTION_ID_KEY] = new_execution_id()
    with pytest.raises(ExecutionIntegrityError):
        build_execution_records(other, events)

    # a reported (or notified) row while history has an open execution.
    for status in ("reported", "notified", "completed"):
        with pytest.raises(ExecutionIntegrityError):
            build_execution_records({**job, "status": status}, events)


# ----------------------------------------------------------------------
# 7. Replanning: a new generation, the old one stays historical
# ----------------------------------------------------------------------


def test_replanning_creates_a_new_generation_and_the_old_one_cannot_act(service, optimizer):
    job = report(service)
    job_id = job["job_id"]

    # Generation 1: propose, approve, start, not completed.
    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    gen1, digest1 = approve_current(service, job_id)
    r1 = proposal_run_id_of(gen1)
    e1 = new_execution_id()
    started1 = start(service, gen1, execution_id=e1)
    record1 = open_record(service, job_id)
    service.repository.mutate_jobs([job_id], not_completed_plan(started1, record1))
    released = service.repository.get(job_id)
    not_before = released["block_candidate"]["earliest_start_minute"]
    r1_history = [ev.canonical_bytes() for ev in history(service, job_id)]

    # The released generation cannot act.
    with pytest.raises(InvalidTransitionError):
        start_plan(gen1, expected_run=r1)({job_id: released})
    with pytest.raises(InvalidTransitionError):
        complete_plan(started1, record1)({job_id: released})
    with pytest.raises(InvalidTransitionError):
        not_completed_plan(started1, record1)({job_id: released})
    with pytest.raises(InvalidTransitionError):
        service.notify(job_id, actor=AUTHORITY, expected_proposal_run_id=r1)

    # Generation 2: a new run proposes a new block no earlier than not_before.
    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    proposed2 = service.repository.get(job_id)
    r2 = proposal_run_id_of(proposed2)
    assert proposed2["status"] == "scheduled"
    assert r2 and r2 != r1
    assert proposed2["schedule_start_minute"] >= not_before
    proposal2 = service.current_proposal(job_id, actor=AUTHORITY)
    assert proposal2.proposal_id == proposal_id_for(r2, job_id) != proposal_id_for(r1, job_id)
    assert proposal2.digest() != digest1

    with pytest.raises(StaleProposalError):
        service.notify(job_id, actor=AUTHORITY, expected_proposal_run_id=r1)

    gen2, digest2 = approve_current(service, job_id)

    with pytest.raises(StaleProposalError):
        start_plan(gen2, expected_run=r1)({job_id: gen2})
    with pytest.raises(InvalidTransitionError):
        complete_plan(started1, record1)({job_id: gen2})

    e2 = new_execution_id()
    started2 = start(service, gen2, execution_id=e2)
    assert e2 != e1

    # E1 cannot touch E2.
    for plan in (
        complete_plan(started1, record1, at_minute=gen2["schedule_end_minute"] + 20),
        not_completed_plan(started1, record1),
    ):
        with pytest.raises(StaleExecutionError):
            service.repository.mutate_jobs([job_id], plan)

    record2 = open_record(service, job_id)
    done = service.repository.mutate_jobs([job_id], complete_plan(started2, record2))[job_id]
    assert done["status"] == "completed"
    assert done["block_candidate"]["metadata"][EXECUTION_ID_KEY] == e2

    first, second = records(service, job_id)
    assert (first.execution_id, first.attempt_number, first.optimization_run_id) == (e1, 1, r1)
    assert first.status is ExecutionStatus.NOT_COMPLETED
    assert (first.planned_start_minute, first.planned_end_minute) == (
        gen1["schedule_start_minute"], gen1["schedule_end_minute"],
    )
    assert first.committed_block_digest == digest1
    assert (second.execution_id, second.attempt_number, second.optimization_run_id) == (e2, 2, r2)
    assert second.status is ExecutionStatus.COMPLETED
    assert second.proposal_id == proposal_id_for(r2, job_id)
    assert second.committed_block_digest == digest2

    # Generation 1's history is untouched by everything that followed.
    assert [ev.canonical_bytes() for ev in history(service, job_id)][: len(r1_history)] == r1_history
    r1_types = {ev.event_type for ev in history(service, job_id) if ev.optimization_run_id == r1}
    assert {E.BLOCK_PROPOSED, E.BLOCK_COMMITTED, E.EXECUTION_STARTED,
            E.EXECUTION_NOT_COMPLETED} <= r1_types


# ----------------------------------------------------------------------
# 8. Postpone helper regression
# ----------------------------------------------------------------------


def _inline_postpone_window(block, not_before_minute, horizon_minutes):
    """plan_postpone's window code exactly as it was before extraction."""

    postponed_block = copy.deepcopy(block)
    postponed_block["earliest_start_minute"] = int(not_before_minute)
    original_latest_end = int(postponed_block.get("latest_end_minute", 1440))
    widened_latest_end = max(original_latest_end, int(horizon_minutes))
    postponed_block["latest_end_minute"] = widened_latest_end
    return postponed_block, original_latest_end, widened_latest_end


@pytest.mark.parametrize(
    "block, not_before, horizon",
    [
        ({"earliest_start_minute": 0, "latest_end_minute": 1440, "x": 1}, 1500, 2880),
        ({"earliest_start_minute": 2000, "latest_end_minute": 4320}, 100, 2880),
        ({"earliest_start_minute": 5}, 10, 1440),
        ({}, 0, 2880),
    ],
)
def test_postpone_window_is_unchanged_by_the_helper_extraction(block, not_before, horizon):
    frozen = copy.deepcopy(block)
    new_block, earliest, original, widened = _raise_not_before(
        block, not_before, horizon, never_lower=False
    )
    expected_block, expected_original, expected_widened = _inline_postpone_window(
        block, not_before, horizon
    )

    assert block == frozen
    assert json.dumps(new_block) == json.dumps(expected_block)
    assert (earliest, original, widened) == (not_before, expected_original, expected_widened)

    lowered_block, lowered_earliest, _, _ = _raise_not_before(
        block, not_before, horizon, never_lower=True
    )
    assert lowered_earliest == max(block.get("earliest_start_minute", 0), not_before)
    assert lowered_block["earliest_start_minute"] == lowered_earliest
