"""Slice 4 Step 2: JobService horizon plumbing.

JobService.horizon_minutes (new, Slice 4 Step 2) is the ONE instance
attribute that now governs every jobs-pipeline location that used to
read the module constant OPTIMIZATION_HORIZON_MINUTES directly:

  - create_job's BlockCandidate.latest_end_minute
  - possession_inputs()/possession_windows()'s own horizon_minutes
    default (both the canonical and generated-slots branches)
  - JobOptimizationService.optimize_corridor's OptimizationRequest.
    horizon_minutes

This suite proves the plumbing, not new scheduling behavior: whatever
OPTIMIZATION_HORIZON_MINUTES currently is gets threaded through every
one of those call sites by default, and an explicit
JobService(horizon_minutes=2880) still overrides it everywhere. Nothing
here wires TimetableCoverage (Step 1) in, and nothing here touches
lifecycle.plan_postpone's own admissibility bound (still Slice 4 Step
3's job).

Slice 4 Step 5 changed OPTIMIZATION_HORIZON_MINUTES itself from 1440 to
2880 (see backend.app.jobs.service's own comment on that constant for
why) - the "default" tests below assert against the CURRENT value
(2880), not a value hardcoded independently of the production constant,
so they keep proving "the default plumbing resolves to whatever the
production constant says" rather than pinning a stale number.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import backend.app.jobs.optimization as optimization_module
from backend.app.data.generator import CorridorDataGenerator
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import (
    OPTIMIZATION_HORIZON_MINUTES,
    InvalidHorizonMinutesError,
    JobService,
)
from contracts import BlockCandidate


def _service(tmp_path: Path, **kwargs) -> JobService:
    return JobService(repository=JobRepository(tmp_path / "jobs.db"), **kwargs)


def _report(service: JobService, track_id: str = "UP-1") -> dict:
    return service.create_job(
        JobCreateRequest(
            track_id=track_id,
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Horizon plumbing check",
        )
    )


# ----------------------------------------------------------------------
# 1. Default JobService() behavior matches OPTIMIZATION_HORIZON_MINUTES
#    (2880 as of Slice 4 Step 5 - was 1440 before that step widened the
#    production default; see that constant's own comment).
# ----------------------------------------------------------------------


def test_default_job_service_horizon_minutes_matches_the_production_constant(
    tmp_path: Path,
):
    service = _service(tmp_path)

    assert service.horizon_minutes == OPTIMIZATION_HORIZON_MINUTES
    assert service.horizon_minutes == 2880


def test_default_job_service_create_job_latest_end_minute_matches_the_default(
    tmp_path: Path,
):
    service = _service(tmp_path)

    job = _report(service)

    assert job["block_candidate"]["latest_end_minute"] == OPTIMIZATION_HORIZON_MINUTES
    assert job["block_candidate"]["latest_end_minute"] == 2880


# ----------------------------------------------------------------------
# 2. Explicit JobService(horizon_minutes=2880) passes 2880 through the
#    jobs-pipeline paths.
# ----------------------------------------------------------------------


def test_explicit_horizon_minutes_is_stored_on_the_instance(tmp_path: Path):
    service = _service(tmp_path, horizon_minutes=2880)

    assert service.horizon_minutes == 2880


def test_possession_inputs_default_resolves_to_instance_horizon_minutes(
    tmp_path: Path, monkeypatch
):
    """possession_inputs()'s own horizon_minutes default (None sentinel)
    must resolve to THIS instance's self.horizon_minutes, not to the
    OPTIMIZATION_HORIZON_MINUTES module constant - proven by capturing
    what CorridorDataGenerator.generate_possession_windows actually
    receives, since the generated-slots path's own WINDOW CONTENT is
    horizon-length-invariant (see SLICE4_MULTIDAY_SCHEDULING_DESIGN.md
    Sec.7.4) and so cannot itself be used as evidence of threading.
    """

    captured: dict = {}
    real = CorridorDataGenerator.generate_possession_windows

    def capturing(self, tracks, horizon_minutes=1440, registry=None):
        captured["horizon_minutes"] = horizon_minutes
        return real(self, tracks, horizon_minutes=horizon_minutes, registry=registry)

    monkeypatch.setattr(
        CorridorDataGenerator, "generate_possession_windows", capturing
    )

    service = _service(tmp_path, horizon_minutes=2880)

    service.possession_inputs()  # no explicit horizon_minutes argument

    assert captured["horizon_minutes"] == 2880


def test_possession_inputs_explicit_argument_still_overrides_instance_default(
    tmp_path: Path, monkeypatch
):
    """An explicit caller-supplied horizon_minutes (several existing
    tests do this deliberately) must still win over the instance
    default - the None sentinel only fires when the caller omits it."""

    captured: dict = {}
    real = CorridorDataGenerator.generate_possession_windows

    def capturing(self, tracks, horizon_minutes=1440, registry=None):
        captured["horizon_minutes"] = horizon_minutes
        return real(self, tracks, horizon_minutes=horizon_minutes, registry=registry)

    monkeypatch.setattr(
        CorridorDataGenerator, "generate_possession_windows", capturing
    )

    service = _service(tmp_path, horizon_minutes=2880)

    service.possession_inputs(horizon_minutes=4320)

    assert captured["horizon_minutes"] == 4320


def test_possession_windows_shares_the_same_default_resolution(
    tmp_path: Path, monkeypatch
):
    """possession_windows() delegates to possession_inputs(); its own
    horizon_minutes default must resolve the same way."""

    captured: dict = {}
    real = CorridorDataGenerator.generate_possession_windows

    def capturing(self, tracks, horizon_minutes=1440, registry=None):
        captured["horizon_minutes"] = horizon_minutes
        return real(self, tracks, horizon_minutes=horizon_minutes, registry=registry)

    monkeypatch.setattr(
        CorridorDataGenerator, "generate_possession_windows", capturing
    )

    service = _service(tmp_path, horizon_minutes=2880)

    service.possession_windows()

    assert captured["horizon_minutes"] == 2880


def test_optimize_corridor_threads_instance_horizon_minutes_to_the_request(
    tmp_path: Path, monkeypatch
):
    """End to end: JobOptimizationService.optimize_corridor must build
    the OptimizationRequest with THIS service's horizon_minutes, not
    the module constant - captured directly off the request handed to
    solve(), the same interception technique
    test_slice3_proposal_review.py already uses for its concurrency
    tests."""

    captured: dict = {}
    real_solve = optimization_module.solve

    def capturing_solve(request):
        captured["horizon_minutes"] = request.horizon_minutes
        return real_solve(request)

    monkeypatch.setattr(optimization_module, "solve", capturing_solve)

    service = _service(tmp_path, horizon_minutes=2880)
    optimizer = JobOptimizationService(service)

    _report(service)
    optimizer.optimize_corridor("CORRIDOR_A")

    assert captured["horizon_minutes"] == 2880


def test_optimize_corridor_default_still_threads_the_production_constant(
    tmp_path: Path, monkeypatch
):
    """The default-value counterpart to the test above - proves the
    threading mechanism itself, not just the explicit-override value."""

    captured: dict = {}
    real_solve = optimization_module.solve

    def capturing_solve(request):
        captured["horizon_minutes"] = request.horizon_minutes
        return real_solve(request)

    monkeypatch.setattr(optimization_module, "solve", capturing_solve)

    service = _service(tmp_path)
    optimizer = JobOptimizationService(service)

    _report(service)
    optimizer.optimize_corridor("CORRIDOR_A")

    assert captured["horizon_minutes"] == OPTIMIZATION_HORIZON_MINUTES
    assert captured["horizon_minutes"] == 2880


# ----------------------------------------------------------------------
# 3. Newly created jobs under an explicit 2880 horizon get the correct
#    explicit latest-end value.
# ----------------------------------------------------------------------


def test_explicit_horizon_create_job_gets_matching_latest_end_minute(
    tmp_path: Path,
):
    service = _service(tmp_path, horizon_minutes=2880)

    job = _report(service)

    assert job["block_candidate"]["latest_end_minute"] == 2880
    # earliest_start_minute is untouched by this change.
    assert job["block_candidate"]["earliest_start_minute"] == 0


def test_different_instances_do_not_share_horizon_state(tmp_path: Path):
    """Two JobService instances constructed with different
    horizon_minutes must not leak into each other - there is exactly
    ONE horizon knob, but it is per-instance, not global/module state."""

    narrow = JobService(
        repository=JobRepository(tmp_path / "a.db"), horizon_minutes=1440
    )
    wide = JobService(
        repository=JobRepository(tmp_path / "b.db"), horizon_minutes=4320
    )

    narrow_job = _report(narrow, track_id="UP-1")
    wide_job = _report(wide, track_id="UP-1")

    assert narrow_job["block_candidate"]["latest_end_minute"] == 1440
    assert wide_job["block_candidate"]["latest_end_minute"] == 4320

    # The module constant itself is untouched by either construction.
    assert OPTIMIZATION_HORIZON_MINUTES == 2880


# ----------------------------------------------------------------------
# 4. Contract-level BlockCandidate.latest_end_minute default remains
#    1440 - untouched by this change.
# ----------------------------------------------------------------------


def test_contract_level_block_candidate_default_latest_end_minute_is_unchanged():
    block = BlockCandidate(
        block_id="BLK-DEFAULT-CHECK",
        asset_id="A-1",
        track_id="UP-1",
        work_type="BALLAST_TAMPING",
        duration_minutes=60,
    )

    assert block.latest_end_minute == 1440


# ----------------------------------------------------------------------
# 5. Invalid horizon values are rejected (whole-day / positive policy),
#    per SLICE4_MULTIDAY_SCHEDULING_DESIGN.md Sec.4.3 - a jobs-pipeline
#    policy, not a contract-level rule (see test above).
# ----------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [0, -1, -1440, 1000, 1439, 1441, 2000])
def test_invalid_horizon_minutes_rejected_at_construction(
    tmp_path: Path, bad_value: int
):
    with pytest.raises(InvalidHorizonMinutesError):
        _service(tmp_path, horizon_minutes=bad_value)


@pytest.mark.parametrize("good_value", [1440, 2880, 4320, 10080])
def test_valid_whole_day_horizon_minutes_accepted(
    tmp_path: Path, good_value: int
):
    service = _service(tmp_path, horizon_minutes=good_value)

    assert service.horizon_minutes == good_value


def test_invalid_horizon_minutes_error_is_a_value_error(tmp_path: Path):
    """Consistent with every other construction-time validation in this
    codebase (e.g. Pydantic validators raising ValueError subclasses)."""

    assert issubclass(InvalidHorizonMinutesError, ValueError)
