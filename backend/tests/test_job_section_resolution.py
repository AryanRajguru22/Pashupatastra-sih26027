"""Sprint 3 Step 11: section-aware maintenance job resource resolution.

Step 10 wired the canonical timetable/possession path into the jobs
pipeline and, in doing so, exposed a real gap: JobService-created jobs
carried section_id=None, so the solver's canonical (track_id, section_id)
resource matching fell back to matching by track_id alone
(solver._possession_window_covers_block) - harmless on a single-section
synthetic corridor, unsafe on the six-section dataset corridor Step 10
wired in.

This file covers:
  - backend.app.data.section_registry.SectionRegistry.resolve_by_chainage
    (the new, narrow, geographic km -> Section lookup, plus its
    half-open boundary convention and fail-closed behavior)
  - backend.app.jobs.resource_resolution.resolve_job_resource (the
    jobs-domain resolver: track_id + a declared distance range -> one
    canonical (track_id, section_id) pair, or a refusal)
  - JobService.create_job actually using that resolver, end to end
  - solver._possession_window_covers_block now genuinely discriminating
    between sections of the same track, proven through the real
    job -> block -> solve() path, not a hand-built request

BOUNDARY CONVENTION UNDER TEST (documented at length in
section_registry.py and resource_resolution.py; summarized here):
  - a section spans the HALF-OPEN interval [km_start, km_end);
  - the corridor's own terminus (no section starts there) is given to
    the final section;
  - a job's declared distance_start and distance_end are resolved
    INDEPENDENTLY and must land in the SAME section - if they do not
    (including the case where one endpoint sits exactly on a boundary
    that puts it in a different section from the other), the job is
    refused as crossing a section boundary, never silently assigned to
    either side.

This convention is not invented in this file: it reuses the answer
backend.app.data.train_adapter.section_for_km already gave to the same
question (start_km <= km < end_km, terminus -> final section). That
legacy module is untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from backend.app.data.generator import CorridorDataGenerator
from backend.app.data.models import Corridor, Section, TrackSegment
from backend.app.data.corridor_dataset import load_corridor_dataset
from backend.app.data.section_registry import (
    ChainageResolutionError,
    SectionRegistry,
)
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import (
    JobOptimizationService,
    build_jobs_optimization_request,
)
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.resource_resolution import (
    JobResource,
    JobResourceResolutionError,
    resolve_job_resource,
)
from backend.app.jobs.service import JobService
from backend.app.optimizer.solver import (
    _possession_window_covers_block,
    solve,
)
from contracts import BlockCandidate, OptimizationRequest, PossessionWindow


HORIZON_START = "2026-09-10T00:00:00+05:30"
HORIZON_MINUTES = 1440


# ----------------------------------------------------------------------
# A small, hand-built two-section, single-track corridor - X(0) - Y(100)
# - Z(200) - for boundary tests and the multi-section optimization proof.
# ----------------------------------------------------------------------

TWO_SECTION_STATIONS = [
    {"station_id": "X", "name": "X", "km": 0.0},
    {"station_id": "Y", "name": "Y", "km": 100.0},
    {"station_id": "Z", "name": "Z", "km": 200.0},
]


def two_section_registry(track_ids: List[str] = ("T1",)) -> SectionRegistry:
    return SectionRegistry.from_stations(
        "TWO_SECTION_CORRIDOR",
        TWO_SECTION_STATIONS,
        track_ids=list(track_ids),
    )


def two_section_corridor_and_registry(
    track_ids: List[str] = ("T1",),
) -> tuple[Corridor, SectionRegistry]:
    tracks = [
        TrackSegment(
            track_id=track_id,
            corridor_id="TWO_SECTION_CORRIDOR",
            segment_name=f"SEG-{track_id}",
            section_name=f"{track_id} main",
            direction="UP",
            km_start=0.0,
            km_end=200.0,
        )
        for track_id in track_ids
    ]

    assets = CorridorDataGenerator(seed=7).generate_assets(
        tracks,
        num_assets_per_track=4,
    )

    corridor = Corridor(
        corridor_id="TWO_SECTION_CORRIDOR",
        name="Two Section Corridor",
        tracks=tracks,
        assets=assets,
    )

    registry = two_section_registry(track_ids)

    return corridor, registry


def two_section_service(tmp_path: Path, track_ids=("T1",)) -> JobService:
    corridor, registry = two_section_corridor_and_registry(track_ids)

    return JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        corridor=corridor,
        registry=registry,
    )


def make_job_request(
    track_id: str = "T1",
    distance_start: float = 49_500.0,
    distance_end: float = 50_500.0,
    description: str = "test job",
) -> JobCreateRequest:
    return JobCreateRequest(
        track_id=track_id,
        job_type="BALLAST_TAMPING",
        distance_start=distance_start,
        distance_end=distance_end,
        workers_min=2,
        workers_max=4,
        description=description,
    )


# ========================================================================
# SectionRegistry.resolve_by_chainage - unit level
# ========================================================================


def test_km_exactly_at_section_start_resolves_to_that_section():
    registry = two_section_registry()

    section = registry.resolve_by_chainage(100.0)

    assert section.section_id == "Y-Z"


def test_km_exactly_at_section_end_resolves_to_the_next_section():
    """The shared boundary (km 100, station Y) is NOT ambiguous under
    the half-open convention: it belongs to the section that STARTS
    there (Y-Z), not the one that ends there (X-Y)."""

    registry = two_section_registry()

    section = registry.resolve_by_chainage(100.0)

    assert section.section_id != "X-Y"
    assert section.section_id == "Y-Z"


def test_km_just_below_boundary_resolves_to_the_earlier_section():
    registry = two_section_registry()

    section = registry.resolve_by_chainage(99.999)

    assert section.section_id == "X-Y"


def test_km_just_above_boundary_resolves_to_the_later_section():
    registry = two_section_registry()

    section = registry.resolve_by_chainage(100.001)

    assert section.section_id == "Y-Z"


def test_corridor_terminus_resolves_to_the_final_section():
    """km 200 (station Z) has no section starting there - given to the
    final section (Y-Z) as the documented exception to the half-open
    rule."""

    registry = two_section_registry()

    section = registry.resolve_by_chainage(200.0)

    assert section.section_id == "Y-Z"


def test_corridor_origin_resolves_to_the_first_section():
    registry = two_section_registry()

    section = registry.resolve_by_chainage(0.0)

    assert section.section_id == "X-Y"


def test_out_of_range_km_is_rejected():
    registry = two_section_registry()

    with pytest.raises(ChainageResolutionError, match="outside"):
        registry.resolve_by_chainage(200.5)


def test_negative_km_is_rejected():
    registry = two_section_registry()

    with pytest.raises(ChainageResolutionError, match="outside"):
        registry.resolve_by_chainage(-0.001)


def test_malformed_km_values_are_rejected_not_silently_coerced():
    registry = two_section_registry()

    for bad_km in (float("inf"), float("-inf")):
        with pytest.raises(ChainageResolutionError):
            registry.resolve_by_chainage(bad_km)

    with pytest.raises(ChainageResolutionError):
        registry.resolve_by_chainage(float("nan"))


def test_empty_registry_rejects_every_chainage():
    registry = SectionRegistry("EMPTY", [])

    with pytest.raises(ChainageResolutionError, match="no registered sections"):
        registry.resolve_by_chainage(0.0)


def test_multi_track_section_resolves_with_explicit_track():
    registry = two_section_registry(track_ids=["T1", "T2"])

    section = registry.resolve_by_chainage(50.0, track_id="T2")

    assert section.section_id == "X-Y"
    assert "T2" in section.track_ids


def test_multi_track_section_without_track_still_resolves_geographically():
    """Geographic identity does not require a track - this registry has
    no overlapping sections, so no track is needed to disambiguate."""

    registry = two_section_registry(track_ids=["T1", "T2"])

    section = registry.resolve_by_chainage(50.0)

    assert section.section_id == "X-Y"


def test_track_not_serving_any_candidate_section_fails_closed():
    registry = two_section_registry(track_ids=["T1"])

    with pytest.raises(ChainageResolutionError, match="none of which list"):
        registry.resolve_by_chainage(50.0, track_id="GHOST-TRACK")


def test_overlapping_sections_sharing_a_station_pair_require_track_to_disambiguate():
    """Parallel routes between the same two stations: two DISTINCT
    sections, same span, different section_id and different track_ids -
    exactly the case SectionRegistry's own docstring describes as
    permitted. Geographic km alone cannot tell them apart."""

    registry = SectionRegistry(
        "PARALLEL_CORRIDOR",
        [
            Section(
                section_id="X-Y-MAIN",
                corridor_id="PARALLEL_CORRIDOR",
                start_station_id="X",
                end_station_id="Y",
                km_start=0.0,
                km_end=100.0,
                track_ids=("MAIN-1",),
            ),
            Section(
                section_id="X-Y-LOOP",
                corridor_id="PARALLEL_CORRIDOR",
                start_station_id="X",
                end_station_id="Y",
                km_start=0.0,
                km_end=100.0,
                track_ids=("LOOP-1",),
            ),
        ],
    )

    # Without a track, both sections match at km 50 - ambiguous, refused.
    with pytest.raises(ChainageResolutionError, match="ambiguous"):
        registry.resolve_by_chainage(50.0)

    # With a track, exactly one candidate survives - resolves cleanly.
    main = registry.resolve_by_chainage(50.0, track_id="MAIN-1")
    loop = registry.resolve_by_chainage(50.0, track_id="LOOP-1")

    assert main.section_id == "X-Y-MAIN"
    assert loop.section_id == "X-Y-LOOP"

    # A track that serves NEITHER parallel section still fails closed.
    with pytest.raises(ChainageResolutionError, match="none of which list"):
        registry.resolve_by_chainage(50.0, track_id="OTHER-TRACK")


# ========================================================================
# resolve_job_resource - unit level
# ========================================================================


def test_resolve_job_resource_within_one_section():
    registry = two_section_registry()

    resource = resolve_job_resource(registry, "T1", 49_500.0, 50_500.0)

    assert resource == JobResource(track_id="T1", section_id="X-Y")


def test_resolve_job_resource_at_the_far_section():
    registry = two_section_registry()

    resource = resolve_job_resource(registry, "T1", 149_500.0, 150_500.0)

    assert resource == JobResource(track_id="T1", section_id="Y-Z")


def test_resolve_job_resource_rejects_a_range_crossing_a_boundary():
    """distance_start=99000 (km 99, section X-Y) and distance_end=101000
    (km 101, section Y-Z) genuinely straddle the boundary at km 100."""

    registry = two_section_registry()

    with pytest.raises(JobResourceResolutionError, match="crosses a section boundary"):
        resolve_job_resource(registry, "T1", 99_000.0, 101_000.0)


def test_resolve_job_resource_rejects_an_end_exactly_on_the_boundary():
    """distance_end=100000 (km 100.0) resolves to Y-Z under the
    half-open rule while distance_start=99000 (km 99) resolves to X-Y -
    even though a human might describe this job as "ending at Y", the
    two endpoints disagree and the job is refused rather than guessed.
    This is the documented, deliberate answer to "km exactly at section
    end" - see the module docstring's BOUNDARY CONVENTION section."""

    registry = two_section_registry()

    with pytest.raises(JobResourceResolutionError, match="crosses a section boundary"):
        resolve_job_resource(registry, "T1", 99_000.0, 100_000.0)


def test_resolve_job_resource_accepts_a_range_starting_exactly_on_a_boundary():
    """distance_start=100000 (km 100.0, the start of Y-Z) and
    distance_end=100900 (km 100.9) both resolve to Y-Z - no crossing,
    clean success even though the start touches the boundary exactly."""

    registry = two_section_registry()

    resource = resolve_job_resource(registry, "T1", 100_000.0, 100_900.0)

    assert resource == JobResource(track_id="T1", section_id="Y-Z")


def test_resolve_job_resource_rejects_out_of_corridor_location():
    registry = two_section_registry()

    with pytest.raises(JobResourceResolutionError, match="start location"):
        resolve_job_resource(registry, "T1", 250_000.0, 251_000.0)


def test_resolve_job_resource_rejects_a_track_not_serving_the_section():
    registry = two_section_registry(track_ids=["T1"])

    with pytest.raises(JobResourceResolutionError, match="start location"):
        # T1 is the only track any section here lists; ask for one that
        # was never registered at all.
        resolve_job_resource(registry, "GHOST", 49_500.0, 50_500.0)


def test_resolve_job_resource_cross_corridor_mismatch():
    """A registry for corridor A never resolves a track/section pair
    that belongs to corridor B - the registries are simply disjoint, so
    a track name that only exists in B's registry is refused by A's."""

    registry_a = two_section_registry(track_ids=["T1"])

    dataset_b = load_corridor_dataset()  # CORR-NDLS-AGC, tracks UP-1/DOWN-1

    with pytest.raises(JobResourceResolutionError):
        resolve_job_resource(registry_a, "UP-1", 49_500.0, 50_500.0)

    # And the reverse: TWO_SECTION_CORRIDOR's track T1 means nothing in
    # the dataset corridor's registry either.
    with pytest.raises(JobResourceResolutionError):
        resolve_job_resource(dataset_b.registry, "T1", 1000.0, 1400.0)


# ========================================================================
# JobService.create_job - the resolver actually wired in
# ========================================================================


def test_job_resolves_to_correct_section_and_track(tmp_path: Path):
    service = two_section_service(tmp_path)

    job = service.create_job(make_job_request())

    block = job["block_candidate"]

    assert block["section_id"] == "X-Y"
    assert block["track_id"] == "T1"


def test_job_at_the_far_section_resolves_correctly(tmp_path: Path):
    service = two_section_service(tmp_path)

    job = service.create_job(
        make_job_request(distance_start=149_500.0, distance_end=150_500.0)
    )

    block = job["block_candidate"]

    assert block["section_id"] == "Y-Z"
    assert block["track_id"] == "T1"


def test_job_crossing_a_section_boundary_is_rejected_at_creation(
    tmp_path: Path,
):
    service = two_section_service(tmp_path)

    with pytest.raises(ValueError, match="crosses a section boundary"):
        service.create_job(
            make_job_request(distance_start=99_000.0, distance_end=101_000.0)
        )

    # Nothing was persisted for the rejected job.
    assert service.repository.list_all() == []


def test_job_out_of_corridor_range_is_rejected_at_creation(tmp_path: Path):
    service = two_section_service(tmp_path)

    with pytest.raises(ValueError):
        service.create_job(
            make_job_request(distance_start=250_000.0, distance_end=251_000.0)
        )

    assert service.repository.list_all() == []


def test_job_missing_location_is_rejected_before_reaching_create_job():
    """distance_start/distance_end are required JobCreateRequest fields -
    a request missing them never reaches JobService.create_job at all."""

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JobCreateRequest(
            track_id="T1",
            job_type="BALLAST_TAMPING",
            workers_min=2,
            workers_max=4,
            description="no location given",
        )


def test_job_on_multi_track_section_with_explicit_track(tmp_path: Path):
    service = two_section_service(tmp_path, track_ids=["T1", "T2"])

    job_t1 = service.create_job(make_job_request(track_id="T1"))
    job_t2 = service.create_job(make_job_request(track_id="T2"))

    assert job_t1["block_candidate"]["section_id"] == "X-Y"
    assert job_t1["block_candidate"]["track_id"] == "T1"
    assert job_t2["block_candidate"]["section_id"] == "X-Y"
    assert job_t2["block_candidate"]["track_id"] == "T2"


def test_job_with_invalid_track_for_its_location_is_rejected(
    tmp_path: Path,
):
    """The track exists on the corridor (JobService's own track-exists
    guard passes) but does not serve the section at the job's declared
    km - a distinct failure from an unknown track_id entirely."""

    # The corridor's own track list includes T2-UNREGISTERED (so
    # JobService's track-exists guard passes, and it has an asset to
    # find), but the REGISTRY only lists T1 as serving either section.
    corridor, _ = two_section_corridor_and_registry(
        track_ids=["T1", "T2-UNREGISTERED"]
    )
    registry = two_section_registry(track_ids=["T1"])

    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        corridor=corridor,
        registry=registry,
    )

    with pytest.raises(ValueError, match="none of which list"):
        service.create_job(make_job_request(track_id="T2-UNREGISTERED"))


def test_job_service_without_a_registry_fails_closed_on_create(
    tmp_path: Path,
):
    """An explicit corridor= with no dataset= and no registry= is a
    configuration no current caller produces, but must still fail
    closed rather than silently accept an unresolved job."""

    corridor, _ = two_section_corridor_and_registry()

    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        corridor=corridor,
    )

    assert service.registry is None

    with pytest.raises(ValueError, match="SectionRegistry"):
        service.create_job(make_job_request())


def test_block_candidate_resolution_reaches_the_real_dataset_corridor(
    tmp_path: Path,
):
    """End to end on the actual six-section dataset corridor wired in
    Step 10, not the hand-built two-section fixture above."""

    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        dataset=load_corridor_dataset(),
    )

    job = service.create_job(
        make_job_request(track_id="UP-1", distance_start=1_000.0, distance_end=1_400.0)
    )

    block = job["block_candidate"]

    assert block["section_id"] == "NDLS-NZM"
    assert block["track_id"] == "UP-1"


# ========================================================================
# The multi-section optimization proof - THROUGH job -> block -> solve(),
# exactly the scenario in the Step 11 brief.
# ========================================================================


def _solve_single_job(
    block: BlockCandidate,
    possession_windows: List[PossessionWindow],
) -> OptimizationRequest:
    request = OptimizationRequest(
        corridor_id="TWO_SECTION_CORRIDOR",
        horizon_start=HORIZON_START,
        horizon_minutes=HORIZON_MINUTES,
        tracks=["T1"],
        candidates=[block],
        possession_windows=possession_windows,
        existing_committed_blocks=[],
        min_headway_minutes=15,
    )
    return request


def test_optimization_is_section_aware_not_track_wide(tmp_path: Path):
    """The exact scenario from the Step 11 brief:

        Section A: track T1, km 0-100
        Section B: track T1, km 100-200
        Job:       T1, km 50   -> resolves to Section A

    A possession window on Section B must NOT cover it; a possession
    window on Section A must. The reverse job at km 150 (Section B)
    gets the opposite result. Both proven through the real
    JobService.create_job -> BlockCandidate -> solver.solve() path.
    """

    service = two_section_service(tmp_path)

    job_a = service.create_job(make_job_request())  # km 49.5-50.5 -> X-Y
    job_b = service.create_job(
        make_job_request(distance_start=149_500.0, distance_end=150_500.0)
    )  # km 149.5-150.5 -> Y-Z

    block_a = BlockCandidate.from_dict(job_a["block_candidate"])
    block_b = BlockCandidate.from_dict(job_b["block_candidate"])

    assert block_a.section_id == "X-Y"
    assert block_b.section_id == "Y-Z"

    window_section_a_only = PossessionWindow(
        window_id="POS-A",
        track_id="T1",
        start_minute=0,
        end_minute=HORIZON_MINUTES,
        section_id="X-Y",
    )
    window_section_b_only = PossessionWindow(
        window_id="POS-B",
        track_id="T1",
        start_minute=0,
        end_minute=HORIZON_MINUTES,
        section_id="Y-Z",
    )

    # --- Job at km 50 (Section X-Y) ---

    # Section B possession does NOT cover a Section A job.
    assert not _possession_window_covers_block(
        window_section_b_only, block_a
    )
    result_wrong_section = solve(
        _solve_single_job(block_a, [window_section_b_only])
    )
    assert block_a.block_id not in {
        b.block_id for b in result_wrong_section.scheduled_blocks
    }
    assert block_a.block_id in result_wrong_section.rejection_reasons

    # Section A possession DOES cover a Section A job.
    assert _possession_window_covers_block(window_section_a_only, block_a)
    result_right_section = solve(
        _solve_single_job(block_a, [window_section_a_only])
    )
    assert block_a.block_id in {
        b.block_id for b in result_right_section.scheduled_blocks
    }

    # --- Job at km 150 (Section Y-Z): the mirror image ---

    assert _possession_window_covers_block(window_section_b_only, block_b)
    assert not _possession_window_covers_block(
        window_section_a_only, block_b
    )

    result_b_covered = solve(
        _solve_single_job(block_b, [window_section_b_only])
    )
    assert block_b.block_id in {
        b.block_id for b in result_b_covered.scheduled_blocks
    }

    result_b_uncovered = solve(
        _solve_single_job(block_b, [window_section_a_only])
    )
    assert block_b.block_id not in {
        b.block_id for b in result_b_uncovered.scheduled_blocks
    }
    assert block_b.block_id in result_b_uncovered.rejection_reasons


def test_uncovered_possession_tracks_flags_the_resolved_section(
    tmp_path: Path,
):
    """build_jobs_optimization_request's own fail-closed check
    (uncovered_possession_tracks) operates on the SAME resolved
    resource, not just solve() internals."""

    from backend.app.optimizer.solver import uncovered_possession_tracks

    service = two_section_service(tmp_path)
    job_a = service.create_job(make_job_request())

    block_a = BlockCandidate.from_dict(job_a["block_candidate"])

    request = build_jobs_optimization_request(
        corridor_id="TWO_SECTION_CORRIDOR",
        tracks=["T1"],
        candidates=[block_a],
        possession_windows=[
            PossessionWindow(
                window_id="POS-B",
                track_id="T1",
                start_minute=0,
                end_minute=HORIZON_MINUTES,
                section_id="Y-Z",
            )
        ],
    )

    uncovered = uncovered_possession_tracks(request)

    assert "T1" in uncovered
    assert block_a.block_id in uncovered["T1"]


# ========================================================================
# Legacy section_id=None path remains isolated
# ========================================================================


def test_legacy_none_section_id_is_still_constructible_directly():
    """The CONTRACT default (BlockCandidate.section_id=None) is
    untouched - this is what golden_scenario.json and every
    pre-Sprint-3-Step-3 hand-built request still relies on. Step 11
    changes what JobService.create_job PRODUCES, not what the contract
    ALLOWS."""

    candidate = BlockCandidate(
        block_id="BLK-LEGACY",
        asset_id="AST-LEGACY",
        track_id="T1",
        work_type="ROUTINE_INSPECTION",
        duration_minutes=45,
    )

    assert candidate.section_id is None


def test_legacy_none_section_id_still_matches_by_track_alone(tmp_path: Path):
    """The asymmetric solver rule itself is untouched: a block that
    genuinely declares no section_id (as a hand-built legacy request
    might) is still matched by track_id alone. This is the ONE case
    that fallback remains for - it no longer applies to
    JobService-created jobs, which now always resolve a real
    section_id."""

    legacy_block = BlockCandidate(
        block_id="BLK-LEGACY",
        asset_id="AST-LEGACY",
        track_id="T1",
        work_type="ROUTINE_INSPECTION",
        duration_minutes=45,
        section_id=None,
    )

    window_on_a_different_section = PossessionWindow(
        window_id="POS-B",
        track_id="T1",
        start_minute=0,
        end_minute=HORIZON_MINUTES,
        section_id="Y-Z",
    )

    assert _possession_window_covers_block(
        window_on_a_different_section, legacy_block
    )


def test_new_jobs_never_produce_a_none_section_id(tmp_path: Path):
    """Positive confirmation of the Step 11 production contract: every
    job JobService.create_job persists carries a resolved section_id -
    the None-fallback path is never silently reached for production
    jobs on a corridor with a working registry."""

    service = two_section_service(tmp_path)

    for _ in range(3):
        job = service.create_job(make_job_request())
        assert job["block_candidate"]["section_id"] is not None


# ========================================================================
# Job lifecycle + audit preserved
# ========================================================================


def _dataset_service(tmp_path: Path) -> JobService:
    # horizon_minutes pinned to 1440: these tests are about section
    # resolution/lifecycle bookkeeping over the real dataset, not horizon
    # width, so they are pinned to a fixed single-day horizon
    # independent of OPTIMIZATION_HORIZON_MINUTES (the production
    # default, widened to 2880 by Slice 4 Step 5) - a change to that
    # production default must not change what these tests exercise.
    return JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        dataset=load_corridor_dataset(),
        horizon_minutes=1440,
    )


def test_notified_job_keeps_its_resolved_section_across_reoptimization(
    tmp_path: Path,
):
    """On the real six-section dataset corridor (Step 10), not the
    hand-built fixture - the generated-slots path's single-section
    assumption (CorridorDataGenerator._sole_section_id) does not apply
    here at all, since this corridor uses the canonical timetable path.
    """

    service = _dataset_service(tmp_path)
    optimizer = JobOptimizationService(service)

    job = service.create_job(
        make_job_request(track_id="UP-1", distance_start=1_000.0, distance_end=1_400.0)
    )

    outcome = optimizer.optimize_corridor("CORR-NDLS-AGC")
    assert outcome["counts"]["scheduled"] == 1

    service.notify(job["job_id"])

    pinned = service.repository.get(job["job_id"])
    assert pinned["block_candidate"]["section_id"] == "NDLS-NZM"
    assert pinned["block_candidate"]["is_committed"] is True

    service.create_job(
        make_job_request(
            track_id="UP-1", distance_start=100_000.0, distance_end=100_400.0
        )
    )

    optimizer.optimize_corridor("CORR-NDLS-AGC")

    after = service.repository.get(job["job_id"])
    assert after["status"] == "notified"
    assert after["block_candidate"]["section_id"] == "NDLS-NZM"


def test_completed_job_stays_terminal_with_a_resolved_section(
    tmp_path: Path,
):
    service = _dataset_service(tmp_path)
    optimizer = JobOptimizationService(service)

    job = service.create_job(
        make_job_request(track_id="UP-1", distance_start=1_000.0, distance_end=1_400.0)
    )

    optimizer.optimize_corridor("CORR-NDLS-AGC")
    service.notify(job["job_id"])
    service.complete(job["job_id"])

    service.create_job(
        make_job_request(
            track_id="UP-1", distance_start=100_000.0, distance_end=100_400.0
        )
    )
    optimizer.optimize_corridor("CORR-NDLS-AGC")

    after = service.repository.get(job["job_id"])
    assert after["status"] == "completed"


def test_resolution_failure_never_reaches_audit_or_persistence(
    tmp_path: Path,
):
    """A resolution failure happens at REPORT time (create_job), before
    any persistence and long before an optimization run - there is
    nothing for the audit trail to record, because nothing was ever
    created. Confirmed here: the repository stays empty and no audit
    database is even touched."""

    import sqlite3

    service = two_section_service(tmp_path)

    with pytest.raises(ValueError):
        service.create_job(
            make_job_request(distance_start=99_000.0, distance_end=101_000.0)
        )

    assert service.repository.list_all() == []

    # The audit table exists (created lazily by AuditRepository), but a
    # rejected create_job never runs an optimization, so it has no rows.
    from backend.app.audit.repository import AuditRepository

    audit_repo = AuditRepository(tmp_path / "jobs.db")
    assert audit_repo.list_by_corridor("TWO_SECTION_CORRIDOR") == []


def test_audit_record_reflects_the_resolved_section_aware_schedule(
    tmp_path: Path,
):
    import json

    from backend.app.audit.repository import AuditRepository

    db_path = tmp_path / "jobs.db"

    # horizon_minutes pinned to 1440 - see _dataset_service's comment
    # above; this test is about audit-record content, not horizon width.
    service = JobService(
        repository=JobRepository(db_path),
        dataset=load_corridor_dataset(),
        horizon_minutes=1440,
    )
    optimizer = JobOptimizationService(service)

    job = service.create_job(
        make_job_request(track_id="UP-1", distance_start=1_000.0, distance_end=1_400.0)
    )
    outcome = optimizer.optimize_corridor("CORR-NDLS-AGC")

    runs = AuditRepository(db_path).list_by_corridor("CORR-NDLS-AGC")

    assert len(runs) == 1

    request = json.loads(runs[0].request_json)
    candidate = next(
        c for c in request["candidates"] if c["block_id"] == job["job_id"]
    )

    assert candidate["section_id"] == "NDLS-NZM"
    assert outcome["counts"]["scheduled"] == 1
