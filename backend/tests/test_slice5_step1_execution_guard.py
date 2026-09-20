"""Sprint 3 Slice 5, Step 1: in_progress status and the execution guard.

Pins, against the real repository, lifecycle guard and optimizer:

  STATUS        in_progress is a real, committed (pinned) status.
  TOKEN GATE    notified -> in_progress, in_progress -> completed and
                in_progress -> reported are permitted only for a
                JobMutation carrying the matching ExecutionTransition.
  CROSS-CHECK   mutate_jobs refuses a token that is not backed by exactly
                one matching EXECUTION_* event (and an execution event not
                backed by its token), rolling the whole transaction back.
  NO BYPASS     the history-less primitives (update_status, update_schedule)
                can never start, complete or release execution.
  CLOSED        (Step 4) there is no direct notified -> completed: every
                entry into completed needs a COMPLETE token from in_progress.

No execution service plan exists yet (Step 2), so the plans that move a
job into and out of in_progress here are written inline, exactly as a
future planner would shape them. Every test uses an isolated database.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.identity.actor import ActorRole, human_actor
from backend.app.jobs.events import (
    JobEventType,
    JobStateSnapshot,
    event_timestamp,
    make_event,
)
from backend.app.jobs.lifecycle import (
    COMMITTED_STATUSES,
    EXECUTION_EVENT_TYPES,
    EXECUTION_ID_KEY,
    TERMINAL_STATUSES,
    CommittedJobError,
    CommittedStateIntegrityError,
    ExecutionTokenError,
    ExecutionTransition,
    ExecutionTransitionKind,
    JobMutation,
    TerminalJobError,
    _withdrawn,
    committed_state_problems,
    proposal_run_id_of,
    protect_committed_and_terminal_state,
)
from backend.app.jobs.models import JobCreateRequest, JobStatus
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService


WORKER = human_actor("WORKER-042", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-003", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-017", ActorRole.AUTHORITY)

E = JobEventType
K = ExecutionTransitionKind

EXECUTION_ID = "EXE-STEP1-0001"


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
    job = report(service, **kwargs)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    service.notify(job["job_id"], actor=AUTHORITY)
    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "notified"
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


def event_types(service, job_id) -> list[str]:
    return [
        item.event.event_type.value
        for item in service.history.list_for_job(job_id)
    ]


def corrupt_block_metadata(db_path, job_id, *, set_=None, drop=()) -> None:
    """Edit block metadata directly, bypassing every repository guard."""

    with closing(sqlite3.connect(db_path)) as conn, conn:
        block = json.loads(
            conn.execute(
                "SELECT block_candidate_json FROM maintenance_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
        )
        metadata = block.setdefault("metadata", {})
        metadata.update(set_ or {})
        for key in drop:
            metadata.pop(key, None)
        conn.execute(
            "UPDATE maintenance_jobs SET block_candidate_json = ? WHERE job_id = ?",
            (json.dumps(block), job_id),
        )


# Slice 5 audit F1 (added in Slice 6): validate_execution_events refuses an
# EXECUTION_COMPLETED event that records no after-work evidence, so a
# hand-built completion must carry some - exactly as the real
# plan_execution_complete does. The shape mirrors record_evidence() output.
# The refusal itself is covered by test_slice6_authority_release.py.
_AFTER_WORK_EVIDENCE = [
    {
        "evidence_id": "EVD-HANDBUILT-AFTER-WORK",
        "phase": "AFTER_WORK",
        "evidence_kind": "PHOTO",
        "evidence_reference": "EVIDENCE-HANDBUILT-AFTER-WORK",
    }
]


def _event(job, event_type, execution_id, before, after, at):
    metadata = {EXECUTION_ID_KEY: execution_id}

    if event_type is JobEventType.EXECUTION_COMPLETED:
        metadata["after_work_evidence"] = list(_AFTER_WORK_EVIDENCE)

    return make_event(
        job["job_id"],
        event_type,
        WORKER,
        occurred_at=at,
        optimization_run_id=proposal_run_id_of(job),
        before_state=before,
        after_state=after,
        metadata=metadata,
    )


def execution_plan(
    job_id,
    target,
    *,
    token_kind="default",
    token_id=EXECUTION_ID,
    block_execution_id=EXECUTION_ID,
    event_specs="default",
    block_edit=None,
):
    """A hand-shaped execution plan with every piece independently corruptible.

    target: 'in_progress' (start), 'completed' or 'reported' (release).
    token_kind: 'default' picks the correct kind for target; None omits it.
    event_specs: 'default' emits the one correct event; otherwise a list of
    (event_type, execution_id) pairs to emit instead.
    """

    default_kind = {
        "in_progress": K.START,
        "completed": K.COMPLETE,
        "reported": K.NOT_COMPLETED,
    }[target]

    def plan(rows):
        job = rows[job_id]
        at = event_timestamp()
        base = replace(JobMutation.unchanged(job), updated_at=at)

        if target == "in_progress":
            block = copy.deepcopy(job["block_candidate"])
            block["metadata"][EXECUTION_ID_KEY] = block_execution_id
            mutation = replace(base, status=target, block_candidate=block)
        elif target == "completed":
            mutation = replace(base, status=target)
        else:
            mutation = _withdrawn(base, job, "work could not be completed")

        if block_edit is not None:
            mutation = replace(
                mutation, block_candidate=block_edit(copy.deepcopy(mutation.block_candidate))
            )

        kind = default_kind if token_kind == "default" else token_kind
        if kind is not None:
            mutation = replace(
                mutation, execution=ExecutionTransition(kind, token_id)
            )

        before = JobStateSnapshot.from_job(job)
        after = JobStateSnapshot.from_job(mutation.as_job(job))

        specs = (
            [(EXECUTION_EVENT_TYPES[default_kind], token_id)]
            if event_specs == "default"
            else event_specs
        )
        events = [
            _event(job, event_type, exec_id, before, after, at)
            for event_type, exec_id in specs
        ]
        return [mutation], events

    return plan


def start(service, job_id, execution_id=EXECUTION_ID) -> dict:
    return service.repository.mutate_jobs(
        [job_id],
        execution_plan(
            job_id,
            "in_progress",
            token_id=execution_id,
            block_execution_id=execution_id,
        ),
    )[job_id]


def in_progress_job(service, optimizer, **kwargs) -> dict:
    job = commit_job(service, optimizer, **kwargs)
    started = start(service, job["job_id"])
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
# 1. in_progress is a real committed status
# ----------------------------------------------------------------------


def test_in_progress_is_a_committed_non_terminal_status():
    assert JobStatus.IN_PROGRESS.value == "in_progress"
    assert "in_progress" in COMMITTED_STATUSES
    assert "notified" in COMMITTED_STATUSES
    assert TERMINAL_STATUSES == ("completed",)

    consistent_block = {
        "status": "COMMITTED",
        "is_committed": True,
        "metadata": {EXECUTION_ID_KEY: EXECUTION_ID},
    }
    assert committed_state_problems("in_progress", consistent_block, (60, 120)) == []
    assert committed_state_problems(
        "in_progress", {**consistent_block, "status": "SCHEDULED"}, (60, 120)
    )


def test_started_job_keeps_its_commitment_placement_and_run(service, optimizer):
    job = commit_job(service, optimizer)
    started = start(service, job["job_id"])

    assert started["status"] == "in_progress"
    assert started["block_candidate"]["status"] == "COMMITTED"
    assert started["block_candidate"]["is_committed"] is True
    assert (started["schedule_start_minute"], started["schedule_end_minute"]) == (
        job["schedule_start_minute"],
        job["schedule_end_minute"],
    )
    assert proposal_run_id_of(started) == proposal_run_id_of(job)
    assert started["block_candidate"]["metadata"][EXECUTION_ID_KEY] == EXECUTION_ID
    assert event_types(service, job["job_id"])[-1] == "EXECUTION_STARTED"


# ----------------------------------------------------------------------
# 2-4. Each execution transition requires its own token
# ----------------------------------------------------------------------


@pytest.mark.parametrize("token_kind", [None, K.COMPLETE, K.NOT_COMPLETED])
def test_notified_to_in_progress_requires_a_start_token(
    service, optimizer, db_path, token_kind
):
    job = commit_job(service, optimizer)
    assert_refused_atomically(
        service,
        db_path,
        job["job_id"],
        execution_plan(job["job_id"], "in_progress", token_kind=token_kind, event_specs=[]),
        ExecutionTokenError,
    )


def test_start_token_cannot_start_a_job_that_is_not_notified(
    service, optimizer, db_path
):
    job = report(service)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    assert service.repository.get(job["job_id"])["status"] == "scheduled"

    def committed_block(block):
        block["status"] = "COMMITTED"
        block["is_committed"] = True
        return block

    assert_refused_atomically(
        service,
        db_path,
        job["job_id"],
        execution_plan(job["job_id"], "in_progress", block_edit=committed_block),
        ExecutionTokenError,
    )


@pytest.mark.parametrize("token_kind", [None, K.START, K.NOT_COMPLETED])
def test_in_progress_to_completed_requires_a_complete_token(
    service, optimizer, db_path, token_kind
):
    job = in_progress_job(service, optimizer)
    assert_refused_atomically(
        service,
        db_path,
        job["job_id"],
        execution_plan(job["job_id"], "completed", token_kind=token_kind, event_specs=[]),
        ExecutionTokenError,
    )


def test_in_progress_completes_with_a_complete_token(service, optimizer):
    job = in_progress_job(service, optimizer)
    done = service.repository.mutate_jobs(
        [job["job_id"]], execution_plan(job["job_id"], "completed")
    )[job["job_id"]]

    assert done["status"] == "completed"
    assert done["block_candidate"]["status"] == "COMMITTED"
    assert event_types(service, job["job_id"])[-1] == "EXECUTION_COMPLETED"


@pytest.mark.parametrize("token_kind", [None, K.START, K.COMPLETE])
def test_in_progress_to_reported_requires_a_not_completed_token(
    service, optimizer, db_path, token_kind
):
    job = in_progress_job(service, optimizer)
    assert_refused_atomically(
        service,
        db_path,
        job["job_id"],
        execution_plan(job["job_id"], "reported", token_kind=token_kind, event_specs=[]),
        ExecutionTokenError,
    )


def test_not_completed_token_releases_the_commitment(service, optimizer):
    job = in_progress_job(service, optimizer)
    released = service.repository.mutate_jobs(
        [job["job_id"]], execution_plan(job["job_id"], "reported")
    )[job["job_id"]]

    assert released["status"] == "reported"
    assert released["schedule_start_minute"] is None
    assert released["schedule_end_minute"] is None
    assert released["block_candidate"]["status"] == "PLANNED"
    assert released["block_candidate"]["is_committed"] is False
    for key in ("committed_start_minute", "committed_end_minute", "proposal_run_id"):
        assert key not in released["block_candidate"]["metadata"]
    assert event_types(service, job["job_id"])[-1] == "EXECUTION_NOT_COMPLETED"


def test_release_with_a_token_must_still_fully_uncommit_the_block(
    service, optimizer, db_path
):
    job = in_progress_job(service, optimizer)

    def still_committed(block):
        block["status"] = "COMMITTED"
        block["is_committed"] = True
        return block

    assert_refused_atomically(
        service,
        db_path,
        job["job_id"],
        execution_plan(job["job_id"], "reported", block_edit=still_committed),
        CommittedJobError,
    )


def test_token_on_a_non_execution_transition_is_misuse(service, optimizer, db_path):
    job = commit_job(service, optimizer)

    def legacy_complete_with_token(rows):
        current = rows[job["job_id"]]
        mutation = replace(
            JobMutation.unchanged(current),
            status="completed",
            execution=ExecutionTransition(K.COMPLETE, EXECUTION_ID),
        )
        return [mutation], []

    assert_refused_atomically(
        service, db_path, job["job_id"], legacy_complete_with_token, ExecutionTokenError
    )


# ----------------------------------------------------------------------
# 5-8. Token / event cross-check
# ----------------------------------------------------------------------


def test_token_without_its_event_is_rolled_back(service, optimizer, db_path):
    job = commit_job(service, optimizer)
    assert_refused_atomically(
        service,
        db_path,
        job["job_id"],
        execution_plan(job["job_id"], "in_progress", event_specs=[]),
        ExecutionTokenError,
    )
    assert service.repository.get(job["job_id"])["status"] == "notified"


def test_token_with_the_wrong_event_type_is_refused(service, optimizer, db_path):
    job = commit_job(service, optimizer)
    assert_refused_atomically(
        service,
        db_path,
        job["job_id"],
        execution_plan(
            job["job_id"],
            "in_progress",
            event_specs=[(E.EXECUTION_COMPLETED, EXECUTION_ID)],
        ),
        ExecutionTokenError,
    )


def test_event_naming_a_different_execution_is_refused(service, optimizer, db_path):
    job = commit_job(service, optimizer)
    assert_refused_atomically(
        service,
        db_path,
        job["job_id"],
        execution_plan(
            job["job_id"],
            "in_progress",
            event_specs=[(E.EXECUTION_STARTED, "EXE-SOMETHING-ELSE")],
        ),
        ExecutionTokenError,
    )


def test_token_naming_a_different_execution_than_the_block_is_refused(
    service, optimizer, db_path
):
    job = commit_job(service, optimizer)
    assert_refused_atomically(
        service,
        db_path,
        job["job_id"],
        execution_plan(
            job["job_id"], "in_progress", block_execution_id="EXE-BLOCK-SAYS-THIS"
        ),
        ExecutionTokenError,
    )

    started = in_progress_job(service, optimizer, track_id="DOWN-1", distance_start=2000.0)
    assert_refused_atomically(
        service,
        db_path,
        started["job_id"],
        execution_plan(started["job_id"], "completed", token_id="EXE-STALE-0000"),
        ExecutionTokenError,
    )


def test_more_than_one_matching_event_is_refused(service, optimizer, db_path):
    job = commit_job(service, optimizer)
    assert_refused_atomically(
        service,
        db_path,
        job["job_id"],
        execution_plan(
            job["job_id"],
            "in_progress",
            event_specs=[
                (E.EXECUTION_STARTED, EXECUTION_ID),
                (E.EXECUTION_STARTED, EXECUTION_ID),
            ],
        ),
        ExecutionTokenError,
    )


def test_execution_event_without_a_token_is_refused(service, optimizer, db_path):
    job = commit_job(service, optimizer)

    def history_claims_a_start(rows):
        current = rows[job["job_id"]]
        state = JobStateSnapshot.from_job(current)
        event = _event(current, E.EXECUTION_STARTED, EXECUTION_ID, state, state, event_timestamp())
        return [JobMutation.unchanged(current)], [event]

    assert_refused_atomically(
        service, db_path, job["job_id"], history_claims_a_start, ExecutionTokenError
    )


# ----------------------------------------------------------------------
# 9-10. History-less primitives cannot start, complete or release
# ----------------------------------------------------------------------


def test_update_status_cannot_complete_an_in_progress_job(service, optimizer, db_path):
    job = in_progress_job(service, optimizer)
    row_before = raw_row(db_path, job["job_id"])

    with pytest.raises(ExecutionTokenError):
        service.repository.update_status(job["job_id"], "completed")

    assert raw_row(db_path, job["job_id"]) == row_before


@pytest.mark.parametrize("committed_status", ["notified", "in_progress"])
def test_update_status_cannot_release_a_committed_job_to_reported(
    service, optimizer, db_path, committed_status
):
    job = (
        commit_job(service, optimizer)
        if committed_status == "notified"
        else in_progress_job(service, optimizer)
    )
    row_before = raw_row(db_path, job["job_id"])

    # in_progress -> reported is a real graph edge, so the refusal must come
    # from the missing execution token, not from an accident of the block.
    expected = ExecutionTokenError if committed_status == "in_progress" else CommittedJobError

    for kwargs in ({}, {"block_status": "PLANNED", "is_committed": False}):
        with pytest.raises(expected):
            service.repository.update_status(job["job_id"], "reported", **kwargs)

    assert raw_row(db_path, job["job_id"]) == row_before


def test_a_perfectly_released_block_is_still_refused_without_a_token(
    service, optimizer
):
    """No status-pair allowance exists: only the token permits the release."""

    job = in_progress_job(service, optimizer)
    released = _withdrawn(JobMutation.unchanged(job), job, "x")

    with pytest.raises(ExecutionTokenError):
        protect_committed_and_terminal_state(
            job,
            "reported",
            released.block_candidate,
            (None, None),
        )

    protect_committed_and_terminal_state(
        job,
        "reported",
        released.block_candidate,
        (None, None),
        execution=ExecutionTransition(K.NOT_COMPLETED, EXECUTION_ID),
    )


def test_update_status_cannot_start_execution(service, optimizer, db_path):
    job = commit_job(service, optimizer)
    row_before = raw_row(db_path, job["job_id"])

    with pytest.raises(CommittedJobError):
        service.repository.update_status(job["job_id"], "in_progress")

    assert raw_row(db_path, job["job_id"]) == row_before


# ----------------------------------------------------------------------
# 11. Terminal before token
# ----------------------------------------------------------------------


def test_terminal_check_precedes_token_interpretation(service, optimizer, db_path):
    job = in_progress_job(service, optimizer)
    done = service.repository.mutate_jobs(
        [job["job_id"]], execution_plan(job["job_id"], "completed")
    )[job["job_id"]]
    assert done["status"] == "completed"

    for kind in K:
        with pytest.raises(TerminalJobError):
            protect_committed_and_terminal_state(
                done,
                "reported",
                done["block_candidate"],
                (None, None),
                execution=ExecutionTransition(kind, EXECUTION_ID),
            )

    assert_refused_atomically(
        service,
        db_path,
        job["job_id"],
        execution_plan(job["job_id"], "reported"),
        TerminalJobError,
    )


# ----------------------------------------------------------------------
# 12-13. Execution integrity findings
# ----------------------------------------------------------------------


def test_in_progress_without_execution_id_is_an_integrity_violation(
    service, optimizer, db_path
):
    job = in_progress_job(service, optimizer)
    job_id = job["job_id"]
    corrupt_block_metadata(db_path, job_id, drop=[EXECUTION_ID_KEY])
    row_before = raw_row(db_path, job_id)

    attempts = [
        lambda: optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER),
        lambda: service.repository.update_status(job_id, "completed"),
        lambda: service.repository.mutate_jobs(
            [job_id], execution_plan(job_id, "completed")
        ),
        lambda: service.repository.mutate_jobs(
            [job_id], execution_plan(job_id, "reported")
        ),
    ]

    for attempt in attempts:
        with pytest.raises(CommittedStateIntegrityError):
            attempt()

    assert raw_row(db_path, job_id) == row_before


def test_notified_carrying_execution_id_is_an_integrity_violation(
    service, optimizer, db_path
):
    job = commit_job(service, optimizer)
    job_id = job["job_id"]
    corrupt_block_metadata(db_path, job_id, set_={EXECUTION_ID_KEY: EXECUTION_ID})
    row_before = raw_row(db_path, job_id)

    attempts = [
        lambda: optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER),
        lambda: service.repository.update_status(job_id, "completed"),
        lambda: service.repository.mutate_jobs(
            [job_id], execution_plan(job_id, "in_progress")
        ),
    ]

    for attempt in attempts:
        with pytest.raises(CommittedStateIntegrityError):
            attempt()

    assert raw_row(db_path, job_id) == row_before


def test_no_write_can_produce_an_in_progress_job_without_execution_id(
    service, optimizer, db_path
):
    job = commit_job(service, optimizer)
    assert_refused_atomically(
        service,
        db_path,
        job["job_id"],
        execution_plan(job["job_id"], "in_progress", block_execution_id=None),
        CommittedJobError,
    )


# ----------------------------------------------------------------------
# 14. Optimization pins in_progress exactly like notified
# ----------------------------------------------------------------------


def test_optimization_pins_in_progress_exactly_like_notified(service, optimizer):
    notified = commit_job(service, optimizer)
    started = in_progress_job(service, optimizer, track_id="DOWN-1", distance_start=2000.0)
    assert service.repository.get(notified["job_id"])["status"] == "notified"

    _, committed = service.classify_for_optimization()
    assert {notified["job_id"], started["job_id"]} <= {b.block_id for b in committed}

    report(service, track_id="UP-1", distance_start=5000.0)
    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    pinned = {entry["job_id"]: entry for entry in outcome["scheduled"]}

    for before in (notified, started):
        job_id = before["job_id"]
        after = service.repository.get(job_id)

        assert after["status"] == before["status"]
        assert after["block_candidate"]["status"] == "COMMITTED"
        assert after["block_candidate"]["is_committed"] is True
        assert (after["schedule_start_minute"], after["schedule_end_minute"]) == (
            before["schedule_start_minute"],
            before["schedule_end_minute"],
        )
        assert pinned[job_id]["is_committed"] is True
        assert (pinned[job_id]["start_minute"], pinned[job_id]["end_minute"]) == (
            before["schedule_start_minute"],
            before["schedule_end_minute"],
        )
        assert event_types(service, job_id)[-1] == "COMMITTED_BLOCK_PRESERVED"

    assert (
        service.repository.get(started["job_id"])["block_candidate"]["metadata"][
            EXECUTION_ID_KEY
        ]
        == EXECUTION_ID
    )


def test_refusing_reoptimization_keeps_in_progress_work_committed(
    service, optimizer, monkeypatch
):
    started = in_progress_job(service, optimizer)
    report(service, track_id="DOWN-1", distance_start=2000.0)

    original = service.possession_inputs

    def without_up_1(*args, **kwargs):
        inputs = original(*args, **kwargs)
        return replace(inputs, windows=[w for w in inputs.windows if w.track_id != "UP-1"])

    monkeypatch.setattr(service, "possession_inputs", without_up_1)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    after = service.repository.get(started["job_id"])
    assert after["status"] == "in_progress"
    assert after["block_candidate"]["status"] == "COMMITTED"
    assert after["schedule_start_minute"] == started["schedule_start_minute"]
    assert event_types(service, started["job_id"])[-1] == "COMMITTED_BLOCK_CONFLICT"


# ----------------------------------------------------------------------
# 15. Committed placement cannot change while in_progress
# ----------------------------------------------------------------------


def test_in_progress_placement_cannot_change_through_any_write_path(
    service, optimizer, db_path
):
    job = in_progress_job(service, optimizer)
    job_id = job["job_id"]
    row_before = raw_row(db_path, job_id)

    with pytest.raises(CommittedJobError):
        service.set_schedule(job_id, 600, 720, actor=ENGINEER)

    with pytest.raises(CommittedJobError):
        service.repository.update_schedule(job_id, 600, 720, updated_at="now")

    for status in ("scheduled", "notified"):
        with pytest.raises(CommittedJobError):
            service.repository.update_status(job_id, status)

    with pytest.raises(CommittedJobError):
        service.repository.update_status(job_id, "in_progress", block_status="SCHEDULED")

    with pytest.raises(CommittedJobError):
        service.repository.apply_optimization_outcome(
            scheduled=[{"job_id": job_id, "start_minute": 600, "end_minute": 720}],
            refused=[],
            updated_at="now",
            solver_status="OPTIMAL",
        )

    assert raw_row(db_path, job_id) == row_before


@pytest.mark.parametrize(
    "key, value",
    [
        ("committed_start_minute", 1),
        ("committed_end_minute", 2),
        ("proposal_run_id", "RUN-FORGED"),
        (EXECUTION_ID_KEY, "EXE-SWAPPED"),
    ],
)
def test_in_progress_block_metadata_cannot_change(
    service, optimizer, db_path, key, value
):
    job = in_progress_job(service, optimizer)

    def edit_metadata(rows):
        current = rows[job["job_id"]]
        block = copy.deepcopy(current["block_candidate"])
        block["metadata"][key] = value
        return [replace(JobMutation.unchanged(current), block_candidate=block)], []

    assert_refused_atomically(
        service, db_path, job["job_id"], edit_metadata, CommittedJobError
    )


# ----------------------------------------------------------------------
# 17. Step 4: the legacy notified -> completed completion is closed
# ----------------------------------------------------------------------


def test_legacy_notified_completion_is_refused_on_every_write_path(
    service, optimizer, db_path
):
    job = commit_job(service, optimizer)
    job_id = job["job_id"]
    row_before = raw_row(db_path, job_id)
    events_before = count_events(db_path)

    assert not hasattr(service, "complete")

    with pytest.raises(ExecutionTokenError):
        service.repository.update_status(job_id, "completed")

    # No token, no event: the old plan_completion shape.
    assert_refused_atomically(
        service,
        db_path,
        job_id,
        execution_plan(job_id, "completed", token_kind=None, event_specs=[]),
        ExecutionTokenError,
    )

    # Even a well-formed COMPLETE token cannot complete work that never started.
    assert_refused_atomically(
        service, db_path, job_id, execution_plan(job_id, "completed"), ExecutionTokenError
    )

    assert raw_row(db_path, job_id) == row_before
    assert count_events(db_path) == events_before
    assert "JOB_COMPLETED" not in event_types(service, job_id)


def test_job_mutation_defaults_to_no_execution_token(service):
    job = report(service)
    assert JobMutation.unchanged(service.repository.get(job["job_id"])).execution is None

    with pytest.raises(ValueError):
        ExecutionTransition(K.START, "  ")
