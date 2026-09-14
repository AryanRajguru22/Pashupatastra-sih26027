"""Sprint 3 Slice 3: NEW Block Proposal -> Authority Review -> Approve/Reject/Postpone.

Core invariant under test throughout: existing blocks/possessions/train
movements remain constraints, the maintenance job is demand, and the
optimizer creates the NEW proposal - this slice only adds three ways an
authority can dispose of that proposal. It never modifies solver
behaviour; every solver-facing effect (earliest_start_minute honoured,
committed work pinned) is the SAME mechanism Slice 1/2 already exercise,
fed through the ordinary block_candidate the solver already reads.

Every test uses an isolated temporary database, exactly like Slice 1's
test_job_lifecycle_accountability.py and Slice 2's
test_slice2_new_job_block_proposal.py.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.jobs.optimization as optimization_module
from backend.app.data.horizon_anchor import horizon_relative_minutes
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.jobs.events import JobEventType
from backend.app.jobs.lifecycle import (
    InvalidTransitionError,
    StaleProposalError,
    plan_postpone,
    plan_reject,
)
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.proposal import build_block_proposal
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import (
    OPTIMIZATION_HORIZON_START,
    JobService,
)


WORKER = human_actor("WORKER-301", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-301", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-301", ActorRole.AUTHORITY)
AUTHORITY_2 = human_actor("AUTHORITY-302", ActorRole.AUTHORITY)

E = JobEventType


# ----------------------------------------------------------------------
# Fixtures / helpers
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


def report(
    service: JobService,
    actor=WORKER,
    track_id: str = "UP-1",
    job_type: str = "BALLAST_TAMPING",
    distance_start: float = 1000.0,
) -> dict:
    return service.create_job(
        JobCreateRequest(
            track_id=track_id,
            job_type=job_type,
            distance_start=distance_start,
            distance_end=distance_start + 400,
            workers_min=2,
            workers_max=4,
            description="Rail head crack observed during foot patrol",
        ),
        actor=actor,
    )


def schedule_job(service: JobService, optimizer: JobOptimizationService, **kwargs) -> tuple[dict, str]:
    """report() + one optimization pass, WITHOUT committing. status='scheduled'."""

    job = report(service, **kwargs)
    run_id = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)["optimization_run_id"]
    return service.repository.get(job["job_id"]), run_id


def history(service: JobService, job_id: str):
    return [item.event for item in service.history.list_for_job(job_id)]


def event_types(service: JobService, job_id: str) -> list[str]:
    return [event.event_type.value for event in history(service, job_id)]


def last_event(service: JobService, job_id: str):
    return history(service, job_id)[-1]


def count_events(db_path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]


# ----------------------------------------------------------------------
# APPROVE
# ----------------------------------------------------------------------


def test_approve_with_correct_run_id_commits(service, optimizer):
    job, run_id = schedule_job(service, optimizer)

    result = service.approve_proposal(
        job["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_id
    )

    assert result["status"] == "notified"
    assert result["block_candidate"]["status"] == "COMMITTED"
    assert result["block_candidate"]["is_committed"] is True

    approved = last_event(service, job["job_id"])
    assert approved.event_type is E.BLOCK_COMMITTED
    assert approved.actor.actor_id == "AUTHORITY-301"
    assert approved.optimization_run_id == run_id


def test_approve_with_stale_run_id_fails_with_state_unchanged(service, optimizer):
    job, first_run = schedule_job(service, optimizer)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)  # reoptimize: new run

    before = service.repository.get(job["job_id"])

    with pytest.raises(StaleProposalError):
        service.approve_proposal(
            job["job_id"], actor=AUTHORITY, expected_proposal_run_id=first_run
        )

    after = service.repository.get(job["job_id"])
    assert after == before
    assert last_event(service, job["job_id"]).event_type is E.TRANSITION_REJECTED


def test_approve_requires_expected_proposal_run_id(service, optimizer):
    job, run_id = schedule_job(service, optimizer)

    with pytest.raises(ValueError):
        service.approve_proposal(job["job_id"], actor=AUTHORITY, expected_proposal_run_id="")


def test_approve_from_invalid_state_is_rejected(service):
    job = report(service)  # status='reported': never optimized

    with pytest.raises(InvalidTransitionError):
        service.approve_proposal(
            job["job_id"], actor=AUTHORITY, expected_proposal_run_id="RUN-X"
        )


def test_approve_uses_commit_block_authorization_not_a_second_action(service, optimizer):
    """Approve must be gated by COMMIT_BLOCK, not a bespoke APPROVE_PROPOSAL check."""

    job, run_id = schedule_job(service, optimizer)
    seen = []

    class RecordingPolicy:
        enforcing = True

        def authorize(self, actor, action):
            seen.append(action)

    service.authorization = RecordingPolicy()

    service.approve_proposal(job["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_id)

    assert seen == [JobAction.COMMIT_BLOCK]


def test_committed_job_cannot_regress_during_later_optimization(service, optimizer):
    job, run_id = schedule_job(service, optimizer)
    service.approve_proposal(job["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_id)

    pinned = service.repository.get(job["job_id"])
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    after = service.repository.get(job["job_id"])
    assert after["status"] == "notified"
    assert after["block_candidate"]["status"] == "COMMITTED"
    assert after["schedule_start_minute"] == pinned["schedule_start_minute"]
    assert after["schedule_end_minute"] == pinned["schedule_end_minute"]
    assert last_event(service, job["job_id"]).event_type is E.COMMITTED_BLOCK_PRESERVED


# ----------------------------------------------------------------------
# REJECT
# ----------------------------------------------------------------------


def test_reject_with_reason_and_correct_run_id_records_event(service, optimizer):
    job, run_id = schedule_job(service, optimizer)

    result = service.reject_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="Track access denied by field crew",
    )

    assert result["status"] == "reported"
    assert result["schedule_start_minute"] is None
    assert result["schedule_end_minute"] is None
    assert result["last_refusal_reason"] == "Track access denied by field crew"
    assert result["block_candidate"]["status"] == "PLANNED"

    rejected = last_event(service, job["job_id"])
    assert rejected.event_type is E.PROPOSAL_REJECTED
    assert rejected.reason == "Track access denied by field crew"
    assert rejected.actor.actor_id == "AUTHORITY-301"
    assert rejected.optimization_run_id == run_id
    assert rejected.before_state.status == "scheduled"
    assert rejected.after_state.status == "reported"


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_reject_requires_a_non_empty_reason(service, optimizer, reason):
    job, run_id = schedule_job(service, optimizer)

    with pytest.raises(ValueError):
        service.reject_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=run_id,
            reason=reason,
        )

    # Nothing was written: the job is still exactly as scheduled.
    assert service.repository.get(job["job_id"])["status"] == "scheduled"


def test_reject_requires_expected_proposal_run_id(service, optimizer):
    job, run_id = schedule_job(service, optimizer)

    with pytest.raises(ValueError):
        service.reject_proposal(
            job["job_id"], actor=AUTHORITY, expected_proposal_run_id="", reason="No good"
        )


def test_reject_with_stale_run_id_is_rejected(service, optimizer):
    job, first_run = schedule_job(service, optimizer)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    before = service.repository.get(job["job_id"])

    with pytest.raises(StaleProposalError):
        service.reject_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=first_run,
            reason="Stale attempt",
        )

    assert service.repository.get(job["job_id"]) == before
    assert last_event(service, job["job_id"]).event_type is E.TRANSITION_REJECTED


def test_rejected_job_can_be_reoptimized_into_a_genuinely_new_proposal(service, optimizer):
    job, first_run = schedule_job(service, optimizer)

    service.reject_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=first_run,
        reason="Needs a different crew window",
    )
    assert service.repository.get(job["job_id"])["status"] == "reported"

    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    second_run = outcome["optimization_run_id"]

    assert second_run != first_run

    after = service.repository.get(job["job_id"])
    assert after["status"] == "scheduled"

    reproposed = last_event(service, job["job_id"])
    # A fresh BLOCK_PROPOSED, not BLOCK_REPROPOSED: the job was 'reported'
    # (not 'scheduled') at the moment this run considered it, because
    # reject withdrew the old proposal entirely rather than leaving it
    # in place for the solver to revise.
    assert reproposed.event_type is E.BLOCK_PROPOSED
    assert reproposed.optimization_run_id == second_run


# ----------------------------------------------------------------------
# POSTPONE
# ----------------------------------------------------------------------


def test_postpone_future_date_within_supported_horizon(service, optimizer):
    """Proves the horizon-anchoring plumbing for a non-degenerate not-before minute.

    The production horizon is a single day anchored at local MIDNIGHT
    (OPTIMIZATION_HORIZON_START); with selected_date always converted at
    clock_minutes=0/day_offset=0, midnight anchoring means only
    selected_date == the horizon's own date resolves inside (0, 1440) -
    every other date is genuinely beyond this deployment's one-day
    horizon (see test_postpone_beyond_horizon_fails_closed below, which
    proves exactly that against the real production horizon_start).
    horizon_start is overridable on JobService.postpone_proposal only so
    a test can exercise a non-degenerate in-horizon minute against the
    SAME conversion production uses - no production caller overrides it.
    """

    job, run_id = schedule_job(service, optimizer)
    custom_horizon_start = "2026-09-09T06:00:00+05:30"

    result = service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="Wait for possession window clearance",
        selected_date="2026-09-10",
        horizon_start=custom_horizon_start,
    )

    assert result["status"] == "reported"
    assert result["block_candidate"]["earliest_start_minute"] == 1080
    assert 0 < result["block_candidate"]["earliest_start_minute"] < result["block_candidate"]["latest_end_minute"]


def test_postpone_exact_horizon_relative_minutes_calculation(service, optimizer):
    """postpone_proposal must use horizon_relative_minutes(selected_date, 0,
    0, horizon_start) exactly - clock_minutes=0 and day_offset=0, i.e.
    local midnight of selected_date, and nothing else."""

    job, run_id = schedule_job(service, optimizer)
    horizon_start = "2026-09-08T12:00:00+05:30"
    selected_date = "2026-09-09"

    expected = horizon_relative_minutes(selected_date, 0, 0, horizon_start)
    assert expected == 720  # midnight of the 9th is 12h after noon on the 8th

    result = service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="Match the authoritative conversion exactly",
        selected_date=selected_date,
        horizon_start=horizon_start,
    )

    assert result["block_candidate"]["earliest_start_minute"] == expected == 720


def test_postpone_sets_earliest_start_minute_the_next_optimization_reads(service, optimizer):
    """The exact mechanism 'next optimization respects earliest_start_minute'
    relies on: the candidate the solver sees carries the postponed value.
    solver.py's own hard-constraint enforcement of earliest_start_minute
    is Slice-0 territory (test_solver.py) and is untouched here.
    """

    job, run_id = schedule_job(service, optimizer)
    custom_horizon_start = "2026-09-09T06:00:00+05:30"

    service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="Not before this window",
        selected_date="2026-09-10",
        horizon_start=custom_horizon_start,
    )

    candidates, _ = service.classify_for_optimization()
    postponed = next(c for c in candidates if c.block_id == job["job_id"])
    assert postponed.earliest_start_minute == 1080
    assert postponed.status == "PLANNED"


def test_postpone_to_horizon_start_date_can_still_be_scheduled(service, optimizer):
    """not_before_minute == 0 (postponing to the production horizon's own
    date) must not itself block scheduling - it is a valid boundary, not
    a beyond-horizon refusal."""

    job, run_id = schedule_job(service, optimizer)

    result = service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="No-op postpone to the horizon's own start date",
        selected_date="2026-09-10",  # == OPTIMIZATION_HORIZON_START's date
    )
    assert result["block_candidate"]["earliest_start_minute"] == 0

    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    assert job["job_id"] in {s["job_id"] for s in outcome["scheduled"]}


def test_postpone_beyond_horizon_fails_closed(service, optimizer):
    job, run_id = schedule_job(service, optimizer)
    before = service.repository.get(job["job_id"])

    with pytest.raises(InvalidTransitionError, match=r"minute 1440"):
        service.postpone_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=run_id,
            reason="Try to postpone past the supported horizon",
            selected_date="2026-09-11",  # the day AFTER OPTIMIZATION_HORIZON_START
        )

    after = service.repository.get(job["job_id"])
    assert after == before
    assert last_event(service, job["job_id"]).event_type is E.TRANSITION_REJECTED


def test_postpone_into_the_past_fails_closed(service, optimizer):
    job, run_id = schedule_job(service, optimizer)
    before = service.repository.get(job["job_id"])

    with pytest.raises(InvalidTransitionError):
        service.postpone_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=run_id,
            reason="Try to postpone before the horizon started",
            selected_date="2026-09-09",  # the day BEFORE OPTIMIZATION_HORIZON_START
        )

    assert service.repository.get(job["job_id"]) == before


def test_postpone_event_metadata_carries_all_required_decision_evidence(service, optimizer):
    job, run_id = schedule_job(service, optimizer)
    original_start = job["schedule_start_minute"]
    original_end = job["schedule_end_minute"]
    custom_horizon_start = "2026-09-09T06:00:00+05:30"

    service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="Crew unavailable until later",
        selected_date="2026-09-10",
        horizon_start=custom_horizon_start,
    )

    postponed = last_event(service, job["job_id"])
    assert postponed.event_type is E.PROPOSAL_POSTPONED
    assert postponed.actor.actor_id == "AUTHORITY-301"
    assert postponed.occurred_at  # timestamp recorded
    assert postponed.reason == "Crew unavailable until later"

    meta = postponed.metadata
    assert meta["selected_date"] == "2026-09-10"
    assert meta["not_before_minute"] == 1080
    assert meta["original_proposal_run_id"] == run_id
    assert meta["original_start_minute"] == original_start
    assert meta["original_end_minute"] == original_end
    assert meta["expected_proposal_run_id"] == run_id
    assert isinstance(meta["proposal_digest"], str) and len(meta["proposal_digest"]) == 64


def test_postpone_requires_expected_proposal_run_id_and_reason(service, optimizer):
    job, run_id = schedule_job(service, optimizer)

    with pytest.raises(ValueError):
        service.postpone_proposal(
            job["job_id"], actor=AUTHORITY, expected_proposal_run_id="",
            reason="x", selected_date="2026-09-10",
        )

    with pytest.raises(ValueError):
        service.postpone_proposal(
            job["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_id,
            reason="", selected_date="2026-09-10",
        )


def test_postpone_with_stale_run_id_is_rejected(service, optimizer):
    job, first_run = schedule_job(service, optimizer)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    before = service.repository.get(job["job_id"])

    with pytest.raises(StaleProposalError):
        service.postpone_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=first_run,
            reason="Stale postpone attempt",
            selected_date="2026-09-10",
        )

    assert service.repository.get(job["job_id"]) == before
    assert last_event(service, job["job_id"]).event_type is E.TRANSITION_REJECTED


def test_concurrent_approve_vs_reoptimization_loses_to_the_newer_run(service, optimizer):
    """An approve racing a reoptimization must never commit a superseded proposal.

    The in-process lifecycle lock (JobService.lifecycle_lock) serializes
    optimize/approve/reject/postpone; the stale-run-id check inside the
    transactional mutation (plan_commit, called by notify -> which
    approve_proposal delegates to) is what makes a LATE approve fail
    once a newer run has already superseded it, whether or not the two
    ever literally overlap in wall-clock time.
    """

    job, first_run = schedule_job(service, optimizer)

    # A reoptimization lands before the approve reaches the repository -
    # simulated by simply running it first, which is the only
    # observable effect a true interleaving could have on this check.
    second_run = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)[
        "optimization_run_id"
    ]
    assert second_run != first_run

    with pytest.raises(StaleProposalError):
        service.approve_proposal(
            job["job_id"], actor=AUTHORITY, expected_proposal_run_id=first_run
        )

    # The newer proposal is untouched and can still be approved.
    result = service.approve_proposal(
        job["job_id"], actor=AUTHORITY, expected_proposal_run_id=second_run
    )
    assert result["status"] == "notified"


def test_concurrent_postpone_vs_reoptimization_loses_to_the_newer_run(service, optimizer):
    job, first_run = schedule_job(service, optimizer)
    second_run = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)[
        "optimization_run_id"
    ]

    with pytest.raises(StaleProposalError):
        service.postpone_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=first_run,
            reason="Racing an in-flight reoptimization",
            selected_date="2026-09-10",
        )

    result = service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=second_run,
        reason="Correct, current run",
        selected_date="2026-09-10",
    )
    assert result["status"] == "reported"


def test_reject_blocks_on_a_running_optimization_and_cannot_reject_stale_data(
    service, optimizer, monkeypatch
):
    """Genuine interleaving for plan_reject, mirroring Slice 1's
    test_commit_waits_for_a_running_optimization_and_cannot_commit_stale_data
    (test_job_lifecycle_accountability.py) - the two tests above only
    prove the SEQUENTIAL ordering outcome (optimize fully completes,
    then reject is attempted); this proves reject actually BLOCKS on
    JobService.lifecycle_lock while a real solve is in flight, rather
    than merely losing a race that never truly overlapped.
    """

    job = report(service)
    first = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    solving = threading.Event()
    release = threading.Event()
    real_solve = optimization_module.solve

    def gated_solve(request):
        solving.set()
        assert release.wait(10), "test gate never released"
        return real_solve(request)

    monkeypatch.setattr(optimization_module, "solve", gated_solve)

    outcomes: list[dict] = []
    errors: list[BaseException] = []
    reject_finished = threading.Event()

    def run_optimization():
        outcomes.append(optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER))

    def run_reject():
        try:
            service.reject_proposal(
                job["job_id"],
                actor=AUTHORITY,
                expected_proposal_run_id=first["optimization_run_id"],
                reason="Racing an in-flight reoptimization",
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            reject_finished.set()

    optimizing = threading.Thread(target=run_optimization)
    optimizing.start()
    assert solving.wait(10)

    rejecting = threading.Thread(target=run_reject)
    rejecting.start()

    # The reject genuinely blocks: it cannot even reach its own
    # transaction while optimization holds the shared lifecycle lock
    # inside the gated solve.
    assert not reject_finished.wait(0.5), (
        "the reject ran while optimization held the lifecycle lock"
    )

    release.set()
    optimizing.join(10)
    rejecting.join(10)

    # Optimization wins the lock first (reject was started only after
    # solving.wait() confirmed optimization already held it) and
    # produces a newer proposal/run.
    assert len(outcomes) == 1
    assert len(errors) == 1 and isinstance(errors[0], StaleProposalError)

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "scheduled"
    assert (
        stored["block_candidate"]["metadata"]["proposal_run_id"]
        == outcomes[0]["optimization_run_id"]
    )
    assert stored["block_candidate"]["metadata"]["proposal_run_id"] != first["optimization_run_id"]

    # No PROPOSAL_REJECTED was ever recorded - the losing reject
    # produced no mutation and no success event, only the accountable
    # record of the refused attempt.
    types = event_types(service, job["job_id"])
    assert E.PROPOSAL_REJECTED.value not in types
    assert types[-2:] == [E.BLOCK_REPROPOSED.value, E.TRANSITION_REJECTED.value]


def test_postpone_blocks_on_a_running_optimization_and_cannot_postpone_stale_data(
    service, optimizer, monkeypatch
):
    """Genuine interleaving for plan_postpone - see the reject test above
    for why this is not redundant with the sequential-ordering test."""

    job = report(service)
    first = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    solving = threading.Event()
    release = threading.Event()
    real_solve = optimization_module.solve

    def gated_solve(request):
        solving.set()
        assert release.wait(10), "test gate never released"
        return real_solve(request)

    monkeypatch.setattr(optimization_module, "solve", gated_solve)

    outcomes: list[dict] = []
    errors: list[BaseException] = []
    postpone_finished = threading.Event()

    def run_optimization():
        outcomes.append(optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER))

    def run_postpone():
        try:
            service.postpone_proposal(
                job["job_id"],
                actor=AUTHORITY,
                expected_proposal_run_id=first["optimization_run_id"],
                reason="Racing an in-flight reoptimization",
                selected_date="2026-09-10",
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            postpone_finished.set()

    optimizing = threading.Thread(target=run_optimization)
    optimizing.start()
    assert solving.wait(10)

    postponing = threading.Thread(target=run_postpone)
    postponing.start()

    assert not postpone_finished.wait(0.5), (
        "the postpone ran while optimization held the lifecycle lock"
    )

    release.set()
    optimizing.join(10)
    postponing.join(10)

    assert len(outcomes) == 1
    assert len(errors) == 1 and isinstance(errors[0], StaleProposalError)

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "scheduled"
    assert (
        stored["block_candidate"]["metadata"]["proposal_run_id"]
        == outcomes[0]["optimization_run_id"]
    )
    assert stored["block_candidate"]["metadata"]["proposal_run_id"] != first["optimization_run_id"]

    types = event_types(service, job["job_id"])
    assert E.PROPOSAL_POSTPONED.value not in types
    assert types[-2:] == [E.BLOCK_REPROPOSED.value, E.TRANSITION_REJECTED.value]


# ----------------------------------------------------------------------
# Same-proposal races: approve/reject/postpone against EACH OTHER
# ----------------------------------------------------------------------


def test_approve_then_reject_same_proposal_fails_closed(service, optimizer):
    """Whichever transition reaches the row first wins; the loser must
    fail on the STATUS precondition (SCHEDULED required), not merely on
    a run-id mismatch, since both were given the SAME run id."""

    job, run_id = schedule_job(service, optimizer)

    approved = service.approve_proposal(
        job["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_id
    )
    assert approved["status"] == "notified"

    before = service.repository.get(job["job_id"])
    history_before = history(service, job["job_id"])

    with pytest.raises(InvalidTransitionError):
        service.reject_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=run_id,
            reason="Too late - already approved",
        )

    after = service.repository.get(job["job_id"])
    assert after == before
    assert after["status"] == "notified"
    assert after["block_candidate"]["status"] == "COMMITTED"
    assert after["block_candidate"]["is_committed"] is True

    # The winning approve's own state/history is intact: the same
    # events, in the same order, still precede whatever the failed
    # reject appended.
    history_after = history(service, job["job_id"])
    assert history_after[: len(history_before)] == history_before
    assert history_before[-1].event_type is E.BLOCK_COMMITTED
    assert history_after[-1].event_type is E.TRANSITION_REJECTED
    assert E.PROPOSAL_REJECTED not in [e.event_type for e in history_after]


def test_reject_then_approve_same_proposal_fails_closed(service, optimizer):
    job, run_id = schedule_job(service, optimizer)

    rejected = service.reject_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="Not acceptable",
    )
    assert rejected["status"] == "reported"

    before = service.repository.get(job["job_id"])
    history_before = history(service, job["job_id"])

    with pytest.raises(InvalidTransitionError):
        service.approve_proposal(
            job["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_id
        )

    after = service.repository.get(job["job_id"])
    assert after == before
    assert after["status"] == "reported"
    assert after["schedule_start_minute"] is None
    assert after["schedule_end_minute"] is None

    history_after = history(service, job["job_id"])
    assert history_after[: len(history_before)] == history_before
    assert history_before[-1].event_type is E.PROPOSAL_REJECTED
    assert history_after[-1].event_type is E.TRANSITION_REJECTED
    assert E.BLOCK_COMMITTED not in [e.event_type for e in history_after]


def test_postpone_then_approve_same_proposal_fails_closed(service, optimizer):
    job, run_id = schedule_job(service, optimizer)

    postponed = service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="Not ready yet",
        selected_date="2026-09-10",
    )
    assert postponed["status"] == "reported"

    before = service.repository.get(job["job_id"])
    history_before = history(service, job["job_id"])

    with pytest.raises(InvalidTransitionError):
        service.approve_proposal(
            job["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_id
        )

    after = service.repository.get(job["job_id"])
    assert after == before
    assert after["status"] == "reported"
    # The postponement's own effect (the not-before constraint) is
    # untouched by the failed approve attempt.
    assert after["block_candidate"]["earliest_start_minute"] == 0

    history_after = history(service, job["job_id"])
    assert history_after[: len(history_before)] == history_before
    assert history_before[-1].event_type is E.PROPOSAL_POSTPONED
    assert history_after[-1].event_type is E.TRANSITION_REJECTED
    assert E.BLOCK_COMMITTED not in [e.event_type for e in history_after]


def test_approve_then_postpone_same_proposal_fails_closed(service, optimizer):
    job, run_id = schedule_job(service, optimizer)

    approved = service.approve_proposal(
        job["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_id
    )
    assert approved["status"] == "notified"

    before = service.repository.get(job["job_id"])
    history_before = history(service, job["job_id"])

    with pytest.raises(InvalidTransitionError):
        service.postpone_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=run_id,
            reason="Too late - already approved",
            selected_date="2026-09-10",
        )

    after = service.repository.get(job["job_id"])
    assert after == before
    assert after["status"] == "notified"
    assert after["block_candidate"]["status"] == "COMMITTED"
    assert after["block_candidate"]["is_committed"] is True

    history_after = history(service, job["job_id"])
    assert history_after[: len(history_before)] == history_before
    assert history_before[-1].event_type is E.BLOCK_COMMITTED
    assert history_after[-1].event_type is E.TRANSITION_REJECTED
    assert E.PROPOSAL_POSTPONED not in [e.event_type for e in history_after]


def test_authorization_denial_blocks_review_actions_before_any_state_change(service, optimizer):
    job, run_id = schedule_job(service, optimizer)

    class DenyingPolicy:
        enforcing = True

        def authorize(self, actor, action):
            if action in (
                JobAction.REJECT_PROPOSAL,
                JobAction.POSTPONE_PROPOSAL,
                JobAction.COMMIT_BLOCK,
            ):
                raise AuthorizationDenied(actor, action, "test policy denies review actions")

    service.authorization = DenyingPolicy()
    before = service.repository.get(job["job_id"])
    events_before = count_events(service.repository.db_path)

    with pytest.raises(AuthorizationDenied):
        service.reject_proposal(
            job["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_id, reason="x"
        )

    with pytest.raises(AuthorizationDenied):
        service.postpone_proposal(
            job["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_id,
            reason="x", selected_date="2026-09-10",
        )

    with pytest.raises(AuthorizationDenied):
        service.approve_proposal(
            job["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_id
        )

    # A denial happens before any read/write of job state, so nothing moved.
    assert service.repository.get(job["job_id"]) == before
    assert count_events(service.repository.db_path) == events_before


# ----------------------------------------------------------------------
# CROSS-CUTTING
# ----------------------------------------------------------------------


def test_full_reject_reoptimize_approve_cycle(service, optimizer):
    job, first_run = schedule_job(service, optimizer)

    service.reject_proposal(
        job["job_id"], actor=AUTHORITY, expected_proposal_run_id=first_run,
        reason="Initial window doesn't work",
    )

    second_run = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)[
        "optimization_run_id"
    ]
    assert second_run != first_run

    final = service.approve_proposal(
        job["job_id"], actor=AUTHORITY, expected_proposal_run_id=second_run
    )

    assert final["status"] == "notified"
    assert final["block_candidate"]["status"] == "COMMITTED"

    types = event_types(service, job["job_id"])
    assert types == [
        "JOB_CREATED",
        "JOB_SCORED",
        "OPTIMIZATION_REQUESTED",
        "OPTIMIZATION_COMPLETED",
        "BLOCK_PROPOSED",
        "PROPOSAL_REJECTED",
        "OPTIMIZATION_REQUESTED",
        "OPTIMIZATION_COMPLETED",
        "BLOCK_PROPOSED",
        "BLOCK_COMMITTED",
    ]


def test_chronological_history_has_no_cross_job_contamination(service, optimizer):
    # Both jobs scheduled by the SAME optimization run, so rejecting one
    # cannot be confused with a later reoptimization touching the other.
    job_a = report(service, track_id="UP-1", distance_start=1000.0)
    job_b = report(service, track_id="UP-1", distance_start=5000.0)
    run_a = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)["optimization_run_id"]

    events_b_before = history(service, job_b["job_id"])

    service.reject_proposal(
        job_a["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_a,
        reason="Job A specific rejection",
    )

    events_b_after = history(service, job_b["job_id"])
    assert events_b_after == events_b_before
    assert all(e.job_id == job_b["job_id"] for e in events_b_after)

    events_a = history(service, job_a["job_id"])
    assert all(e.job_id == job_a["job_id"] for e in events_a)
    assert event_types(service, job_a["job_id"])[-1] == "PROPOSAL_REJECTED"

    sequences_a = [
        item.sequence for item in service.history.list_for_job(job_a["job_id"])
    ]
    assert sequences_a == sorted(sequences_a)


def test_digest_changes_when_proposal_placement_or_identity_changes(service, optimizer):
    job, run_id = schedule_job(service, optimizer)

    from backend.app.audit.repository import AuditRepository

    stored = service.repository.get(job["job_id"])
    run = AuditRepository(service.repository.db_path).get(run_id)
    events = service.history.list_for_job(job["job_id"])

    proposal = build_block_proposal(stored, service.corridor.corridor_id, events, run)
    baseline_digest = proposal.digest()

    # Changing an identity/placement field changes the digest...
    assert replace(proposal, start_minute=proposal.start_minute + 5).digest() != baseline_digest
    assert replace(proposal, job_id="JOB-DIFFERENT").digest() != baseline_digest
    assert replace(proposal, track_id="SOME-OTHER-TRACK").digest() != baseline_digest

    # ...but a purely explanatory/derived field does not.
    assert replace(proposal, generated_at="2099-01-01T00:00:00.000000+00:00").digest() == baseline_digest
    assert replace(proposal, objective_score=proposal.objective_score + 1.0).digest() == baseline_digest
    assert replace(proposal, explanation=()).digest() == baseline_digest


def test_every_new_state_changing_http_route_records_history():
    """The three new routes are on the same accountable path as every
    other job mutation - see also
    test_job_lifecycle_accountability.test_every_state_changing_route_is_a_reviewed_history_recording_path,
    which pins the route inventory itself.
    """

    from backend.app.api.main import app
    from backend.app.jobs.router import service as router_service

    def wipe():
        with closing(sqlite3.connect(router_service.repository.db_path)) as conn, conn:
            conn.execute("DELETE FROM maintenance_jobs")

    wipe()
    client = TestClient(app)
    worker_headers = {"X-Actor-Id": "WORKER-301", "X-Actor-Role": "WORKER"}
    engineer_headers = {"X-Actor-Id": "ENGINEER-301", "X-Actor-Role": "ENGINEER"}
    authority_headers = {"X-Actor-Id": "AUTHORITY-301", "X-Actor-Role": "AUTHORITY"}

    try:
        created = client.post(
            "/jobs",
            json={
                "track_id": "UP-1",
                "job_type": "BALLAST_TAMPING",
                "distance_start": 1000.0,
                "distance_end": 1400.0,
                "workers_min": 2,
                "workers_max": 4,
                "description": "HTTP route accountability check",
            },
            headers=worker_headers,
        ).json()
        job_id = created["job_id"]

        run_id = client.post(
            "/corridors/CORRIDOR_A/optimize-jobs", headers=engineer_headers
        ).json()["optimization_run_id"]

        def events():
            return client.get(f"/jobs/{job_id}/history").json()["events"]

        before = len(events())
        rejected = client.post(
            f"/jobs/{job_id}/proposal/reject",
            json={"expected_proposal_run_id": run_id, "reason": "HTTP reject check"},
            headers=authority_headers,
        )
        assert rejected.status_code == 200, rejected.text
        added = events()[before:]
        assert added[-1]["event_type"] == "PROPOSAL_REJECTED"

        run_id_2 = client.post(
            "/corridors/CORRIDOR_A/optimize-jobs", headers=engineer_headers
        ).json()["optimization_run_id"]

        before = len(events())
        postponed = client.post(
            f"/jobs/{job_id}/proposal/postpone",
            json={
                "expected_proposal_run_id": run_id_2,
                "reason": "HTTP postpone check",
                "selected_date": "2026-09-10",
            },
            headers=authority_headers,
        )
        assert postponed.status_code == 200, postponed.text
        added = events()[before:]
        assert added[-1]["event_type"] == "PROPOSAL_POSTPONED"

        run_id_3 = client.post(
            "/corridors/CORRIDOR_A/optimize-jobs", headers=engineer_headers
        ).json()["optimization_run_id"]

        before = len(events())
        approved = client.post(
            f"/jobs/{job_id}/proposal/approve",
            json={"expected_proposal_run_id": run_id_3},
            headers=authority_headers,
        )
        assert approved.status_code == 200, approved.text
        added = events()[before:]
        assert added[-1]["event_type"] == "BLOCK_COMMITTED"
        assert client.get(f"/jobs/{job_id}").json()["status"] == "notified"

    finally:
        wipe()


def test_postpone_route_rejects_malformed_selected_date():
    from backend.app.api.main import app
    from backend.app.jobs.router import service as router_service

    def wipe():
        with closing(sqlite3.connect(router_service.repository.db_path)) as conn, conn:
            conn.execute("DELETE FROM maintenance_jobs")

    wipe()
    client = TestClient(app)
    worker_headers = {"X-Actor-Id": "WORKER-301", "X-Actor-Role": "WORKER"}
    authority_headers = {"X-Actor-Id": "AUTHORITY-301", "X-Actor-Role": "AUTHORITY"}

    try:
        created = client.post(
            "/jobs",
            json={
                "track_id": "UP-1",
                "job_type": "BALLAST_TAMPING",
                "distance_start": 1000.0,
                "distance_end": 1400.0,
                "workers_min": 2,
                "workers_max": 4,
                "description": "malformed date check",
            },
            headers=worker_headers,
        ).json()

        response = client.post(
            f"/jobs/{created['job_id']}/proposal/postpone",
            json={
                "expected_proposal_run_id": "RUN-X",
                "reason": "bad date",
                "selected_date": "not-a-date",
            },
            headers=authority_headers,
        )
        assert response.status_code == 422
    finally:
        wipe()


def test_reject_and_postpone_reject_extra_fields():
    from backend.app.api.main import app
    from backend.app.jobs.router import service as router_service

    def wipe():
        with closing(sqlite3.connect(router_service.repository.db_path)) as conn, conn:
            conn.execute("DELETE FROM maintenance_jobs")

    wipe()
    client = TestClient(app)
    headers = {"X-Actor-Id": "AUTHORITY-301", "X-Actor-Role": "AUTHORITY"}

    try:
        for path, body in (
            (
                "/jobs/JOB-DOES-NOT-EXIST/proposal/approve",
                {"expected_proposal_run_id": "RUN-X", "extra": True},
            ),
            (
                "/jobs/JOB-DOES-NOT-EXIST/proposal/reject",
                {"expected_proposal_run_id": "RUN-X", "reason": "x", "extra": True},
            ),
            (
                "/jobs/JOB-DOES-NOT-EXIST/proposal/postpone",
                {
                    "expected_proposal_run_id": "RUN-X",
                    "reason": "x",
                    "selected_date": "2026-09-10",
                    "extra": True,
                },
            ),
        ):
            response = client.post(path, json=body, headers=headers)
            assert response.status_code == 422, (path, response.text)
    finally:
        wipe()
