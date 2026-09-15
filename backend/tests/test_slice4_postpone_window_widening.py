"""Slice 4 Step 3: the authority-postpone window-widening fix.

BEFORE this step, JobService.postpone_proposal / lifecycle.plan_postpone
raised a job's earliest_start_minute to the postponed date's
horizon-relative minute but never touched latest_end_minute. Under
Step 2's horizon plumbing that made a widened deployment horizon
actively dangerous: a job postponed past its OLD latest_end_minute
would satisfy the (also stale) admissibility check by coincidence, or
be wrongly refused by it, and even when accepted would come out of
postponement with latest_start = latest_end - duration < earliest_start
- silently and permanently window_infeasible on the very next
optimization (SLICE4_MULTIDAY_SCHEDULING_DESIGN.md Sec.2 limitation #3,
Sec.8.2).

This suite proves, in order:
  A. default JobService (OPTIMIZATION_HORIZON_MINUTES) - unchanged
     behavior/mechanism; Slice 4 Step 5 later widened the VALUE of that
     constant from 1440 to 2880, and the assertions below were updated
     to match (see the section's own comment)
  B. explicit multi-day JobService - widening plumbed through correctly
  C. the CRITICAL regression: a job whose stored latest_end_minute has
     drifted below the CURRENT deployment horizon must still postpone
     correctly and come out widened, not stuck at its stale value
  D. widening never shrinks an already-wider-than-horizon window
  E. boundary / fail-closed behavior is unchanged (still bounded by the
     deployment horizon, not silently opened up)
  F. stale-proposal / concurrency protection is untouched
  G. event/history metadata still carries every required fact, plus the
     new widening facts
  H. the existing authority lifecycle (postpone -> reoptimize, and
     reject/approve) still works, including a genuine end-to-end
     multi-day reschedule through the canonical timetable path

Nothing here wires TimetableCoverage (Step 1) into possession_inputs.
This module itself makes no production change - Step 5 (a later,
separate change) is what widened OPTIMIZATION_HORIZON_MINUTES to 2880;
the "default" tests in section A were updated as a consequence, per
SLICE4_MULTIDAY_SCHEDULING_DESIGN.md Sec.11.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List

import pytest

from backend.app.data.canonical_train import CorridorTopology
from backend.app.data.corridor_dataset import CorridorDataset
from backend.app.data.generator import CorridorDataGenerator
from backend.app.data.models import Corridor, TrackSegment
from backend.app.data.section_registry import SectionRegistry
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.jobs.events import JobEventType
from backend.app.jobs.lifecycle import InvalidTransitionError, StaleProposalError
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService


WORKER = human_actor("WORKER-401", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-401", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-401", ActorRole.AUTHORITY)

HORIZON_START = "2026-09-10T00:00:00+05:30"  # == OPTIMIZATION_HORIZON_START
E = JobEventType


# ----------------------------------------------------------------------
# Shared helpers (CORRIDOR_A generated path - sections A-H except the
# end-to-end part of H, which needs real multi-day timetable data).
# ----------------------------------------------------------------------


def _service(tmp_path: Path, name: str = "jobs.db", **kwargs) -> JobService:
    return JobService(repository=JobRepository(tmp_path / name), **kwargs)


def _report(
    service: JobService,
    actor=WORKER,
    track_id: str = "UP-1",
    distance_start: float = 1000.0,
) -> Dict[str, Any]:
    return service.create_job(
        JobCreateRequest(
            track_id=track_id,
            job_type="BALLAST_TAMPING",
            distance_start=distance_start,
            distance_end=distance_start + 400,
            workers_min=2,
            workers_max=4,
            description="Slice 4 Step 3 postpone-widening check",
        ),
        actor=actor,
    )


def _schedule(service: JobService, optimizer: JobOptimizationService, **kwargs):
    """report() + one optimization pass, WITHOUT committing -> 'scheduled'."""

    job = _report(service, **kwargs)
    run_id = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)[
        "optimization_run_id"
    ]
    return service.repository.get(job["job_id"]), run_id


def _force_latest_end_minute(service: JobService, job_id: str, value: int) -> None:
    """TEST-ONLY: directly rewrite a job's stored latest_end_minute,
    bypassing the lifecycle entirely.

    Simulates a job whose stored window predates a horizon change (Step
    2's create_job always stamps the CURRENT self.horizon_minutes at
    creation time, so a mismatch between a job's stored latest_end_minute
    and its service's current horizon_minutes is otherwise unreachable
    within one JobService's lifetime - this is exactly the situation
    Sec.8.2/Step 3's own widening logic must handle correctly, e.g. a
    job created under a narrower horizon before a deployment widened it,
    or restored from an older snapshot).
    """

    job = service.repository.get(job_id)
    block = dict(job["block_candidate"])
    block["latest_end_minute"] = value

    with closing(sqlite3.connect(service.repository.db_path)) as conn, conn:
        conn.execute(
            "UPDATE maintenance_jobs SET block_candidate_json = ? WHERE job_id = ?",
            (json.dumps(block), job_id),
        )


def _history(service: JobService, job_id: str):
    return [item.event for item in service.history.list_for_job(job_id)]


def _last_event(service: JobService, job_id: str):
    return _history(service, job_id)[-1]


# ----------------------------------------------------------------------
# A. Default JobService (OPTIMIZATION_HORIZON_MINUTES) - unchanged
#    mechanism, though Slice 4 Step 5 widened the value itself from
#    1440 to 2880 - see backend.app.jobs.service's own comment on that
#    constant. These tests were written against the pre-Step-5 default
#    and are updated here per that step's own explicit callout
#    (SLICE4_MULTIDAY_SCHEDULING_DESIGN.md Sec.11): the underlying
#    guarantee ("a no-op postpone doesn't shrink the window", "beyond
#    the deployment horizon still fails closed") is unchanged, only the
#    concrete boundary values are.
# ----------------------------------------------------------------------


def test_default_job_service_horizon_minutes_matches_the_production_constant(
    tmp_path: Path,
):
    from backend.app.jobs.service import OPTIMIZATION_HORIZON_MINUTES

    assert _service(tmp_path).horizon_minutes == OPTIMIZATION_HORIZON_MINUTES
    assert _service(tmp_path).horizon_minutes == 2880


def test_default_horizon_postpone_within_horizon_is_unaffected(
    tmp_path: Path,
):
    """Postponing to the horizon's own start date (not_before_minute=0)
    under the DEFAULT 2880-minute horizon must behave exactly as before
    Step 3: no widening needed since max(2880, 2880) == 2880."""

    service = _service(tmp_path)
    optimizer = JobOptimizationService(service)
    job, run_id = _schedule(service, optimizer)

    result = service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="No-op postpone under the default horizon",
        selected_date="2026-09-10",
    )

    assert result["status"] == "reported"
    assert result["block_candidate"]["earliest_start_minute"] == 0
    assert result["block_candidate"]["latest_end_minute"] == 2880

    # Still schedulable by the very next run - the existing guarantee.
    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    assert job["job_id"] in {s["job_id"] for s in outcome["scheduled"]}


def test_default_horizon_beyond_horizon_still_fails_closed(tmp_path: Path):
    """The exact pre-Step-3 regression case, unchanged in mechanism -
    repointed at the NEW (post-Step-5) horizon boundary, per
    SLICE4_MULTIDAY_SCHEDULING_DESIGN.md Sec.11's explicit instruction
    that this class of test must be intentionally updated, not silently
    left red. selected_date="2026-09-12" resolves to minute 2880, which
    is now the first minute OUTSIDE the default [0, 2880) horizon."""

    service = _service(tmp_path)
    optimizer = JobOptimizationService(service)
    job, run_id = _schedule(service, optimizer)
    before = service.repository.get(job["job_id"])

    with pytest.raises(InvalidTransitionError, match=r"minute 2880"):
        service.postpone_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=run_id,
            reason="Beyond the default two-day horizon",
            selected_date="2026-09-12",
        )

    assert service.repository.get(job["job_id"]) == before


# ----------------------------------------------------------------------
# B. Explicit multi-day JobService - widening plumbed through.
# ----------------------------------------------------------------------


def test_explicit_horizon_job_has_matching_original_latest_end(
    tmp_path: Path,
):
    service = _service(tmp_path, horizon_minutes=2880)
    job = _report(service)

    assert job["block_candidate"]["latest_end_minute"] == 2880


def test_explicit_horizon_postpone_to_day_two_widens_correctly(
    tmp_path: Path,
):
    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)
    job, run_id = _schedule(service, optimizer)

    assert job["block_candidate"]["latest_end_minute"] == 2880

    result = service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="Defer to day two",
        selected_date="2026-09-11",  # day 2 of a 2-day horizon
    )

    assert result["status"] == "reported"
    assert result["block_candidate"]["earliest_start_minute"] == 1440
    # max(2880, 2880) == 2880 - stays exactly at the deployment horizon.
    assert result["block_candidate"]["latest_end_minute"] >= 2880
    assert result["block_candidate"]["latest_end_minute"] == 2880


# ----------------------------------------------------------------------
# C. CRITICAL regression: stored latest_end_minute has drifted below the
#    current deployment horizon.
# ----------------------------------------------------------------------


def test_critical_regression_stale_1440_widens_to_2880_deployment_horizon(
    tmp_path: Path,
):
    """The job's OWN stored latest_end_minute is (forced to) 1440 - as if
    it predated a horizon widening - while its JobService now runs a
    2880-minute deployment horizon. Postponing to day two must:
      1. NOT be rejected merely because the stale stored value says 1440
         (the admissibility check reads the DEPLOYMENT horizon now);
      2. widen latest_end_minute to the current deployment horizon
         (2880), never leaving it at the stale 1440.
    """

    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)
    job, run_id = _schedule(service, optimizer)

    _force_latest_end_minute(service, job["job_id"], 1440)
    assert (
        service.repository.get(job["job_id"])["block_candidate"][
            "latest_end_minute"
        ]
        == 1440
    )

    # not_before_minute for day 2 resolves to 1440, which is >= the
    # STALE stored latest_end_minute (1440) but < the true deployment
    # horizon (2880) - exactly the case a stored-field check would get
    # wrong.
    result = service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="Postpone across a stale stored horizon boundary",
        selected_date="2026-09-11",
    )

    assert result["status"] == "reported"
    assert result["block_candidate"]["earliest_start_minute"] == 1440
    assert result["block_candidate"]["latest_end_minute"] == 2880
    assert result["block_candidate"]["latest_end_minute"] != 1440

    # The structural guarantee the whole fix exists for: the job's own
    # window is non-empty, so it cannot be window_infeasible purely as
    # an artifact of postponement.
    assert (
        result["block_candidate"]["earliest_start_minute"]
        < result["block_candidate"]["latest_end_minute"]
    )


# ----------------------------------------------------------------------
# D. No unnecessary shrink.
# ----------------------------------------------------------------------


def test_postpone_never_shrinks_an_already_wider_window(tmp_path: Path):
    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)
    job, run_id = _schedule(service, optimizer)

    _force_latest_end_minute(service, job["job_id"], 4320)

    result = service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="Postpone within a narrower deployment horizon",
        selected_date="2026-09-11",  # resolves to 1440, well inside 2880
    )

    # max(4320, 2880) == 4320 - untouched.
    assert result["block_candidate"]["latest_end_minute"] == 4320


# ----------------------------------------------------------------------
# E. Boundary behavior - fail-closed unchanged.
# ----------------------------------------------------------------------


def test_boundary_last_valid_date_inside_a_two_day_horizon(tmp_path: Path):
    """Under a 2880-minute (2-day) horizon, day 2's own midnight
    (not_before_minute=1440) is the LAST valid postpone target."""

    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)
    job, run_id = _schedule(service, optimizer)

    result = service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="Last valid boundary date",
        selected_date="2026-09-11",
    )

    assert result["block_candidate"]["earliest_start_minute"] == 1440


def test_boundary_date_beyond_the_deployment_horizon_fails_closed(
    tmp_path: Path,
):
    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)
    job, run_id = _schedule(service, optimizer)
    before = service.repository.get(job["job_id"])

    with pytest.raises(InvalidTransitionError, match=r"minute 2880"):
        service.postpone_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=run_id,
            reason="One day past a 2-day deployment horizon",
            selected_date="2026-09-12",  # resolves to minute 2880 == horizon_minutes
        )

    assert service.repository.get(job["job_id"]) == before
    assert _last_event(service, job["job_id"]).event_type is E.TRANSITION_REJECTED


def test_boundary_date_before_the_horizon_start_fails_closed(tmp_path: Path):
    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)
    job, run_id = _schedule(service, optimizer)
    before = service.repository.get(job["job_id"])

    with pytest.raises(InvalidTransitionError):
        service.postpone_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=run_id,
            reason="Before the horizon even starts",
            selected_date="2026-09-09",
        )

    assert service.repository.get(job["job_id"]) == before


# ----------------------------------------------------------------------
# F. Stale-proposal / concurrency safety - untouched.
# ----------------------------------------------------------------------


def test_stale_proposal_run_id_still_rejected_under_widened_horizon(
    tmp_path: Path,
):
    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)
    job, first_run = _schedule(service, optimizer)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)  # new run

    before = service.repository.get(job["job_id"])

    with pytest.raises(StaleProposalError):
        service.postpone_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=first_run,
            reason="Stale postpone attempt",
            selected_date="2026-09-11",
        )

    after = service.repository.get(job["job_id"])
    assert after == before
    assert _last_event(service, job["job_id"]).event_type is E.TRANSITION_REJECTED


def test_missing_expected_run_id_still_rejected(tmp_path: Path):
    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)
    job, run_id = _schedule(service, optimizer)

    with pytest.raises(ValueError):
        service.postpone_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id="",
            reason="x",
            selected_date="2026-09-11",
        )


# ----------------------------------------------------------------------
# G. Event/history metadata.
# ----------------------------------------------------------------------


def test_postpone_event_metadata_carries_required_and_widening_facts(
    tmp_path: Path,
):
    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)
    job, run_id = _schedule(service, optimizer)
    _force_latest_end_minute(service, job["job_id"], 1440)

    original_start = job["schedule_start_minute"]
    original_end = job["schedule_end_minute"]

    result = service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="Crew unavailable until day two",
        selected_date="2026-09-11",
    )

    postponed = _last_event(service, job["job_id"])
    assert postponed.event_type is E.PROPOSAL_POSTPONED
    assert postponed.actor.actor_id == "AUTHORITY-401"
    assert postponed.reason == "Crew unavailable until day two"
    assert postponed.occurred_at

    meta = postponed.metadata
    assert meta["selected_date"] == "2026-09-11"
    assert meta["not_before_minute"] == 1440
    assert meta["original_proposal_run_id"] == run_id
    assert meta["original_start_minute"] == original_start
    assert meta["original_end_minute"] == original_end
    assert meta["expected_proposal_run_id"] == run_id
    assert isinstance(meta["proposal_digest"], str) and len(meta["proposal_digest"]) == 64

    # New Step 3 facts.
    assert meta["original_latest_end_minute"] == 1440
    assert meta["widened_latest_end_minute"] == 2880

    # And the persisted job state itself carries the widened value -
    # not just the event's own metadata.
    assert result["block_candidate"]["latest_end_minute"] == 2880


def test_no_mutation_bypass_stale_postpone_leaves_job_byte_identical(
    tmp_path: Path,
):
    """A REFUSED postpone (stale run id) must leave every column,
    including block_candidate_json, untouched - no partial write."""

    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)
    job, first_run = _schedule(service, optimizer)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    before = service.repository.get(job["job_id"])

    with pytest.raises(StaleProposalError):
        service.postpone_proposal(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=first_run,
            reason="Must not partially apply",
            selected_date="2026-09-11",
        )

    assert service.repository.get(job["job_id"]) == before


# ----------------------------------------------------------------------
# H. Existing authority lifecycle: postpone -> reoptimize, reject/
#    approve untouched, and a genuine end-to-end multi-day reschedule.
# ----------------------------------------------------------------------


def test_postpone_to_horizon_start_date_then_reoptimize_still_schedules(
    tmp_path: Path,
):
    """The same-day postpone-then-reoptimize guarantee
    (test_postpone_to_horizon_start_date_can_still_be_scheduled in
    test_slice3_proposal_review.py) must survive under an explicit
    multi-day JobService too."""

    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)
    job, run_id = _schedule(service, optimizer)

    service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_id,
        reason="No-op postpone to the horizon's own start date",
        selected_date="2026-09-10",
    )

    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    assert job["job_id"] in {s["job_id"] for s in outcome["scheduled"]}


def test_reject_and_approve_unaffected_by_the_postpone_change(tmp_path: Path):
    """Smoke test: plan_reject / plan_commit take no horizon_minutes
    parameter and must be entirely unaffected by Step 3."""

    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)

    job_a, run_a = _schedule(service, optimizer, distance_start=1000.0)
    rejected = service.reject_proposal(
        job_a["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=run_a,
        reason="Unrelated to postpone widening",
    )
    assert rejected["status"] == "reported"

    job_b, run_b = _schedule(service, optimizer, distance_start=5000.0)
    approved = service.approve_proposal(
        job_b["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_b
    )
    assert approved["status"] == "notified"
    assert approved["block_candidate"]["status"] == "COMMITTED"


def test_full_postpone_reoptimize_approve_cycle_widens_and_completes(
    tmp_path: Path,
):
    """End to end: report -> optimize -> postpone (widening applied) ->
    reoptimize -> approve, mirroring
    test_full_reject_reoptimize_approve_cycle in
    test_slice3_proposal_review.py but for postpone."""

    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)
    job, first_run = _schedule(service, optimizer)

    service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=first_run,
        reason="Defer to day two",
        selected_date="2026-09-11",
    )

    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    second_run = outcome["optimization_run_id"]
    assert second_run != first_run

    scheduled_job_ids = {s["job_id"] for s in outcome["scheduled"]}

    if job["job_id"] in scheduled_job_ids:
        final = service.approve_proposal(
            job["job_id"], actor=AUTHORITY, expected_proposal_run_id=second_run
        )
        assert final["status"] == "notified"
        assert final["block_candidate"]["status"] == "COMMITTED"
        assert final["schedule_start_minute"] >= 1440
    else:
        # CORRIDOR_A's GENERATED_STATIC_SLOTS possession path only
        # offers day-1 slots regardless of horizon_minutes (a separate,
        # lower-priority item - SLICE4_MULTIDAY_SCHEDULING_DESIGN.md
        # Sec.5/Sec.7.4 - not part of Step 3). The postponed job must
        # still be REFUSED for a possession/capacity reason, never
        # because its own window became empty - proving the widening
        # fix did its job even when day-2 possession coverage is a
        # separate, not-yet-addressed limitation.
        current = service.repository.get(job["job_id"])
        assert current["status"] == "reported"
        assert current["last_refusal_reason"] is not None
        assert "does not fit within earliest_start_minute" not in (
            current["last_refusal_reason"] or ""
        )


# ----------------------------------------------------------------------
# H (continued). Genuine end-to-end multi-day reschedule through the
# CANONICAL timetable path, where real day-2 possession coverage exists
# (both days are explicitly supplied - no TimetableCoverage gap here,
# Step 4's concern).
# ----------------------------------------------------------------------


STATIONS = [
    {"station_id": "AAA", "name": "Alpha", "km": 0.0},
    {"station_id": "BBB", "name": "Bravo", "km": 20.0},
]
TRACK_IDS = ("UP-1", "DOWN-1")
CORRIDOR_ID = "STEP3_CANONICAL_CORRIDOR"


def _canonical_tracks() -> List[TrackSegment]:
    return [
        TrackSegment(
            track_id=track_id,
            corridor_id=CORRIDOR_ID,
            segment_name=f"SEG-{CORRIDOR_ID}-{track_id}",
            section_name=f"{track_id} main",
            direction="UP" if track_id.startswith("UP") else "DOWN",
            km_start=0.0,
            km_end=20.0,
        )
        for track_id in TRACK_IDS
    ]


def _stop(station_id: str, clock: str) -> Dict[str, Any]:
    return {
        "station_id": station_id,
        "scheduled_arrival": clock,
        "scheduled_departure": clock,
    }


def _train(train_number: str, service_date: str) -> Dict[str, Any]:
    return {
        "train_number": train_number,
        "track_assignment": "UP-1",
        "direction": "UP",
        "service_date": service_date,
        "station_times": [_stop("AAA", "08:00"), _stop("BBB", "08:30")],
    }


def _canonical_dataset() -> CorridorDataset:
    """A hand-built two-day timetable: one train on day 1, one train on
    day 2, both fully covered by real data (no coverage gap - Step 1's
    TimetableCoverage is not wired in yet, and none is needed here since
    every day the horizon touches genuinely has data)."""

    tracks = _canonical_tracks()

    return CorridorDataset(
        corridor=Corridor(
            corridor_id=CORRIDOR_ID,
            name=CORRIDOR_ID,
            tracks=tracks,
            assets=CorridorDataGenerator(seed=42).generate_assets(
                tracks, num_assets_per_track=4
            ),
        ),
        topology=CorridorTopology.from_stations(STATIONS),
        registry=SectionRegistry.from_stations(
            CORRIDOR_ID, STATIONS, track_ids=[t.track_id for t in tracks]
        ),
        timetable_records=(
            _train("T-DAY1", "2026-09-10"),
            _train("T-DAY2", "2026-09-11"),
        ),
    )


def test_end_to_end_postpone_across_days_with_real_multiday_possession(
    tmp_path: Path,
):
    """The product requirement, proven against a real solve: worker
    report -> optimizer -> proposal -> authority postpones to day 2 ->
    optimizer runs again -> a NEW proposal ON/AFTER day 2 is produced,
    because day 2's possession data genuinely exists in this scenario.
    """

    dataset = _canonical_dataset()
    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        dataset=dataset,
        horizon_minutes=2880,
    )
    optimizer = JobOptimizationService(service)

    job = service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="ROUTINE_INSPECTION",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Multi-day postpone end-to-end check",
        ),
        actor=WORKER,
    )

    first_run = optimizer.optimize_corridor(CORRIDOR_ID, actor=ENGINEER)[
        "optimization_run_id"
    ]

    first_state = service.repository.get(job["job_id"])
    assert first_state["status"] == "scheduled", first_state.get(
        "last_refusal_reason"
    )

    service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=first_run,
        reason="Defer past the day-1 train movement window",
        selected_date="2026-09-11",
    )

    postponed_state = service.repository.get(job["job_id"])
    assert postponed_state["status"] == "reported"
    assert postponed_state["block_candidate"]["earliest_start_minute"] == 1440
    assert postponed_state["block_candidate"]["latest_end_minute"] == 2880

    second_outcome = optimizer.optimize_corridor(CORRIDOR_ID, actor=ENGINEER)
    second_run = second_outcome["optimization_run_id"]
    assert second_run != first_run

    final_state = service.repository.get(job["job_id"])
    assert final_state["status"] == "scheduled", final_state.get(
        "last_refusal_reason"
    )
    # Genuinely placed on/after day 2 - not merely "not window_infeasible"
    # but ACTUALLY scheduled past the postponed floor.
    assert final_state["schedule_start_minute"] >= 1440

    proposal = service.current_proposal(job["job_id"])
    assert proposal.optimization_run_id == second_run
    assert proposal.start_minute >= 1440
