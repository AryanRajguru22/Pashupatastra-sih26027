"""Slice 4 Step 4: wiring the timetable coverage gate into the jobs/
possession pipeline.

CORE SAFETY PROBLEM (SLICE4_MULTIDAY_SCHEDULING_DESIGN.md Sec.7):
backend.app.data.timetable_adapter.derive_section_possession_windows
derives possession as the COMPLEMENT of recorded train occupation. That
is only correct for a calendar date the timetable actually attempted to
describe - for a date it never mentions at all, "no occupation found"
means "no data", not "no trains", and deriving a wide-open possession
window for it would silently read missing timetable data as free track
capacity.

This suite proves the gate JobService._canonical_possession_inputs now
applies, reusing Step 1's isolated backend.app.data.timetable_coverage
module UNCHANGED (see test_timetable_coverage.py for that module's own
suite - nothing here re-tests its internals):

  A. fully covered single-day horizon - no regression
  B. incomplete multi-day horizon over the REAL checked-in dataset
     (two synthetic service_dates since Slice 4 Step 7, so a 3-day
     horizon leaves 2026-09-12 uncovered) - fails closed
  C. the exact uncovered date(s) are surfaced, derived from the horizon
     (not hardcoded in production code)
  D. multiple uncovered days, in order
  E. a hand-built dataset with genuine multi-day coverage - proceeds
     normally
  F. a record with no service_date at all - fails closed (Step 1's own
     rule, preserved)
  G. a malformed service_date - existing HorizonAnchorError preserved
  H. postpone is NOT gated - only reoptimize is
  I. existing lifecycle/audit semantics are untouched by a coverage
     failure - job preserved, no fake proposal, no audit row
  J. no REAL/LIVE provenance is ever claimed for a failed attempt
  K. the solver is never invoked when coverage fails

Nothing here changes OPTIMIZATION_HORIZON_MINUTES, the solver, contract
defaults, postpone's own admissibility logic (Step 3, untouched), or
the generated-slots (no-timetable) possession path, which this gate
does not apply to at all.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pytest

import backend.app.jobs.optimization as optimization_module
from backend.app.audit.repository import AuditRepository
from backend.app.data.canonical_train import CorridorTopology
from backend.app.data.corridor_dataset import CorridorDataset, load_corridor_dataset
from backend.app.data.generator import CorridorDataGenerator
from backend.app.data.horizon_anchor import HorizonAnchorError
from backend.app.data.models import Corridor, TrackSegment
from backend.app.data.section_registry import SectionRegistry
from backend.app.data.timetable_coverage import horizon_calendar_dates
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.jobs.lifecycle import proposal_run_id_of
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import (
    OPTIMIZATION_HORIZON_START,
    POSSESSION_DERIVATION_CANONICAL_TIMETABLE,
    JobService,
    TimetableCoverageGapError,
)


HORIZON_START = OPTIMIZATION_HORIZON_START  # "2026-09-10T00:00:00+05:30"
CHECKED_IN_CORRIDOR_ID = "CORR-NDLS-AGC"  # carries exactly two synthetic
# service_dates, 2026-09-10 and 2026-09-11, since Slice 4 Step 7 - see
# test_slice4_step7_two_day_demo_dataset.py, which pins that directly.

WORKER = human_actor("WORKER-501", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-501", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-501", ActorRole.AUTHORITY)


# ----------------------------------------------------------------------
# Helpers over the REAL checked-in dataset.
# ----------------------------------------------------------------------


def _checked_in_service(tmp_path: Path, **kwargs) -> JobService:
    return JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        dataset=load_corridor_dataset(),
        **kwargs,
    )


def _report(service: JobService) -> Dict[str, Any]:
    return service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Slice 4 Step 4 coverage gate check",
        ),
        actor=WORKER,
    )


# ----------------------------------------------------------------------
# Helpers for a small, hand-built multi-day-covered dataset (tests E-G).
# ----------------------------------------------------------------------


STATIONS = [
    {"station_id": "AAA", "name": "Alpha", "km": 0.0},
    {"station_id": "BBB", "name": "Bravo", "km": 20.0},
]
TRACK_IDS = ("UP-1", "DOWN-1")
HAND_CORRIDOR_ID = "STEP4_HAND_BUILT_CORRIDOR"


def _hand_tracks() -> List[TrackSegment]:
    return [
        TrackSegment(
            track_id=track_id,
            corridor_id=HAND_CORRIDOR_ID,
            segment_name=f"SEG-{HAND_CORRIDOR_ID}-{track_id}",
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


def _train(train_number: str, service_date) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "train_number": train_number,
        "track_assignment": "UP-1",
        "direction": "UP",
        "station_times": [_stop("AAA", "08:00"), _stop("BBB", "08:30")],
    }
    if service_date is not None:
        record["service_date"] = service_date
    return record


def _hand_dataset(records) -> CorridorDataset:
    tracks = _hand_tracks()

    return CorridorDataset(
        corridor=Corridor(
            corridor_id=HAND_CORRIDOR_ID,
            name=HAND_CORRIDOR_ID,
            tracks=tracks,
            assets=CorridorDataGenerator(seed=42).generate_assets(
                tracks, num_assets_per_track=4
            ),
        ),
        topology=CorridorTopology.from_stations(STATIONS),
        registry=SectionRegistry.from_stations(
            HAND_CORRIDOR_ID, STATIONS, track_ids=[t.track_id for t in tracks]
        ),
        timetable_records=tuple(records),
    )


def _hand_service(tmp_path: Path, records, **kwargs) -> JobService:
    return JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        dataset=_hand_dataset(records),
        **kwargs,
    )


# ----------------------------------------------------------------------
# A. Fully covered single-day horizon - no regression.
# ----------------------------------------------------------------------


def test_a_checked_in_dataset_passes_the_gate_at_a_pinned_single_day_horizon(
    tmp_path: Path,
):
    """A pinned 1440-minute (single-day) horizon requires only 2026-09-10,
    which the checked-in dataset covers - the gate must be a complete
    no-op here.

    horizon_minutes stays pinned so this remains a single-day check
    independent of the production default (2880 since Slice 4 Step 5).
    """

    service = _checked_in_service(tmp_path, horizon_minutes=1440)

    inputs = service.possession_inputs()  # no explicit args

    assert inputs.windows
    assert inputs.derivation == POSSESSION_DERIVATION_CANONICAL_TIMETABLE


def test_a_checked_in_dataset_optimization_still_proposes_normally(
    tmp_path: Path,
):
    """End-to-end: no regression to existing proposal generation, at a
    pinned single-day horizon - see the test above for why this is
    pinned rather than left at the (now wider) production default."""

    service = _checked_in_service(tmp_path, horizon_minutes=1440)
    job = _report(service)

    outcome = JobOptimizationService(service).optimize_corridor(
        CHECKED_IN_CORRIDOR_ID, actor=ENGINEER
    )

    assert outcome["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert outcome["counts"]["scheduled"] == 1

    persisted = service.repository.get(job["job_id"])
    assert persisted["status"] == "scheduled"
    assert persisted["schedule_start_minute"] is not None


def test_a_checked_in_dataset_passes_the_gate_at_the_actual_production_default(
    tmp_path: Path,
):
    """INTENTIONALLY INVERTED BY SLICE 4 STEP 7. This test used to prove
    the BARE production default (2880) failed closed against the
    checked-in dataset, which then covered only 2026-09-10. Step 7
    extended that synthetic dataset to cover 2026-09-10 and 2026-09-11,
    so the bare default is now fully covered and must PASS the gate. The
    fail-closed property itself is preserved one day further out - see
    the test_b_* tests below, now at BEYOND_COVERAGE_HORIZON (4320).
    """

    service = _checked_in_service(tmp_path)  # bare: the real production default
    assert service.horizon_minutes == 2880

    inputs = service.possession_inputs()

    assert inputs.derivation == POSSESSION_DERIVATION_CANONICAL_TIMETABLE
    assert any(w.start_minute >= 1440 for w in inputs.windows)


# ----------------------------------------------------------------------
# B. Incomplete multi-day horizon over the REAL checked-in dataset.
#
# Repointed by Slice 4 Step 7: the checked-in dataset now covers
# 2026-09-10 and 2026-09-11, so the first horizon it does NOT cover is
# three days (4320 minutes), whose third required date 2026-09-12 is
# absent. Every fail-closed guarantee below is unchanged; only the
# horizon that triggers it moved one day out.
# ----------------------------------------------------------------------


BEYOND_COVERAGE_HORIZON = 3 * 1440


def test_b_checked_in_dataset_fails_closed_beyond_its_two_day_coverage(
    tmp_path: Path,
):
    """Widening horizon_minutes past the dataset's covered dates must
    fail closed, never silently derive a possession window for the
    uncovered 2026-09-12."""

    service = _checked_in_service(tmp_path, horizon_minutes=BEYOND_COVERAGE_HORIZON)

    with pytest.raises(TimetableCoverageGapError):
        service.possession_inputs()


def test_b_optimize_corridor_fails_closed_with_no_scheduling(
    tmp_path: Path,
):
    service = _checked_in_service(tmp_path, horizon_minutes=BEYOND_COVERAGE_HORIZON)
    job = _report(service)

    with pytest.raises(TimetableCoverageGapError):
        JobOptimizationService(service).optimize_corridor(
            CHECKED_IN_CORRIDOR_ID, actor=ENGINEER
        )

    persisted = service.repository.get(job["job_id"])
    assert persisted["status"] == "reported"
    assert persisted["schedule_start_minute"] is None


# ----------------------------------------------------------------------
# C. Exact uncovered dates are surfaced, derived mechanically.
# ----------------------------------------------------------------------


def test_c_uncovered_dates_are_exact_and_horizon_derived(tmp_path: Path):
    service = _checked_in_service(tmp_path, horizon_minutes=BEYOND_COVERAGE_HORIZON)

    with pytest.raises(TimetableCoverageGapError) as excinfo:
        service.possession_inputs()

    exc = excinfo.value

    # Cross-checked against the SAME pure utility the production code
    # calls, not a hardcoded literal - proves the dates are mechanically
    # derived from horizon_start/horizon_minutes, not baked into
    # production logic.
    expected_required = horizon_calendar_dates(HORIZON_START, BEYOND_COVERAGE_HORIZON)
    assert exc.required_dates == tuple(expected_required)

    assert exc.covered_dates == (date(2026, 9, 10), date(2026, 9, 11))
    assert exc.uncovered_dates == (date(2026, 9, 12),)

    # The message itself names the uncovered date - human-readable
    # structured information, not a bare "refused" string.
    assert "2026-09-12" in str(exc)


# ----------------------------------------------------------------------
# D. Multiple uncovered days, in order.
# ----------------------------------------------------------------------


def test_d_multiple_uncovered_days_reported_deterministically(
    tmp_path: Path,
):
    service = _checked_in_service(tmp_path, horizon_minutes=4 * 1440)

    with pytest.raises(TimetableCoverageGapError) as excinfo:
        service.possession_inputs()

    assert excinfo.value.uncovered_dates == (
        date(2026, 9, 12),
        date(2026, 9, 13),
    )
    assert excinfo.value.covered_dates == (date(2026, 9, 10), date(2026, 9, 11))


# ----------------------------------------------------------------------
# E. Coverage complete (hand-built, genuine multi-day data) - proceeds
#    normally.
# ----------------------------------------------------------------------


def test_e_genuinely_covered_multiday_timetable_proceeds_normally(
    tmp_path: Path,
):
    records = [
        _train("T-DAY1", "2026-09-10"),
        _train("T-DAY2", "2026-09-11"),
    ]

    service = _hand_service(tmp_path, records, horizon_minutes=2880)

    inputs = service.possession_inputs()  # must not raise

    assert inputs.windows
    assert inputs.derivation == POSSESSION_DERIVATION_CANONICAL_TIMETABLE

    job = service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="ROUTINE_INSPECTION",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Genuine multi-day coverage check",
        ),
        actor=WORKER,
    )

    outcome = JobOptimizationService(service).optimize_corridor(
        HAND_CORRIDOR_ID, actor=ENGINEER
    )

    assert outcome["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert job["job_id"] in {s["job_id"] for s in outcome["scheduled"]}


# ----------------------------------------------------------------------
# F. Missing service_date - Step 1's fail-closed rule preserved.
# ----------------------------------------------------------------------


def test_f_record_with_no_service_date_contributes_no_coverage(
    tmp_path: Path,
):
    """A record that never states a service_date asserts no date at all
    (backend.app.data.timetable_coverage.covered_service_dates) - it
    cannot be read as covering the horizon's required date."""

    records = [_train("NO_DATE", None)]

    # Pinned to a single-day horizon: this test's own claim (a
    # dateless record contributes zero coverage) is horizon-length
    # independent, but the asserted uncovered_dates tuple below names
    # exactly one date, which requires a single-day horizon regardless
    # of what JobService's own production default currently is.
    service = _hand_service(tmp_path, records, horizon_minutes=1440)

    with pytest.raises(TimetableCoverageGapError) as excinfo:
        service.possession_inputs()

    assert excinfo.value.covered_dates == ()
    assert excinfo.value.uncovered_dates == (date(2026, 9, 10),)


# ----------------------------------------------------------------------
# G. Malformed service_date - existing validation/error preserved.
# ----------------------------------------------------------------------


def test_g_malformed_service_date_still_raises_horizon_anchor_error(
    tmp_path: Path,
):
    """A record that DOES assert a date, but gets it wrong, must still
    surface as the existing HorizonAnchorError (Step 1's
    covered_service_dates reuses horizon_anchor.parse_service_date
    unchanged) - not silently skipped, and not masked by the new gate."""

    records = [_train("BAD_DATE", "not-a-real-date")]

    service = _hand_service(tmp_path, records)

    with pytest.raises(HorizonAnchorError):
        service.possession_inputs()


def test_g_malformed_service_date_propagates_through_optimize_corridor(
    tmp_path: Path,
):
    records = [_train("BAD_DATE", "not-a-real-date")]
    service = _hand_service(tmp_path, records)
    job = service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="ROUTINE_INSPECTION",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Malformed date propagation check",
        ),
        actor=WORKER,
    )

    with pytest.raises(HorizonAnchorError):
        JobOptimizationService(service).optimize_corridor(
            HAND_CORRIDOR_ID, actor=ENGINEER
        )

    persisted = service.repository.get(job["job_id"])
    assert persisted["status"] == "reported"


# ----------------------------------------------------------------------
# H. Postpone is NOT gated - only reoptimize is.
# ----------------------------------------------------------------------


def test_h_postpone_into_an_uncovered_date_succeeds_reoptimize_fails_closed(
    tmp_path: Path,
):
    """The critical separation: a job first scheduled under a horizon
    the checked-in dataset DOES cover (1440) is postponed, via a SECOND
    JobService sharing the same repository but a WIDER horizon (4320),
    to a date (2026-09-12) that horizon touches but the dataset does NOT
    cover. Postponement must succeed - it never calls possession_inputs
    at all. The very next reoptimization attempt must then fail closed
    with TimetableCoverageGapError, naming exactly that date.

    Repointed by Slice 4 Step 7 (was 2880 / 2026-09-11, which the
    two-day dataset now covers).
    """

    db_path = tmp_path / "jobs.db"

    narrow_service = JobService(
        repository=JobRepository(db_path),
        dataset=load_corridor_dataset(),
        horizon_minutes=1440,
    )
    job = _report(narrow_service)
    first_run = JobOptimizationService(narrow_service).optimize_corridor(
        CHECKED_IN_CORRIDOR_ID, actor=ENGINEER
    )["optimization_run_id"]

    scheduled = narrow_service.repository.get(job["job_id"])
    assert scheduled["status"] == "scheduled"

    wide_service = JobService(
        repository=JobRepository(db_path),
        dataset=load_corridor_dataset(),
        horizon_minutes=BEYOND_COVERAGE_HORIZON,
    )

    # Postpone succeeds: the target date (minute 2880) is inside the WIDE
    # deployment horizon (< 4320) - coverage is never consulted for this
    # operation.
    postponed = wide_service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=first_run,
        reason="Defer into a date the timetable does not yet cover",
        selected_date="2026-09-12",
    )

    assert postponed["status"] == "reported"
    assert postponed["block_candidate"]["earliest_start_minute"] == 2880
    assert postponed["block_candidate"]["latest_end_minute"] == BEYOND_COVERAGE_HORIZON

    # Reoptimizing now must fail closed - the widened window did nothing
    # to manufacture timetable data for 2026-09-12.
    with pytest.raises(TimetableCoverageGapError) as excinfo:
        JobOptimizationService(wide_service).optimize_corridor(
            CHECKED_IN_CORRIDOR_ID, actor=ENGINEER
        )

    assert excinfo.value.uncovered_dates == (date(2026, 9, 12),)

    final = wide_service.repository.get(job["job_id"])
    assert final["status"] == "reported"
    assert final["block_candidate"]["earliest_start_minute"] == 2880


# ----------------------------------------------------------------------
# I. Existing lifecycle/audit semantics untouched by a coverage failure.
# ----------------------------------------------------------------------


def test_i_job_preserved_no_fake_proposal_no_status_corruption(
    tmp_path: Path,
):
    service = _checked_in_service(tmp_path, horizon_minutes=BEYOND_COVERAGE_HORIZON)
    job = _report(service)
    before = service.repository.get(job["job_id"])

    with pytest.raises(TimetableCoverageGapError):
        JobOptimizationService(service).optimize_corridor(
            CHECKED_IN_CORRIDOR_ID, actor=ENGINEER
        )

    after = service.repository.get(job["job_id"])

    assert after["status"] == before["status"] == "reported"
    assert after["schedule_start_minute"] is None
    assert after["schedule_end_minute"] is None
    assert proposal_run_id_of(after) is None  # no fake proposal
    assert after["block_candidate"]["status"] == "PLANNED"


def test_i_no_optimization_run_audit_row_is_created(tmp_path: Path):
    """The failure happens before AuditService.record_run is ever
    called - no optimization_runs row, immutable or otherwise, should
    exist for a coverage-gated attempt."""

    db_path = tmp_path / "jobs.db"
    service = JobService(
        repository=JobRepository(db_path),
        dataset=load_corridor_dataset(),
        horizon_minutes=BEYOND_COVERAGE_HORIZON,
    )
    _report(service)

    audit = AuditRepository(db_path)
    before_count = len(audit.list_all())

    with pytest.raises(TimetableCoverageGapError):
        JobOptimizationService(service).optimize_corridor(
            CHECKED_IN_CORRIDOR_ID, actor=ENGINEER
        )

    after_count = len(audit.list_all())
    assert after_count == before_count == 0


# ----------------------------------------------------------------------
# J. No REAL/LIVE provenance is ever claimed for a failed attempt.
# ----------------------------------------------------------------------


def test_j_coverage_failure_claims_no_real_or_live_provenance(
    tmp_path: Path,
):
    service = _checked_in_service(tmp_path, horizon_minutes=BEYOND_COVERAGE_HORIZON)
    _report(service)

    with pytest.raises(TimetableCoverageGapError) as excinfo:
        JobOptimizationService(service).optimize_corridor(
            CHECKED_IN_CORRIDOR_ID, actor=ENGINEER
        )

    message = str(excinfo.value)
    assert "REAL_SCHEDULED" not in message
    assert "LIVE" not in message

    # No provenance snapshot exists at all for this failed attempt - see
    # test_i_no_optimization_run_audit_row_is_created, which proves no
    # optimization_runs row (the only place a provenance_snapshot_json
    # is written) was created.


# ----------------------------------------------------------------------
# K. The solver is never invoked when coverage fails.
# ----------------------------------------------------------------------


def test_k_solver_never_invoked_when_coverage_fails(tmp_path, monkeypatch):
    solve_calls: list = []
    real_solve = optimization_module.solve

    def counting_solve(request):
        solve_calls.append(request)
        return real_solve(request)

    monkeypatch.setattr(optimization_module, "solve", counting_solve)

    service = _checked_in_service(tmp_path, horizon_minutes=BEYOND_COVERAGE_HORIZON)
    _report(service)

    with pytest.raises(TimetableCoverageGapError):
        JobOptimizationService(service).optimize_corridor(
            CHECKED_IN_CORRIDOR_ID, actor=ENGINEER
        )

    assert solve_calls == []


def test_k_solver_is_invoked_once_when_coverage_is_complete(
    tmp_path, monkeypatch
):
    """Sensitivity check for the test above: the SAME instrumentation,
    against a fully-covered horizon, must show exactly one solve() call
    - proving the absence in the failing case is caused by the gate,
    not by the monkeypatch itself never firing."""

    solve_calls: list = []
    real_solve = optimization_module.solve

    def counting_solve(request):
        solve_calls.append(request)
        return real_solve(request)

    monkeypatch.setattr(optimization_module, "solve", counting_solve)

    # Pinned to a single-day horizon (fully covered by the dataset) -
    # see test_a_*'s own comment for why this is pinned rather than left
    # at JobService's own (now wider) production default.
    service = _checked_in_service(tmp_path, horizon_minutes=1440)
    _report(service)

    JobOptimizationService(service).optimize_corridor(
        CHECKED_IN_CORRIDOR_ID, actor=ENGINEER
    )

    assert len(solve_calls) == 1
