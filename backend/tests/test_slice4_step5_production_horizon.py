"""Slice 4 Step 5: widening the production optimization horizon.

Steps 1-4 (timetable coverage foundation, JobService horizon plumbing,
postpone window widening, timetable coverage gate) are unchanged by this
step and are not retested here beyond what is needed to prove the
production default's new value is safe - see test_timetable_coverage.py,
test_jobs_service_horizon_plumbing.py, test_slice4_postpone_window_
widening.py and test_slice4_timetable_coverage_gate.py for their own
dedicated suites.

THIS STEP'S ONLY PRODUCTION CHANGE
    backend.app.jobs.service.OPTIMIZATION_HORIZON_MINUTES: 1440 -> 2880.
    Nothing else in production code changed. In particular:
      - BlockCandidate.latest_end_minute's CONTRACT-LEVEL default stays
        1440 (contracts/schemas.py, untouched) - see
        test_jobs_service_horizon_plumbing.py's own pinned test for that.
      - The solver (backend.app.optimizer.solver) is untouched.
      - The Step 4 timetable coverage gate is untouched and is exactly
        what makes this widening safe: a horizon that now spans two
        calendar days over a timetable that only describes one fails
        closed instead of silently manufacturing possession for the
        undescribed day.

WHY 2880 AND NOT 4320/10080
    Measured directly (see the Step 5 report for the full numbers): CP-
    SAT solve time on this solver's fixed 10-second budget grows steeply
    with (horizon days x concurrent active job count). At the checked-in
    "realistic" fixture's own job volume (15 concurrent jobs), 2880
    stayed OPTIMAL with a wide safety margin (well under 1s up to 25
    jobs, ~50 jobs before approaching the budget). 4320 and 10080 both
    degraded to FEASIBLE-not-OPTIMAL (i.e. the solver timed out rather
    than proving optimality) at job counts only ~2x that realistic
    volume (25-30 jobs). test_production_default_synthetic_multiday_
    optimization_within_solver_budget below reproduces the SAFE end of
    that evidence at the chosen default; it deliberately does not probe
    the unsafe end of the same curve inside the regular suite, which
    would either be flaky (near the 10s cutoff) or slow.

This suite deliberately does NOT modify the checked-in dataset, does NOT
add synthetic timetable days to it, and does NOT touch the generated-
slots (day-1-only) limitation - that remains Step 6's job.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from backend.app.data.canonical_train import CorridorTopology
from backend.app.data.corridor_dataset import CorridorDataset, load_corridor_dataset
from backend.app.data.generator import CorridorDataGenerator
from backend.app.data.models import Corridor, TrackSegment
from backend.app.data.section_registry import SectionRegistry
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import (
    OPTIMIZATION_HORIZON_START,
    JobService,
    TimetableCoverageGapError,
)
from contracts import BlockCandidate


WORKER = human_actor("WORKER-501-S5", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-501-S5", ActorRole.ENGINEER)

CHECKED_IN_CORRIDOR_ID = "CORR-NDLS-AGC"


# ----------------------------------------------------------------------
# A. Production default is now 2880, not 1440.
# ----------------------------------------------------------------------


def test_production_default_is_2880_minutes():
    from backend.app.jobs.service import OPTIMIZATION_HORIZON_MINUTES

    assert OPTIMIZATION_HORIZON_MINUTES == 2880


def test_bare_job_service_picks_up_the_2880_default(tmp_path: Path):
    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))

    assert service.horizon_minutes == 2880


# ----------------------------------------------------------------------
# B/C. Explicit overrides still work in both directions.
# ----------------------------------------------------------------------


def test_explicit_override_1440_still_narrows_to_a_single_day(
    tmp_path: Path,
):
    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"), horizon_minutes=1440
    )

    assert service.horizon_minutes == 1440

    job = service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Explicit 1440 override",
        ),
        actor=WORKER,
    )
    assert job["block_candidate"]["latest_end_minute"] == 1440


def test_explicit_override_2880_matches_the_new_default(tmp_path: Path):
    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"), horizon_minutes=2880
    )

    assert service.horizon_minutes == 2880


# ----------------------------------------------------------------------
# D. Contract-level BlockCandidate.latest_end_minute default is untouched.
# ----------------------------------------------------------------------


def test_contract_level_latest_end_minute_default_is_still_1440():
    block = BlockCandidate(
        block_id="BLK-S5-CONTRACT-CHECK",
        asset_id="A-1",
        track_id="UP-1",
        work_type="BALLAST_TAMPING",
        duration_minutes=60,
    )

    assert block.latest_end_minute == 1440


# ----------------------------------------------------------------------
# H/I/J. Checked-in dataset vs the widened production default.
#
# INTENTIONALLY INVERTED BY SLICE 4 STEP 7. When written, the checked-in
# dataset covered only 2026-09-10, so these tests proved the bare 2880
# default failed closed against it. Step 7 extended that synthetic
# dataset to 2026-09-10 and 2026-09-11, so the bare default is now fully
# covered and optimizes normally. The fail-closed guarantee is preserved
# one day out: a 3-day horizon leaves 2026-09-12 uncovered.
# ----------------------------------------------------------------------


def _report_default_horizon_job(service: JobService) -> Dict[str, Any]:
    return service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Step 5 default-horizon coverage check",
        ),
        actor=WORKER,
    )


def test_checked_in_dataset_optimizes_under_the_bare_production_default(
    tmp_path: Path,
):
    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        dataset=load_corridor_dataset(),
    )
    assert service.horizon_minutes == 2880  # the real default, not an override

    job = _report_default_horizon_job(service)

    outcome = JobOptimizationService(service).optimize_corridor(
        CHECKED_IN_CORRIDOR_ID, actor=ENGINEER
    )

    assert outcome["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert job["job_id"] in {s["job_id"] for s in outcome["scheduled"]}


def test_checked_in_dataset_still_fails_closed_one_day_beyond_its_coverage(
    tmp_path: Path,
):
    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        dataset=load_corridor_dataset(),
        horizon_minutes=4320,
    )

    job = _report_default_horizon_job(service)

    with pytest.raises(TimetableCoverageGapError) as excinfo:
        JobOptimizationService(service).optimize_corridor(
            CHECKED_IN_CORRIDOR_ID, actor=ENGINEER
        )

    assert excinfo.value.covered_dates == (date(2026, 9, 10), date(2026, 9, 11))
    assert excinfo.value.uncovered_dates == (date(2026, 9, 12),)

    # No fake proposal, job untouched, and the solver never ran - the
    # same all-or-nothing guarantee test_slice4_timetable_coverage_gate.py
    # proves exhaustively; spot-checked here.
    persisted = service.repository.get(job["job_id"])
    assert persisted["status"] == "reported"
    assert persisted["schedule_start_minute"] is None


def test_http_optimize_jobs_returns_409_for_a_coverage_gap(tmp_path: Path, monkeypatch):
    """Verifies the EXISTING HTTP failure semantics (router.py's own
    TimetableCoverageGapError -> 409 mapping, unchanged by Step 5) still
    fire correctly for a genuine coverage gap. Since Slice 4 Step 7 the
    checked-in dataset covers the 2880 default, so the router service's
    horizon is monkeypatched to 4320 to reach an uncovered date
    (2026-09-12) - the HTTP mapping under test is unchanged.

    The shared app-level router singleton (backend.app.jobs.router) is
    built at import time against the generated CORRIDOR_A corridor (no
    timetable, so the coverage gate never applies to it) - see
    test_jobs_api_workflow.py's own docstring. Rather than rebuild the
    app or touch the real router singleton's persistent attributes
    permanently, this test monkeypatches that ONE existing instance's
    dataset/corridor/registry to the checked-in real dataset for the
    duration of this test only (monkeypatch restores them afterward),
    and points its repository at an isolated tmp_path database so
    nothing here touches jobs.db.
    """

    from backend.app.api.main import app
    from backend.app.jobs import router as router_module

    from backend.app.jobs.history import JobHistoryRepository

    dataset = load_corridor_dataset()
    isolated_repo = JobRepository(tmp_path / "jobs.db")

    # Rebuild history against the isolated repository path too, exactly
    # as JobService.__init__ does, so history writes land in the same
    # tmp file as the job row itself.
    monkeypatch.setattr(router_module.service, "repository", isolated_repo)
    monkeypatch.setattr(
        router_module.service, "history", JobHistoryRepository(isolated_repo.db_path)
    )
    monkeypatch.setattr(router_module.service, "dataset", dataset)
    monkeypatch.setattr(router_module.service, "corridor", dataset.corridor)
    monkeypatch.setattr(router_module.service, "registry", dataset.registry)
    assert router_module.service.horizon_minutes == 2880  # the real default
    monkeypatch.setattr(router_module.service, "horizon_minutes", 4320)

    client = TestClient(app)

    create_response = client.post(
        "/jobs",
        json={
            "track_id": "UP-1",
            "job_type": "BALLAST_TAMPING",
            "distance_start": 1000.0,
            "distance_end": 1400.0,
            "workers_min": 2,
            "workers_max": 4,
            "description": "HTTP coverage-gap check",
        },
    )
    assert create_response.status_code == 201, create_response.text

    response = client.post(f"/corridors/{CHECKED_IN_CORRIDOR_ID}/optimize-jobs")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "2026-09-12" in detail
    assert "coverage" in detail.lower()


# ----------------------------------------------------------------------
# K. A genuinely multi-day, fully-covered canonical timetable proceeds
# through the pipeline at the BARE production default - a small
# explicit fixture, per the task's own instruction, not a change to the
# checked-in demo dataset.
# ----------------------------------------------------------------------


STATIONS = [
    {"station_id": "AAA", "name": "Alpha", "km": 0.0},
    {"station_id": "BBB", "name": "Bravo", "km": 20.0},
]
TRACK_IDS = ("UP-1", "DOWN-1")
COVERED_CORRIDOR_ID = "STEP5_FULLY_COVERED_CORRIDOR"


def _tracks() -> list[TrackSegment]:
    return [
        TrackSegment(
            track_id=track_id,
            corridor_id=COVERED_CORRIDOR_ID,
            segment_name=f"SEG-{COVERED_CORRIDOR_ID}-{track_id}",
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


def _fully_covered_two_day_dataset() -> CorridorDataset:
    tracks = _tracks()

    return CorridorDataset(
        corridor=Corridor(
            corridor_id=COVERED_CORRIDOR_ID,
            name=COVERED_CORRIDOR_ID,
            tracks=tracks,
            assets=CorridorDataGenerator(seed=42).generate_assets(
                tracks, num_assets_per_track=4
            ),
        ),
        topology=CorridorTopology.from_stations(STATIONS),
        registry=SectionRegistry.from_stations(
            COVERED_CORRIDOR_ID, STATIONS, track_ids=[t.track_id for t in tracks]
        ),
        timetable_records=(
            _train("T-DAY1", "2026-09-10"),
            _train("T-DAY2", "2026-09-11"),
        ),
    )


def test_fully_covered_multiday_timetable_proceeds_at_the_bare_default(
    tmp_path: Path,
):
    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        dataset=_fully_covered_two_day_dataset(),
    )
    assert service.horizon_minutes == 2880  # bare default, not an override

    job = service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="ROUTINE_INSPECTION",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Fully covered multi-day check",
        ),
        actor=WORKER,
    )

    outcome = JobOptimizationService(service).optimize_corridor(
        COVERED_CORRIDOR_ID, actor=ENGINEER
    )

    assert outcome["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert job["job_id"] in {s["job_id"] for s in outcome["scheduled"]}


# ----------------------------------------------------------------------
# L. Horizon boundary behavior at the new default: first required date,
# final required date, exactly-at-horizon, beyond-horizon.
# ----------------------------------------------------------------------


def test_boundary_first_required_date_is_the_horizon_start_date(tmp_path: Path):
    from backend.app.data.timetable_coverage import horizon_calendar_dates

    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))
    dates = horizon_calendar_dates(OPTIMIZATION_HORIZON_START, service.horizon_minutes)

    assert dates[0] == date(2026, 9, 10)


def test_boundary_final_required_date_is_one_day_before_horizon_end(
    tmp_path: Path,
):
    from backend.app.data.timetable_coverage import horizon_calendar_dates

    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))
    dates = horizon_calendar_dates(OPTIMIZATION_HORIZON_START, service.horizon_minutes)

    # 2880 minutes = exactly 2 calendar days from midnight: 09-10, 09-11.
    assert dates == [date(2026, 9, 10), date(2026, 9, 11)]


def test_boundary_exactly_at_horizon_end_postpone_fails_closed(tmp_path: Path):
    """not_before_minute == horizon_minutes (2880) is the first minute
    OUTSIDE [0, horizon_minutes) - must fail closed, not be accepted as
    a boundary-inclusive value."""

    from backend.app.identity.actor import ActorRole, human_actor
    from backend.app.jobs.lifecycle import InvalidTransitionError

    authority = human_actor("AUTHORITY-501-S5", ActorRole.AUTHORITY)

    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))
    optimizer = JobOptimizationService(service)

    job = service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Boundary check",
        ),
        actor=WORKER,
    )
    run_id = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)[
        "optimization_run_id"
    ]

    with pytest.raises(InvalidTransitionError, match=r"minute 2880"):
        service.postpone_proposal(
            job["job_id"],
            actor=authority,
            expected_proposal_run_id=run_id,
            reason="Exactly at the horizon boundary",
            selected_date="2026-09-12",  # resolves to minute 2880
        )


def test_boundary_one_minute_before_horizon_end_is_accepted(tmp_path: Path):
    """The LAST valid postpone target under the 2880-minute default is
    day 2's own midnight (minute 1440), well inside [0, 2880)."""

    from backend.app.identity.actor import ActorRole, human_actor

    authority = human_actor("AUTHORITY-501-S5B", ActorRole.AUTHORITY)

    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))
    optimizer = JobOptimizationService(service)

    job = service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Boundary check",
        ),
        actor=WORKER,
    )
    run_id = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)[
        "optimization_run_id"
    ]

    result = service.postpone_proposal(
        job["job_id"],
        actor=authority,
        expected_proposal_run_id=run_id,
        reason="Last valid date inside the default horizon",
        selected_date="2026-09-11",
    )

    assert result["block_candidate"]["earliest_start_minute"] == 1440
    assert result["block_candidate"]["latest_end_minute"] == 2880


# ----------------------------------------------------------------------
# Generated-slots path.
#
# INTENTIONALLY INVERTED BY SLICE 4 STEP 6 (not silently left red - see
# SLICE4_MULTIDAY_SCHEDULING_DESIGN.md Sec.11's own pattern for this
# kind of update). These two tests originally documented the Step 6
# limitation Step 5 deliberately left unsolved: CORRIDOR_A's generated
# slots offered only day 1 regardless of horizon_minutes, so widening
# the production default to 2880 could not accidentally make the
# generated path APPEAR to have multi-day coverage. Step 6 removed that
# limitation (backend.app.data.generator.CorridorDataGenerator.
# generate_possession_windows now repeats the same 3 slots once per
# calendar day the horizon spans), so the claims these tests made no
# longer hold and are replaced with their Step-6-correct counterparts
# below - the underlying safety property (no fake schedule, no coverage
# gate applied to a path that has no coverage concept) is unchanged.
# ----------------------------------------------------------------------


def test_generated_slots_now_offer_two_days_of_windows_at_the_new_default(
    tmp_path: Path,
):
    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))
    assert service.horizon_minutes == 2880

    windows = service.possession_windows()  # bare default horizon

    # 3 slots/day x 2 tracks x 2 days = 12; every window still fits
    # entirely inside [0, 2880), and day 2's windows are genuinely >=
    # 1440 (absolute minutes), not day-1 clock values.
    assert len(windows) == 12
    for window in windows:
        assert 0 <= window.start_minute < window.end_minute <= 2880

    day_two = [w for w in windows if w.start_minute >= 1440]
    assert len(day_two) == 6
    assert {w.window_id for w in day_two} == {
        "POS-UP-1-NIGHT-D1",
        "POS-UP-1-MIDDAY-D1",
        "POS-UP-1-EVENING-D1",
        "POS-DOWN-1-NIGHT-D1",
        "POS-DOWN-1-MIDDAY-D1",
        "POS-DOWN-1-EVENING-D1",
    }


def test_generated_slots_job_postponed_to_day_two_is_now_schedulable(
    tmp_path: Path,
):
    """Step 6 regression: a job postponed to day 2 under the widened
    default now FINDS a possession window on CORRIDOR_A (day 2's
    generated slots exist) and gets genuinely scheduled there - not
    merely 'not window_infeasible' (Step 3's own guarantee) but actually
    placed. This must still never raise TimetableCoverageGapError - the
    generated-slots path has no coverage concept at all - and must never
    be scheduled on day 1 (that would silently ignore the postponement).
    """

    from backend.app.identity.actor import ActorRole, human_actor

    authority = human_actor("AUTHORITY-501-S5C", ActorRole.AUTHORITY)

    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))
    optimizer = JobOptimizationService(service)

    job = service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Generated-slots day-2 postpone",
        ),
        actor=WORKER,
    )
    run_id = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)[
        "optimization_run_id"
    ]

    service.postpone_proposal(
        job["job_id"],
        actor=authority,
        expected_proposal_run_id=run_id,
        reason="Defer to day two - now has possession coverage",
        selected_date="2026-09-11",
    )

    # Reoptimizing must NOT raise TimetableCoverageGapError - the
    # generated path has no coverage concept - and must now genuinely
    # place the job on/after day 2.
    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    current = service.repository.get(job["job_id"])
    assert job["job_id"] in {s["job_id"] for s in outcome["scheduled"]}
    assert current["status"] == "scheduled"
    assert current["schedule_start_minute"] >= 1440


# ----------------------------------------------------------------------
# M. Performance/load evidence at the chosen production default.
#
# Uses a FULLY-COVERED two-day fixture so the measurement exercises
# realistic possession/candidate volume rather than a 1-2 train hand
# fixture, and is not accidentally measuring TimetableCoverageGapError
# being raised in microseconds instead of an actual solve. Originally
# synthesized in memory from the one-day checked-in trains; since Slice
# 4 Step 7 the checked-in dataset itself covers both dates (24 synthetic
# trains per date), so it is used directly.
#
# No brittle wall-clock threshold: the only hard assertion is against
# the solver's OWN configured 10-second budget
# (backend.app.optimizer.solver._SOLVE_TIME_LIMIT_SECONDS), which this
# suite does not change, plus solver_status - never a "must complete in
# under N ms" assertion tied to this machine's speed.
# ----------------------------------------------------------------------


def test_production_default_synthetic_multiday_optimization_within_solver_budget(
    tmp_path: Path,
):
    dataset = load_corridor_dataset()
    assert len(dataset.timetable_records) == 48

    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        dataset=dataset,
    )
    assert service.horizon_minutes == 2880  # the real default

    for i in range(15):  # matches the checked-in "realistic" fixture's
        # own concurrent-job volume (15 maintenance_jobs entries).
        service.create_job(
            JobCreateRequest(
                track_id="UP-1",
                job_type="BALLAST_TAMPING",
                distance_start=1000.0 + i * 500,
                distance_end=1300.0 + i * 500,
                workers_min=2,
                workers_max=4,
                description=f"Load-evidence job {i}",
            ),
            actor=WORKER,
        )

    optimizer = JobOptimizationService(service)

    started = time.perf_counter()
    outcome = optimizer.optimize_corridor(dataset.corridor_id, actor=ENGINEER)
    elapsed = time.perf_counter() - started

    assert outcome["solver_status"] == "OPTIMAL"
    assert outcome["solve_time_seconds"] < 9.5  # comfortably under the
    # solver's own fixed 10-second budget - not a brittle micro-timing
    # assertion, a check against this deployment's own configured cap.
    assert outcome["counts"]["considered"] == 15
    assert elapsed < 15  # generous smoke bound on the WHOLE pipeline,
    # not just solve() - possession derivation, job creation, etc.
