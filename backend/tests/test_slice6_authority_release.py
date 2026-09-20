"""Sprint 3 Slice 6: authority release of an approved (committed) block.

THE HOLE THIS CLOSES
    Approval commits a block. Before Slice 6 the only way out of
    'notified' was START, into execution: reject and postpone act on a
    'scheduled', UNCOMMITTED proposal, and EXECUTION_NOT_COMPLETED
    requires an execution that actually started. A committed block whose
    possession was never granted, whose crew or safety clearance fell
    through, or which the authority cancelled before START, had no valid
    path back.

WHAT IS PINNED HERE, against the real service, repository, lifecycle
guard and HTTP API:

  GATE      release works from 'notified' and from nowhere else, and it
            is permitted ONLY by an AuthorityRelease token on the
            JobMutation - so the history-less primitives, which build no
            mutation, structurally cannot release a block.
  IDENTITY  a release names the exact run it withdraws; a stale one
            changes nothing and records nothing but the refusal.
  RELEASE   status, placement, block, metadata and history after a
            release, including that the ORIGINAL commitment survives in
            history and that nothing is optimized or re-proposed.
  DISTINCT  release, reject, postpone and not-completed stay four
            different operations with four different events.
  F1/F3/F4  the three bounded Slice 5 audit findings folded into this
            slice.

Every test uses an isolated temporary database.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.jobs.lifecycle as lifecycle
from contracts import DEFAULT_HORIZON_START

from backend.app.identity.actor import ActorRole, human_actor, unidentified_actor
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.jobs.events import (
    JobEventType,
    JobStateSnapshot,
    event_timestamp,
    make_event,
)
from backend.app.jobs.execution import EXECUTION_ID_KEY
from backend.app.jobs.lifecycle import (
    COMMITTED_START_KEY,
    PROPOSAL_RUN_ID_KEY,
    RELEASED_RUN_ID_KEY,
    AuthorityRelease,
    CommittedJobError,
    ExecutionTokenError,
    InvalidTransitionError,
    JobMutation,
    ReleaseTokenError,
    StaleProposalError,
    TerminalJobError,
    plan_release,
    proposal_run_id_of,
    protect_committed_and_terminal_state,
    validate_execution_events,
    validate_release_events,
)
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService
from backend.tests.execution_helpers import (
    complete_execution_body,
    start_execution_body,
)


WORKER = human_actor("WORKER-042", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-003", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-017", ActorRole.AUTHORITY)

CORRIDOR = "CORRIDOR_A"
E = JobEventType

REASON = "possession not granted by section controller"

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


def release(service, job, actor=AUTHORITY, reason=REASON, run_id=None) -> dict:
    return service.release_committed_block(
        job["job_id"],
        actor=actor,
        expected_proposal_run_id=run_id or proposal_run_id_of(job),
        reason=reason,
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


def count_runs(db_path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM optimization_runs").fetchone()[0]


def event_types(service, job_id) -> list:
    return [item.event.event_type for item in service.history.list_for_job(job_id)]


def events_of(service, job_id, event_type) -> list:
    return [
        item.event
        for item in service.history.list_for_job(job_id)
        if item.event.event_type is event_type
    ]


def assert_untouched(service, db_path, job_id, row_before, events_before, runs_before):
    """The row, the run table and every event but a recorded refusal are unchanged."""

    assert raw_row(db_path, job_id) == row_before
    assert count_runs(db_path) == runs_before
    assert count_events(db_path) in (events_before, events_before + 1)
    assert E.BLOCK_RELEASED not in event_types(service, job_id)


# ----------------------------------------------------------------------
# 1, 7. A release succeeds from notified and leaves exactly the right state
# ----------------------------------------------------------------------


def test_notified_job_is_released_back_to_reported(service, optimizer, db_path):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    run_id = proposal_run_id_of(job)
    committed_start = job["schedule_start_minute"]
    committed_end = job["schedule_end_minute"]
    runs_before = count_runs(db_path)

    released = release(service, job)

    # Status and placement.
    assert released["status"] == "reported"
    assert released["schedule_start_minute"] is None
    assert released["schedule_end_minute"] is None

    # The block is planned and no longer committed.
    block = released["block_candidate"]
    assert block["status"] == "PLANNED"
    assert block["is_committed"] is False

    # Current placement metadata, the proposal run and (defensively) any
    # execution id are all gone.
    metadata = block["metadata"]
    assert PROPOSAL_RUN_ID_KEY not in metadata
    assert COMMITTED_START_KEY not in metadata
    assert "committed_end_minute" not in metadata
    assert EXECUTION_ID_KEY not in metadata

    # The reason is preserved, exactly as a rejection's is.
    assert released["last_refusal_reason"] == REASON

    # The event.
    (event,) = events_of(service, job_id, E.BLOCK_RELEASED)
    assert event.reason == REASON
    assert event.actor.actor_id == AUTHORITY.actor_id
    assert event.optimization_run_id == run_id
    assert event.metadata[RELEASED_RUN_ID_KEY] == run_id
    assert event.metadata["released_start_minute"] == committed_start
    assert event.metadata["released_end_minute"] == committed_end
    assert event.metadata["transition"] == "release"
    assert event.before_state.status == "notified"
    assert event.before_state.is_committed is True
    assert event.after_state.status == "reported"
    assert event.after_state.is_committed is False
    assert event.after_state.proposal_run_id is None

    # 8. Releasing optimizes nothing: no new run, no new proposal.
    assert count_runs(db_path) == runs_before
    assert proposal_run_id_of(released) is None
    assert E.BLOCK_PROPOSED not in event_types(service, job_id)[
        event_types(service, job_id).index(E.BLOCK_RELEASED) :
    ]


def test_released_history_reconstructs_the_whole_commitment(service, optimizer):
    """The original block survives in history; nothing is overwritten."""

    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    run_id = proposal_run_id_of(job)

    release(service, job)

    types = event_types(service, job_id)
    assert types[0] is E.JOB_CREATED
    assert types[1] is E.JOB_SCORED
    assert E.BLOCK_PROPOSED in types
    assert E.BLOCK_COMMITTED in types
    assert types[-1] is E.BLOCK_RELEASED

    assert types.index(E.BLOCK_PROPOSED) < types.index(E.BLOCK_COMMITTED)
    assert types.index(E.BLOCK_COMMITTED) < types.index(E.BLOCK_RELEASED)

    # The committed placement is still readable from the original events.
    (proposed,) = events_of(service, job_id, E.BLOCK_PROPOSED)
    (committed,) = events_of(service, job_id, E.BLOCK_COMMITTED)
    assert proposed.optimization_run_id == run_id
    assert committed.optimization_run_id == run_id
    assert committed.metadata["start_minute"] == job["schedule_start_minute"]
    assert committed.metadata["end_minute"] == job["schedule_end_minute"]
    assert committed.after_state.is_committed is True


def test_release_raises_the_not_before_without_resetting_it(service, optimizer):
    """The released window is not simply offered back, and a prior floor holds."""

    job = notified_job(service, optimizer)
    released_end = job["schedule_end_minute"]
    stored_earliest = int(job["block_candidate"]["earliest_start_minute"])

    released = release(service, job)
    block = released["block_candidate"]

    assert block["earliest_start_minute"] == max(stored_earliest, released_end)
    # Slice 4 widening, shared with plan_postpone: widen, never shrink.
    assert block["latest_end_minute"] >= service.horizon_minutes

    (event,) = events_of(service, job["job_id"], E.BLOCK_RELEASED)
    assert event.metadata["previous_earliest_start_minute"] == stored_earliest
    assert event.metadata["not_before_minute"] == block["earliest_start_minute"]


def test_release_never_lowers_a_prior_postponement(service, optimizer, db_path):
    """never_lower=True: a postponed job's floor survives a later release."""

    job = report(service)
    job_id = job["job_id"]
    optimize(optimizer)

    # Raise the floor by hand to a value beyond any placement this
    # corridor produces, then commit and release; the floor must hold.
    stored = service.repository.get(job_id)
    high_floor = stored["schedule_end_minute"] + 5000

    with closing(sqlite3.connect(db_path)) as conn:
        import json

        block = stored["block_candidate"]
        block["earliest_start_minute"] = high_floor
        conn.execute(
            "UPDATE maintenance_jobs SET block_candidate_json = ? WHERE job_id = ?",
            (json.dumps(block), job_id),
        )
        conn.commit()

    committed = approve(service, job_id)
    released = release(service, committed)

    assert released["block_candidate"]["earliest_start_minute"] == high_floor


# ----------------------------------------------------------------------
# 6, 12, 13, 19. Release works from 'notified' and from nowhere else
# ----------------------------------------------------------------------


def test_release_is_refused_from_reported(service, optimizer, db_path):
    job = report(service)
    job_id = job["job_id"]
    before = (raw_row(db_path, job_id), count_events(db_path), count_runs(db_path))

    with pytest.raises(InvalidTransitionError):
        service.release_committed_block(
            job_id,
            actor=AUTHORITY,
            expected_proposal_run_id="RUN-ANY",
            reason=REASON,
        )

    assert_untouched(service, db_path, job_id, *before)


def test_release_is_refused_from_scheduled(service, optimizer, db_path):
    """A scheduled proposal is rejected or postponed, never released."""

    job = report(service)
    job_id = job["job_id"]
    optimize(optimizer)
    scheduled = service.repository.get(job_id)
    assert scheduled["status"] == "scheduled"

    before = (raw_row(db_path, job_id), count_events(db_path), count_runs(db_path))

    with pytest.raises(InvalidTransitionError):
        release(service, scheduled)

    assert_untouched(service, db_path, job_id, *before)
    assert service.repository.get(job_id)["status"] == "scheduled"
    assert event_types(service, job_id)[-1] is E.TRANSITION_REJECTED


def test_release_is_refused_after_execution_starts(service, optimizer, db_path):
    """12, 13. Once in_progress the correct operation is NOT_COMPLETED."""

    job = notified_job(service, optimizer)
    job_id = job["job_id"]

    started, execution = service.start_execution(
        job_id, actor=WORKER, **start_execution_body(job)
    )
    assert started["status"] == "in_progress"

    before = (raw_row(db_path, job_id), count_events(db_path), count_runs(db_path))

    with pytest.raises(InvalidTransitionError):
        release(service, started)

    assert_untouched(service, db_path, job_id, *before)
    assert service.repository.get(job_id)["status"] == "in_progress"

    # The post-start failure path still works and is a different event.
    not_completed, _ = service.report_execution_not_completed(
        job_id,
        actor=WORKER,
        execution_id=execution.execution_id,
        actual_end_at=complete_execution_body(job, execution.execution_id)[
            "actual_end_at"
        ],
        reason="track machine failure",
    )

    assert not_completed["status"] == "reported"
    types = event_types(service, job_id)
    assert E.EXECUTION_NOT_COMPLETED in types
    assert E.BLOCK_RELEASED not in types


def test_release_is_refused_on_a_completed_job(service, optimizer, db_path):
    """19. completed stays terminal; release is not a way back out of it."""

    job = notified_job(service, optimizer)
    job_id = job["job_id"]

    _, execution = service.start_execution(
        job_id, actor=WORKER, **start_execution_body(job)
    )
    completed, _ = service.complete_execution(
        job_id, actor=WORKER, **complete_execution_body(job, execution.execution_id)
    )
    assert completed["status"] == "completed"

    before = (raw_row(db_path, job_id), count_events(db_path), count_runs(db_path))

    with pytest.raises(InvalidTransitionError):
        release(service, job)

    assert raw_row(db_path, job_id) == before[0]
    assert service.repository.get(job_id)["status"] == "completed"
    assert E.BLOCK_RELEASED not in event_types(service, job_id)


# ----------------------------------------------------------------------
# 2. A release needs an identified human
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "actor",
    [
        pytest.param(None, id="unidentified"),
        pytest.param(unidentified_actor(), id="explicit-unidentified"),
    ],
)
def test_release_requires_an_identified_human_actor(
    service, optimizer, db_path, actor
):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    before = (raw_row(db_path, job_id), count_events(db_path), count_runs(db_path))

    with pytest.raises(InvalidTransitionError):
        service.release_committed_block(
            job_id,
            actor=actor,
            expected_proposal_run_id=proposal_run_id_of(job),
            reason=REASON,
        )

    assert_untouched(service, db_path, job_id, *before)
    assert service.repository.get(job_id)["status"] == "notified"


# ----------------------------------------------------------------------
# 3. Authorization: its own action, checked exactly once, before anything
# ----------------------------------------------------------------------


class DenyPolicy:
    enforcing = True

    def __init__(self, *denied):
        self.denied = set(denied)

    def authorize(self, actor, action):
        if action in self.denied:
            raise AuthorizationDenied(actor, action)


class RecordingPolicy:
    enforcing = False

    def __init__(self):
        self.calls = []

    def authorize(self, actor, action):
        self.calls.append(action)


def test_release_has_its_own_authorization_action(service, optimizer):
    job = notified_job(service, optimizer)
    policy = RecordingPolicy()
    service.authorization = policy

    release(service, job)

    assert policy.calls == [JobAction.RELEASE_COMMITTED_BLOCK]
    assert JobAction.REJECT_PROPOSAL not in policy.calls
    assert JobAction.COMMIT_BLOCK not in policy.calls


def test_release_denial_changes_nothing(service, optimizer, db_path):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    row_before = raw_row(db_path, job_id)
    events_before = count_events(db_path)

    service.authorization = DenyPolicy(JobAction.RELEASE_COMMITTED_BLOCK)

    with pytest.raises(AuthorizationDenied):
        release(service, job)

    # Denied before anything is read or written: not even a refusal event.
    assert raw_row(db_path, job_id) == row_before
    assert count_events(db_path) == events_before
    assert service.repository.get(job_id)["status"] == "notified"


def test_denying_reject_does_not_deny_release(service, optimizer):
    """Release is not REJECT_PROPOSAL wearing a different name."""

    job = notified_job(service, optimizer)
    service.authorization = DenyPolicy(
        JobAction.REJECT_PROPOSAL, JobAction.POSTPONE_PROPOSAL
    )

    released = release(service, job)

    assert released["status"] == "reported"


# ----------------------------------------------------------------------
# 4, 5. Proposal generation identity and stale safety
# ----------------------------------------------------------------------


@pytest.mark.parametrize("run_id", ["", "   ", None])
def test_release_requires_an_expected_run_id(service, optimizer, db_path, run_id):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    before = (raw_row(db_path, job_id), count_events(db_path), count_runs(db_path))

    with pytest.raises(ValueError):
        service.release_committed_block(
            job_id,
            actor=AUTHORITY,
            expected_proposal_run_id=run_id,
            reason=REASON,
        )

    assert_untouched(service, db_path, job_id, *before)


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_release_requires_a_reason(service, optimizer, db_path, reason):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    before = (raw_row(db_path, job_id), count_events(db_path), count_runs(db_path))

    with pytest.raises(ValueError):
        service.release_committed_block(
            job_id,
            actor=AUTHORITY,
            expected_proposal_run_id=proposal_run_id_of(job),
            reason=reason,
        )

    assert_untouched(service, db_path, job_id, *before)


def test_release_naming_the_wrong_run_is_refused(service, optimizer, db_path):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    before = (raw_row(db_path, job_id), count_events(db_path), count_runs(db_path))

    with pytest.raises(StaleProposalError):
        release(service, job, run_id="RUN-SOMETHING-ELSE")

    assert_untouched(service, db_path, job_id, *before)
    assert event_types(service, job_id)[-1] is E.TRANSITION_REJECTED


def test_a_stale_release_after_reoptimization_is_refused(service, optimizer, db_path):
    """R1 -> P1 -> NOTIFIED, reoptimize, then RELEASE(P1) must fail closed.

    A notified job's committed block is preserved by re-optimization (it
    keeps run R1), so the strongest available stale case is a release that
    names an earlier run while the row names a later one. Built by
    releasing, re-optimizing into R2, re-approving, then replaying R1.
    """

    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    run_1 = proposal_run_id_of(job)

    release(service, job)
    run_2 = optimize(optimizer)
    assert run_2 != run_1

    re_approved = approve(service, job_id)
    assert proposal_run_id_of(re_approved) == run_2

    before = (raw_row(db_path, job_id), count_events(db_path), count_runs(db_path))
    releases_before = len(events_of(service, job_id, E.BLOCK_RELEASED))

    with pytest.raises(StaleProposalError):
        release(service, job, run_id=run_1)

    # Nothing mutated, no second BLOCK_RELEASED, placement and proposal kept.
    assert raw_row(db_path, job_id) == before[0]
    assert count_runs(db_path) == before[2]
    assert len(events_of(service, job_id, E.BLOCK_RELEASED)) == releases_before

    stored = service.repository.get(job_id)
    assert stored["status"] == "notified"
    assert proposal_run_id_of(stored) == run_2
    assert stored["block_candidate"]["is_committed"] is True
    assert event_types(service, job_id)[-1] is E.TRANSITION_REJECTED


def test_an_r1_release_cannot_touch_an_r2_commitment(service, optimizer):
    """The full cross-generation trace, end to end.

    R1 -> P1 -> APPROVE -> NOTIFIED -> RELEASE -> REPORTED ->
    REOPTIMIZE -> R2 -> P2 -> APPROVE -> NOTIFIED -> START ->
    IN_PROGRESS -> COMPLETE -> COMPLETED.
    """

    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    run_1 = proposal_run_id_of(job)
    p1_start = job["schedule_start_minute"]

    released = release(service, job)
    assert released["status"] == "reported"

    # 9, 10. The released job is optimized again and gets a NEW generation.
    run_2 = optimize(optimizer)
    rescheduled = service.repository.get(job_id)
    assert rescheduled["status"] == "scheduled"
    assert run_2 != run_1
    assert proposal_run_id_of(rescheduled) == run_2

    # 16. An R1 approval cannot land on the R2 proposal.
    with pytest.raises(StaleProposalError):
        service.approve_proposal(
            job_id, actor=AUTHORITY, expected_proposal_run_id=run_1
        )

    # 11. Approve R2 and execute normally.
    notified = approve(service, job_id)
    assert proposal_run_id_of(notified) == run_2

    # An R1 start cannot land either.
    with pytest.raises(StaleProposalError):
        service.start_execution(
            job_id,
            actor=WORKER,
            **{**start_execution_body(notified), "expected_proposal_run_id": run_1},
        )

    _, execution = service.start_execution(
        job_id, actor=WORKER, **start_execution_body(notified)
    )
    completed, _ = service.complete_execution(
        job_id,
        actor=WORKER,
        **complete_execution_body(notified, execution.execution_id),
    )

    assert completed["status"] == "completed"

    # Execution is tied to R2, and is the job's first and only attempt.
    assert execution.optimization_run_id == run_2
    assert execution.attempt_number == 1
    (record,) = service.get_execution(job_id, actor=WORKER)
    assert record.optimization_run_id == run_2

    # R1 history is intact and distinct from R2's.
    released_events = events_of(service, job_id, E.BLOCK_RELEASED)
    assert len(released_events) == 1
    assert released_events[0].optimization_run_id == run_1
    assert released_events[0].metadata["released_start_minute"] == p1_start

    committed = events_of(service, job_id, E.BLOCK_COMMITTED)
    assert [event.optimization_run_id for event in committed] == [run_1, run_2]

    types = event_types(service, job_id)
    assert types.index(E.BLOCK_RELEASED) < types.index(E.EXECUTION_STARTED)
    assert types[-1] is E.EXECUTION_COMPLETED


# ----------------------------------------------------------------------
# The token gate: no status pair alone permits a release
# ----------------------------------------------------------------------


def test_update_status_cannot_release_a_notified_job(service, optimizer, db_path):
    """The history-less primitive builds no mutation, so it carries no token."""

    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    row_before = raw_row(db_path, job_id)

    for kwargs in ({}, {"block_status": "PLANNED", "is_committed": False}):
        with pytest.raises(ReleaseTokenError):
            service.repository.update_status(job_id, "reported", **kwargs)

    assert raw_row(db_path, job_id) == row_before
    assert service.repository.get(job_id)["status"] == "notified"


def test_a_perfectly_released_block_is_still_refused_without_a_token(
    service, optimizer
):
    """23. Remove the gate and this passes - which is the point of the test."""

    job = notified_job(service, optimizer)
    released = lifecycle._withdrawn(JobMutation.unchanged(job), job, REASON)

    with pytest.raises(ReleaseTokenError):
        protect_committed_and_terminal_state(
            job, "reported", released.block_candidate, (None, None)
        )

    # With the token naming the stored run, the same state is permitted.
    protect_committed_and_terminal_state(
        job,
        "reported",
        released.block_candidate,
        (None, None),
        release=AuthorityRelease(proposal_run_id_of(job)),
    )


def test_a_release_token_must_name_the_stored_run(service, optimizer):
    job = notified_job(service, optimizer)
    released = lifecycle._withdrawn(JobMutation.unchanged(job), job, REASON)

    with pytest.raises(ReleaseTokenError):
        protect_committed_and_terminal_state(
            job,
            "reported",
            released.block_candidate,
            (None, None),
            release=AuthorityRelease("RUN-FORGED"),
        )


def test_a_release_token_on_any_other_move_is_misuse(service, optimizer):
    """A release token permits notified -> reported and nothing else."""

    job = notified_job(service, optimizer)
    token = AuthorityRelease(proposal_run_id_of(job))
    placement = (job["schedule_start_minute"], job["schedule_end_minute"])

    # The job staying exactly where it is, carrying a release token.
    with pytest.raises(ReleaseTokenError):
        protect_committed_and_terminal_state(
            job, "notified", job["block_candidate"], placement, release=token
        )

    # And an uncommitted job, which has no commitment to release at all.
    scheduled = report(service, distance_start=6000.0)
    optimize(optimizer)
    scheduled = service.repository.get(scheduled["job_id"])
    withdrawn = lifecycle._withdrawn(
        JobMutation.unchanged(scheduled), scheduled, REASON
    )

    with pytest.raises(ReleaseTokenError):
        protect_committed_and_terminal_state(
            scheduled,
            "reported",
            withdrawn.block_candidate,
            (None, None),
            release=AuthorityRelease(proposal_run_id_of(scheduled)),
        )


def test_a_mutation_can_never_carry_both_tokens(service, optimizer):
    job = notified_job(service, optimizer)
    released = lifecycle._withdrawn(JobMutation.unchanged(job), job, REASON)

    with pytest.raises(ReleaseTokenError):
        protect_committed_and_terminal_state(
            job,
            "reported",
            released.block_candidate,
            (None, None),
            release=AuthorityRelease(proposal_run_id_of(job)),
            execution=lifecycle.ExecutionTransition(
                lifecycle.ExecutionTransitionKind.NOT_COMPLETED, f"EXE-{'0' * 32}"
            ),
        )


def test_a_release_must_fully_uncommit_the_block(service, optimizer):
    """A token does not license a half-release."""

    job = notified_job(service, optimizer)
    token = AuthorityRelease(proposal_run_id_of(job))

    with pytest.raises(CommittedJobError):
        protect_committed_and_terminal_state(
            job,
            "reported",
            job["block_candidate"],  # still COMMITTED, metadata intact
            (None, None),
            release=token,
        )


def _release_plan(job_id, *, token=None, event_run_id="default", omit_event=False):
    """A hand-shaped release plan with the token and the event independently forgeable."""

    def plan(rows):
        job = rows[job_id]
        at = event_timestamp(FIXED_NOW)
        run_id = proposal_run_id_of(job)
        mutation = lifecycle._withdrawn(
            replace(JobMutation.unchanged(job), updated_at=at), job, REASON
        )

        if token is not None:
            mutation = replace(mutation, release=token)

        events = []

        if not omit_event:
            named = run_id if event_run_id == "default" else event_run_id
            events.append(
                make_event(
                    job_id,
                    E.BLOCK_RELEASED,
                    AUTHORITY,
                    occurred_at=at,
                    reason=REASON,
                    optimization_run_id=run_id,
                    before_state=JobStateSnapshot.from_job(job),
                    after_state=JobStateSnapshot.from_job(mutation.as_job(job)),
                    metadata={RELEASED_RUN_ID_KEY: named},
                )
            )

        return [mutation], events

    return plan


def test_a_release_event_without_its_token_is_refused(service, optimizer, db_path):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    row_before = raw_row(db_path, job_id)
    events_before = count_events(db_path)

    with pytest.raises(ReleaseTokenError):
        service.repository.mutate_jobs([job_id], _release_plan(job_id))

    assert raw_row(db_path, job_id) == row_before
    assert count_events(db_path) == events_before


def test_a_release_token_without_its_event_is_refused(service, optimizer, db_path):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    row_before = raw_row(db_path, job_id)
    events_before = count_events(db_path)

    with pytest.raises(ReleaseTokenError):
        service.repository.mutate_jobs(
            [job_id],
            _release_plan(
                job_id,
                token=AuthorityRelease(proposal_run_id_of(job)),
                omit_event=True,
            ),
        )

    assert raw_row(db_path, job_id) == row_before
    assert count_events(db_path) == events_before


def test_a_release_event_naming_another_run_than_its_token_is_refused(
    service, optimizer, db_path
):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    row_before = raw_row(db_path, job_id)
    events_before = count_events(db_path)

    with pytest.raises(ReleaseTokenError):
        service.repository.mutate_jobs(
            [job_id],
            _release_plan(
                job_id,
                token=AuthorityRelease(proposal_run_id_of(job)),
                event_run_id="RUN-FORGED",
            ),
        )

    assert raw_row(db_path, job_id) == row_before
    assert count_events(db_path) == events_before


def test_validate_release_events_is_pure_and_symmetric():
    token = AuthorityRelease("RUN-1")
    mutation = JobMutation(
        job_id="J1",
        status="reported",
        schedule_start_minute=None,
        schedule_end_minute=None,
        updated_at=None,
        last_solver_status=None,
        last_refusal_reason=REASON,
        block_candidate={},
        release=token,
    )
    event = make_event(
        "J1",
        E.BLOCK_RELEASED,
        AUTHORITY,
        occurred_at=event_timestamp(FIXED_NOW),
        metadata={RELEASED_RUN_ID_KEY: "RUN-1"},
    )

    validate_release_events([mutation], [event])

    with pytest.raises(ReleaseTokenError):
        validate_release_events([mutation], [event, event])

    with pytest.raises(ReleaseTokenError):
        validate_release_events([], [event])


# ----------------------------------------------------------------------
# 14, 15. Concurrency
# ----------------------------------------------------------------------


def test_two_concurrent_releases_succeed_exactly_once(service, optimizer, db_path):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    run_id = proposal_run_id_of(job)

    successes: list = []
    failures: list = []
    barrier = threading.Barrier(6)

    def attempt(n: int):
        barrier.wait()
        try:
            successes.append(
                service.release_committed_block(
                    job_id,
                    actor=human_actor(f"AUTHORITY-{n:03d}", ActorRole.AUTHORITY),
                    expected_proposal_run_id=run_id,
                    reason=REASON,
                )
            )
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)

    assert len(successes) == 1
    assert len(failures) == 5
    assert all(
        isinstance(exc, (InvalidTransitionError, StaleProposalError))
        for exc in failures
    )
    assert len(events_of(service, job_id, E.BLOCK_RELEASED)) == 1
    assert service.repository.get(job_id)["status"] == "reported"


def test_release_and_start_race_leaves_exactly_one_outcome(service, optimizer):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]

    outcomes: list = []
    failures: list = []
    barrier = threading.Barrier(2)

    def do_release():
        barrier.wait()
        try:
            outcomes.append(("release", release(service, job)))
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)

    def do_start():
        barrier.wait()
        try:
            started, _ = service.start_execution(
                job_id, actor=WORKER, **start_execution_body(job)
            )
            outcomes.append(("start", started))
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)

    threads = [threading.Thread(target=do_release), threading.Thread(target=do_start)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)

    assert len(outcomes) == 1
    assert len(failures) == 1

    kind, _ = outcomes[0]
    stored = service.repository.get(job_id)
    types = event_types(service, job_id)

    if kind == "release":
        assert stored["status"] == "reported"
        assert types.count(E.BLOCK_RELEASED) == 1
        assert E.EXECUTION_STARTED not in types
    else:
        assert stored["status"] == "in_progress"
        assert types.count(E.EXECUTION_STARTED) == 1
        assert E.BLOCK_RELEASED not in types


def test_release_races_reoptimization_safely(service, optimizer, db_path):
    """Whichever lands first, the invariants hold and history stays honest."""

    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    for i in range(3):
        report(service, distance_start=4000.0 + 500 * i)

    failures: list = []
    barrier = threading.Barrier(2)

    def do_release():
        barrier.wait()
        try:
            release(service, job)
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)

    def do_optimize():
        barrier.wait()
        try:
            optimize(optimizer)
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)

    threads = [threading.Thread(target=do_release), threading.Thread(target=do_optimize)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)

    stored = service.repository.get(job_id)
    releases = events_of(service, job_id, E.BLOCK_RELEASED)

    assert len(releases) <= 1

    if releases:
        # The release landed. Either the job is still reported, or a later
        # optimization re-proposed it - never a stranded commitment.
        assert stored["status"] in ("reported", "scheduled")
        assert stored["block_candidate"]["is_committed"] is False
    else:
        # The release lost; the commitment is intact and preserved.
        assert stored["status"] == "notified"
        assert stored["block_candidate"]["status"] == "COMMITTED"
        assert stored["schedule_start_minute"] == job["schedule_start_minute"]

    # A reported/notified job never carries a half-state.
    if stored["status"] == "reported":
        assert stored["schedule_start_minute"] is None
        assert PROPOSAL_RUN_ID_KEY not in stored["block_candidate"]["metadata"]


# ----------------------------------------------------------------------
# 16. Release vs the other three review actions
# ----------------------------------------------------------------------


def test_reject_and_postpone_still_refuse_a_committed_block(
    service, optimizer, db_path
):
    """Release did not widen reject/postpone; they remain scheduled-only."""

    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    run_id = proposal_run_id_of(job)
    row_before = raw_row(db_path, job_id)

    with pytest.raises(InvalidTransitionError):
        service.reject_proposal(
            job_id, actor=AUTHORITY, expected_proposal_run_id=run_id, reason="x"
        )

    with pytest.raises(InvalidTransitionError):
        service.postpone_proposal(
            job_id,
            actor=AUTHORITY,
            expected_proposal_run_id=run_id,
            reason="x",
            selected_date="2026-09-11",
        )

    assert raw_row(db_path, job_id) == row_before
    assert service.repository.get(job_id)["status"] == "notified"


def test_the_four_review_outcomes_are_four_distinct_events(service, optimizer):
    """Release is never recorded as a rejection, a postponement or a failure."""

    job = notified_job(service, optimizer)
    release(service, job)

    types = set(event_types(service, job["job_id"]))
    assert E.BLOCK_RELEASED in types
    assert E.PROPOSAL_REJECTED not in types
    assert E.PROPOSAL_POSTPONED not in types
    assert E.EXECUTION_NOT_COMPLETED not in types


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------


@pytest.fixture()
def client(db_path, monkeypatch):
    import backend.app.jobs.router as router_module

    http_service = JobService(repository=JobRepository(db_path), clock=lambda: FIXED_NOW)
    monkeypatch.setattr(router_module, "service", http_service)
    monkeypatch.setattr(
        router_module, "optimization_service", JobOptimizationService(http_service)
    )

    from backend.app.api.main import app

    with TestClient(app) as test_client:
        yield test_client


AUTHORITY_HEADERS = {"X-Actor-Id": "AUTHORITY-017", "X-Actor-Role": "AUTHORITY"}
ENGINEER_HEADERS = {"X-Actor-Id": "ENGINEER-003", "X-Actor-Role": "ENGINEER"}
WORKER_HEADERS = {"X-Actor-Id": "WORKER-042", "X-Actor-Role": "WORKER"}


def _http_notified(client) -> dict:
    created = client.post(
        "/jobs",
        json={
            "track_id": "UP-1",
            "job_type": "BALLAST_TAMPING",
            "distance_start": 1000.0,
            "distance_end": 1400.0,
            "workers_min": 2,
            "workers_max": 4,
            "description": "Rail head crack observed during foot patrol",
        },
        headers=WORKER_HEADERS,
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["job_id"]

    assert (
        client.post(
            f"/corridors/{CORRIDOR}/optimize-jobs", headers=ENGINEER_HEADERS
        ).status_code
        == 200
    )

    import backend.app.jobs.router as router_module

    run_id = proposal_run_id_of(router_module.service.repository.get(job_id))

    approved = client.post(
        f"/jobs/{job_id}/proposal/approve",
        json={"expected_proposal_run_id": run_id},
        headers=AUTHORITY_HEADERS,
    )
    assert approved.status_code == 200, approved.text
    return {"job_id": job_id, "run_id": run_id}


def test_http_release_returns_the_reported_job(client):
    context = _http_notified(client)

    response = client.post(
        f"/jobs/{context['job_id']}/proposal/release",
        json={"expected_proposal_run_id": context["run_id"], "reason": REASON},
        headers=AUTHORITY_HEADERS,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message"] == "Committed block released"
    assert body["job"]["status"] == "reported"
    assert body["job"]["schedule_start_minute"] is None

    history = client.get(
        f"/jobs/{context['job_id']}/history", headers=AUTHORITY_HEADERS
    ).json()["events"]
    assert history[-1]["event_type"] == "BLOCK_RELEASED"
    assert history[-1]["reason"] == REASON


def test_http_stale_release_is_a_conflict(client):
    context = _http_notified(client)

    response = client.post(
        f"/jobs/{context['job_id']}/proposal/release",
        json={"expected_proposal_run_id": "RUN-NOT-CURRENT", "reason": REASON},
        headers=AUTHORITY_HEADERS,
    )

    assert response.status_code == 409, response.text
    assert client.get(f"/jobs/{context['job_id']}").json()["status"] == "notified"


def test_http_release_after_start_is_refused(client):
    context = _http_notified(client)
    job_id = context["job_id"]

    import backend.app.jobs.router as router_module

    stored = router_module.service.repository.get(job_id)
    started = client.post(
        f"/jobs/{job_id}/execution/start",
        json=start_execution_body(stored),
        headers=WORKER_HEADERS,
    )
    assert started.status_code == 200, started.text

    response = client.post(
        f"/jobs/{job_id}/proposal/release",
        json={"expected_proposal_run_id": context["run_id"], "reason": REASON},
        headers=AUTHORITY_HEADERS,
    )

    assert response.status_code == 400, response.text
    assert client.get(f"/jobs/{job_id}").json()["status"] == "in_progress"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"expected_proposal_run_id": "RUN-1"},
        {"reason": REASON},
        {"expected_proposal_run_id": "", "reason": REASON},
        {"expected_proposal_run_id": "RUN-1", "reason": ""},
        {"expected_proposal_run_id": "RUN-1", "reason": REASON, "status": "cancelled"},
    ],
)
def test_http_release_validates_its_body_strictly(client, body):
    context = _http_notified(client)

    response = client.post(
        f"/jobs/{context['job_id']}/proposal/release",
        json=body,
        headers=AUTHORITY_HEADERS,
    )

    assert response.status_code == 422, response.text
    assert client.get(f"/jobs/{context['job_id']}").json()["status"] == "notified"


def test_http_release_is_denied_without_authorization(client):
    import backend.app.jobs.router as router_module

    context = _http_notified(client)
    original = router_module.service.authorization
    router_module.service.authorization = DenyPolicy(
        JobAction.RELEASE_COMMITTED_BLOCK
    )

    try:
        response = client.post(
            f"/jobs/{context['job_id']}/proposal/release",
            json={"expected_proposal_run_id": context["run_id"], "reason": REASON},
            headers=AUTHORITY_HEADERS,
        )
    finally:
        router_module.service.authorization = original

    assert response.status_code == 403, response.text
    assert client.get(f"/jobs/{context['job_id']}").json()["status"] == "notified"


def test_http_release_of_a_missing_job_is_a_404(client):
    response = client.post(
        "/jobs/JOB-DOES-NOT-EXIST/proposal/release",
        json={"expected_proposal_run_id": "RUN-1", "reason": REASON},
        headers=AUTHORITY_HEADERS,
    )

    assert response.status_code == 404, response.text


def test_release_route_introduces_no_new_status(client):
    """5, F5. One release operation; no new workflow state anywhere."""

    from backend.app.jobs.models import JobStatus

    assert [status.value for status in JobStatus] == [
        "reported",
        "scheduled",
        "notified",
        "in_progress",
        "completed",
    ]

    schema = client.app.openapi()["components"]["schemas"]["ReleaseBlockRequest"]
    assert set(schema["properties"]) == {"expected_proposal_run_id", "reason"}
    assert schema.get("additionalProperties") is False


# ----------------------------------------------------------------------
# F1. EXECUTION_COMPLETED must carry after-work evidence
# ----------------------------------------------------------------------


def _completion_plan(job_id, execution_id, *, after_work_evidence):
    """A hand-built completion, the one shape that could bypass the plan's check."""

    def plan(rows):
        job = rows[job_id]
        at = event_timestamp(FIXED_NOW)
        mutation = replace(
            JobMutation.unchanged(job),
            status="completed",
            updated_at=at,
            execution=lifecycle.ExecutionTransition(
                lifecycle.ExecutionTransitionKind.COMPLETE, execution_id
            ),
        )
        event = make_event(
            job_id,
            E.EXECUTION_COMPLETED,
            WORKER,
            occurred_at=at,
            optimization_run_id=proposal_run_id_of(job),
            before_state=JobStateSnapshot.from_job(job),
            after_state=JobStateSnapshot.from_job(mutation.as_job(job)),
            metadata={
                EXECUTION_ID_KEY: execution_id,
                "after_work_evidence": after_work_evidence,
            },
        )
        return [mutation], [event]

    return plan


@pytest.mark.parametrize("evidence", [[], None], ids=["empty", "missing"])
def test_completion_with_no_after_work_evidence_is_refused(
    service, optimizer, db_path, evidence
):
    """F1: the validator itself refuses it, not only the production plan."""

    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    _, execution = service.start_execution(
        job_id, actor=WORKER, **start_execution_body(job)
    )

    in_progress = service.repository.get(job_id)
    row_before = raw_row(db_path, job_id)
    events_before = count_events(db_path)

    with pytest.raises(ExecutionTokenError):
        service.repository.mutate_jobs(
            [job_id],
            _completion_plan(
                job_id, execution.execution_id, after_work_evidence=evidence
            ),
        )

    assert raw_row(db_path, job_id) == row_before
    assert count_events(db_path) == events_before
    assert service.repository.get(job_id)["status"] == "in_progress"
    assert in_progress["status"] == "in_progress"


def test_completion_with_after_work_evidence_is_accepted_by_the_validator(
    service, optimizer
):
    """The F1 check is narrow: a completion WITH evidence still passes."""

    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    _, execution = service.start_execution(
        job_id, actor=WORKER, **start_execution_body(job)
    )

    completed = service.repository.mutate_jobs(
        [job_id],
        _completion_plan(
            job_id,
            execution.execution_id,
            after_work_evidence=[
                {
                    "evidence_id": "EVD-1",
                    "phase": "AFTER_WORK",
                    "evidence_reference": "after/photo-1.jpg",
                }
            ],
        ),
    )[job_id]

    assert completed["status"] == "completed"


def test_f1_check_is_reachable_only_through_the_validator(service, optimizer):
    """Pure proof, independent of any database."""

    token = lifecycle.ExecutionTransition(
        lifecycle.ExecutionTransitionKind.COMPLETE, f"EXE-{'0' * 32}"
    )
    mutation = JobMutation(
        job_id="J1",
        status="completed",
        schedule_start_minute=10,
        schedule_end_minute=20,
        updated_at=None,
        last_solver_status=None,
        last_refusal_reason=None,
        block_candidate={},
        execution=token,
    )

    def completed_event(metadata):
        return make_event(
            "J1",
            E.EXECUTION_COMPLETED,
            WORKER,
            occurred_at=event_timestamp(FIXED_NOW),
            metadata={EXECUTION_ID_KEY: token.execution_id, **metadata},
        )

    with pytest.raises(ExecutionTokenError):
        validate_execution_events([mutation], [completed_event({})])

    with pytest.raises(ExecutionTokenError):
        validate_execution_events(
            [mutation], [completed_event({"after_work_evidence": []})]
        )

    validate_execution_events(
        [mutation], [completed_event({"after_work_evidence": [{"evidence_id": "E"}]})]
    )


# ----------------------------------------------------------------------
# F3. A status mutation can never strand a placement on a reported job
# ----------------------------------------------------------------------


def test_update_status_cannot_report_a_job_that_still_has_a_placement(
    service, optimizer, db_path
):
    """F3: the primitive is held to the same placement invariant as the service."""

    job = report(service)
    job_id = job["job_id"]
    optimize(optimizer)

    scheduled = service.repository.get(job_id)
    assert scheduled["status"] == "scheduled"
    assert scheduled["schedule_start_minute"] is not None

    row_before = raw_row(db_path, job_id)

    with pytest.raises(InvalidTransitionError):
        service.repository.update_status(job_id, "reported")

    with pytest.raises(InvalidTransitionError):
        service.repository.update_status(
            job_id, "reported", block_status="PLANNED", is_committed=False
        )

    assert raw_row(db_path, job_id) == row_before
    stored = service.repository.get(job_id)
    assert stored["status"] == "scheduled"
    assert stored["schedule_start_minute"] == scheduled["schedule_start_minute"]


def test_update_status_still_allows_legitimate_moves(service, optimizer, db_path):
    """F3 is an invariant, not a new prohibition: valid primitive moves still work."""

    job = report(service)
    job_id = job["job_id"]

    # reported -> reported with no placement is consistent, and permitted.
    assert service.repository.update_status(job_id, "reported")["status"] == "reported"

    optimize(optimizer)
    scheduled = service.repository.get(job_id)

    # scheduled -> scheduled with its live placement is consistent too.
    again = service.repository.update_status(job_id, "scheduled")
    assert again["status"] == "scheduled"
    assert again["schedule_start_minute"] == scheduled["schedule_start_minute"]


def test_reject_and_release_both_clear_the_placement_they_report_on(
    service, optimizer
):
    """The service path already satisfied F3's invariant; it still does."""

    rejected_job = report(service, distance_start=1000.0)
    released_job = report(service, distance_start=6000.0)
    optimize(optimizer)

    rejected = service.reject_proposal(
        rejected_job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=proposal_run_id_of(
            service.repository.get(rejected_job["job_id"])
        ),
        reason="not acceptable",
    )

    committed = approve(service, released_job["job_id"])
    released = release(service, committed)

    for job in (rejected, released):
        assert job["status"] == "reported"
        assert job["schedule_start_minute"] is None
        assert job["schedule_end_minute"] is None


# ----------------------------------------------------------------------
# F4. A solver refusal is never recorded against committed work
# ----------------------------------------------------------------------


def test_record_refusal_refuses_a_committed_job(service, optimizer, db_path):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    row_before = raw_row(db_path, job_id)

    with pytest.raises(CommittedJobError):
        service.repository.record_refusal(
            job_id, "no feasible window", updated_at="now", solver_status="INFEASIBLE"
        )

    assert raw_row(db_path, job_id) == row_before


def test_record_refusal_refuses_an_in_progress_job(service, optimizer, db_path):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    service.start_execution(job_id, actor=WORKER, **start_execution_body(job))
    row_before = raw_row(db_path, job_id)

    with pytest.raises(CommittedJobError):
        service.repository.record_refusal(
            job_id, "no feasible window", updated_at="now", solver_status="INFEASIBLE"
        )

    assert raw_row(db_path, job_id) == row_before


def test_record_refusal_still_works_on_an_uncommitted_job(service, optimizer):
    """F4 is narrow: legitimate refusal recording is unchanged."""

    job = report(service)
    job_id = job["job_id"]

    updated = service.repository.record_refusal(
        job_id, "no feasible window", updated_at="now", solver_status="INFEASIBLE"
    )

    assert updated["last_refusal_reason"] == "no feasible window"
    assert updated["last_solver_status"] == "INFEASIBLE"
    assert updated["status"] == "reported"

    optimize(optimizer)
    scheduled = service.repository.get(job_id)
    assert scheduled["status"] == "scheduled"

    still_works = service.repository.record_refusal(
        job_id, "later refusal", updated_at="now", solver_status="INFEASIBLE"
    )
    assert still_works["last_refusal_reason"] == "later refusal"


def test_record_refusal_still_refuses_a_terminal_job(service, optimizer):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]
    _, execution = service.start_execution(
        job_id, actor=WORKER, **start_execution_body(job)
    )
    service.complete_execution(
        job_id, actor=WORKER, **complete_execution_body(job, execution.execution_id)
    )

    with pytest.raises(TerminalJobError):
        service.repository.record_refusal(
            job_id, "no feasible window", updated_at="now"
        )
