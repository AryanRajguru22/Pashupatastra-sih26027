"""Slice 4 Step 6: repeat the generated/synthetic possession slots
across the configured multi-day horizon.

BEFORE this step, CorridorDataGenerator.generate_possession_windows
accepted a horizon_minutes parameter and completely ignored it in its
body - it always emitted exactly the 3 original day-1 slots per track
(NIGHT 30-300, MIDDAY 690-870, EVENING 1260-1410), regardless of how
wide the deployment horizon was. Once Step 5 widened the PRODUCTION
horizon to 2880 (2 days), this became an inconsistency the design doc
flagged explicitly (SLICE4_MULTIDAY_SCHEDULING_DESIGN.md Sec.5/Sec.7.4):
a corridor with no timetable (CORRIDOR_A/B_DENSE/C_DISRUPTED, the
default deployment corridor) offered maintenance opportunity on day 1
only, silently capping every job at the horizon's first day.

THIS STEP'S ONLY PRODUCTION CHANGE
    backend.app.data.generator.CorridorDataGenerator.
    generate_possession_windows (and the new classmethod
    repeat_daily_slots it now delegates to) - the 3 slots are repeated
    once per calendar day inside [0, horizon_minutes), using ABSOLUTE
    minute offsets (day N's slots are >= N*1440, never re-based to
    0-1439). No new horizon configuration was introduced:
    JobService.possession_inputs -> _generated_possession_inputs already
    threaded self.horizon_minutes into this exact call site (Step 2) -
    the seam only needed the generator's OWN body fixed, not a new
    plumbing path.

This suite proves, in order:
  A. 1440-minute horizon - exact pre-Step-6 output, no regression
  B. 2880-minute horizon - day 1 unchanged, day 2 is exactly +1440
  C. 4320-minute horizon - three days, deterministic ordering
  D. a non-multiple horizon does not crash the generator itself (the
     whole-calendar-day POLICY remains enforced once, at JobService
     construction - InvalidHorizonMinutesError - not duplicated here)
  E. boundary: a slot ending exactly at the horizon is retained; a slot
     that would cross the horizon is excluded, never truncated
  F. metadata preserved: track_id, section_id, window_type, provenance
  G. determinism: repeated calls are byte-identical; no duplicate
     ids/intervals
  H. no modulo: day-2+ minutes are genuinely >= 1440
  I. integration: JobService(horizon_minutes=2880) over a generated
     corridor reaches optimize_corridor with two days of windows
  J. explicit overrides (1440/2880/4320) all still work
  K. existing Slice 1-3 lifecycle/proposal tests are unaffected (see the
     full-suite run in the Step 6 report - not re-tested here to avoid
     duplicating those suites)
  L. the canonical timetable path is untouched: still gated by
     TimetableCoverage, never reaches the generator at all

Nothing here changes the solver, contracts, BlockCandidate defaults,
timetable coverage logic, authority lifecycle, JobStatus, or the
production horizon default (still 2880, Step 5, unchanged).
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from backend.app.data.generator import CorridorDataGenerator
from backend.app.data.models import TrackSegment
from backend.app.data.section_registry import SectionRegistry
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import (
    POSSESSION_DERIVATION_GENERATED_SLOTS,
    JobService,
)
from contracts import PossessionWindow


WORKER = human_actor("WORKER-601", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-601", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-601", ActorRole.AUTHORITY)


def _tracks(*track_ids: str) -> List[TrackSegment]:
    return [
        TrackSegment(
            track_id=track_id,
            corridor_id="STEP6_TEST_CORRIDOR",
            segment_name=f"SEG-STEP6-{track_id}",
            section_name=f"{track_id} main",
            direction="UP" if track_id.startswith("UP") else "DOWN",
            km_start=0.0,
            km_end=20.0,
        )
        for track_id in track_ids
    ]


def _registry(*track_ids: str) -> SectionRegistry:
    stations = [
        {"station_id": "AAA", "name": "Alpha", "km": 0.0},
        {"station_id": "BBB", "name": "Bravo", "km": 20.0},
    ]
    return SectionRegistry.from_stations(
        "STEP6_TEST_CORRIDOR", stations, track_ids=list(track_ids)
    )


# ----------------------------------------------------------------------
# A. 1440-minute horizon - exact pre-Step-6 output.
# ----------------------------------------------------------------------


def test_a_1440_horizon_produces_exactly_the_original_three_slots():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=1440)

    assert len(windows) == 3
    by_id = {w.window_id: w for w in windows}

    assert by_id["POS-UP-1-NIGHT"].start_minute == 30
    assert by_id["POS-UP-1-NIGHT"].end_minute == 300
    assert by_id["POS-UP-1-NIGHT"].window_type == "NIGHT_TRAFFIC_BLOCK"

    assert by_id["POS-UP-1-MIDDAY"].start_minute == 690
    assert by_id["POS-UP-1-MIDDAY"].end_minute == 870
    assert by_id["POS-UP-1-MIDDAY"].window_type == "MIDDAY_MAINTENANCE_SLOT"

    assert by_id["POS-UP-1-EVENING"].start_minute == 1260
    assert by_id["POS-UP-1-EVENING"].end_minute == 1410
    assert by_id["POS-UP-1-EVENING"].window_type == "EVENING_OFF_PEAK"


def test_a_1440_horizon_two_tracks_produces_exactly_six_windows():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1", "DOWN-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=1440)

    assert len(windows) == 6
    assert {w.track_id for w in windows} == {"UP-1", "DOWN-1"}


# ----------------------------------------------------------------------
# B. 2880-minute horizon - day 1 unchanged, day 2 is exactly +1440.
# ----------------------------------------------------------------------


def test_b_2880_horizon_produces_exactly_six_slots_per_track():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=2880)

    assert len(windows) == 6  # 2 x the original 3


def test_b_2880_horizon_day_one_slots_exactly_match_the_original_values():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=2880)
    by_id = {w.window_id: w for w in windows}

    assert (by_id["POS-UP-1-NIGHT"].start_minute, by_id["POS-UP-1-NIGHT"].end_minute) == (30, 300)
    assert (by_id["POS-UP-1-MIDDAY"].start_minute, by_id["POS-UP-1-MIDDAY"].end_minute) == (690, 870)
    assert (by_id["POS-UP-1-EVENING"].start_minute, by_id["POS-UP-1-EVENING"].end_minute) == (1260, 1410)


def test_b_2880_horizon_day_two_slots_are_exactly_day_one_plus_1440():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=2880)
    by_id = {w.window_id: w for w in windows}

    assert (by_id["POS-UP-1-NIGHT-D1"].start_minute, by_id["POS-UP-1-NIGHT-D1"].end_minute) == (30 + 1440, 300 + 1440)
    assert (by_id["POS-UP-1-MIDDAY-D1"].start_minute, by_id["POS-UP-1-MIDDAY-D1"].end_minute) == (690 + 1440, 870 + 1440)
    assert (by_id["POS-UP-1-EVENING-D1"].start_minute, by_id["POS-UP-1-EVENING-D1"].end_minute) == (1260 + 1440, 1410 + 1440)
    assert by_id["POS-UP-1-NIGHT-D1"].window_type == "NIGHT_TRAFFIC_BLOCK"


def test_b_2880_horizon_all_intervals_stay_within_bounds():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1", "DOWN-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=2880)

    assert len(windows) == 12  # 2 tracks x 2 days x 3 slots
    for window in windows:
        assert 0 <= window.start_minute < window.end_minute <= 2880


# ----------------------------------------------------------------------
# C. 4320-minute horizon - three days, deterministic ordering.
# ----------------------------------------------------------------------


def test_c_4320_horizon_produces_exactly_nine_slots_per_track():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=4320)

    assert len(windows) == 9  # 3 x the original 3
    ids = [w.window_id for w in windows]
    assert ids == [
        "POS-UP-1-NIGHT",
        "POS-UP-1-MIDDAY",
        "POS-UP-1-EVENING",
        "POS-UP-1-NIGHT-D1",
        "POS-UP-1-MIDDAY-D1",
        "POS-UP-1-EVENING-D1",
        "POS-UP-1-NIGHT-D2",
        "POS-UP-1-MIDDAY-D2",
        "POS-UP-1-EVENING-D2",
    ]


def test_c_4320_horizon_day_offsets_are_0_1_2():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=4320)
    by_id = {w.window_id: w for w in windows}

    assert by_id["POS-UP-1-NIGHT"].start_minute == 30 + 0 * 1440
    assert by_id["POS-UP-1-NIGHT-D1"].start_minute == 30 + 1 * 1440
    assert by_id["POS-UP-1-NIGHT-D2"].start_minute == 30 + 2 * 1440
    assert by_id["POS-UP-1-EVENING-D2"].end_minute == 1410 + 2 * 1440 == 4290


# ----------------------------------------------------------------------
# D. Non-multiple horizon - generator does not crash; the whole-day
#    POLICY stays enforced once, at JobService construction, not here.
# ----------------------------------------------------------------------


def test_d_jobservice_still_rejects_non_multiple_horizons(tmp_path: Path):
    from backend.app.jobs.service import InvalidHorizonMinutesError

    with pytest.raises(InvalidHorizonMinutesError):
        JobService(repository=JobRepository(tmp_path / "jobs.db"), horizon_minutes=2000)


def test_d_generator_itself_tolerates_a_non_multiple_horizon():
    """The generator has no whole-day policy of its own (that lives at
    JobService construction) - a direct call with a non-multiple
    horizon must not raise, and must still only emit slots that fit
    entirely inside the given horizon."""

    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=2000)

    for window in windows:
        assert window.end_minute <= 2000

    # Day 2's EVENING slot (2700-2850) does not fit inside 2000 minutes
    # and must be excluded; day 2's NIGHT (1470-1740) and MIDDAY
    # (2130-2310) - MIDDAY does not fit either.
    ids = {w.window_id for w in windows}
    assert "POS-UP-1-NIGHT-D1" in ids  # 1470-1740, fits
    assert "POS-UP-1-MIDDAY-D1" not in ids  # 2130-2310, does not fit
    assert "POS-UP-1-EVENING-D1" not in ids  # 2700-2850, does not fit


# ----------------------------------------------------------------------
# E. Boundary: exactly-at-horizon retained, beyond-horizon excluded.
# ----------------------------------------------------------------------


def test_e_slot_ending_exactly_at_the_horizon_is_retained():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")

    # Day 1's EVENING slot ends at exactly minute 1410.
    windows = generator.generate_possession_windows(tracks, horizon_minutes=1410)

    ids = {w.window_id for w in windows}
    assert "POS-UP-1-EVENING" in ids
    evening = next(w for w in windows if w.window_id == "POS-UP-1-EVENING")
    assert evening.end_minute == 1410 == 1410  # inclusive boundary retained


def test_e_slot_extending_one_minute_beyond_the_horizon_is_excluded():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=1409)

    ids = {w.window_id for w in windows}
    assert "POS-UP-1-EVENING" not in ids  # would end at 1410 > 1409
    for window in windows:
        assert window.end_minute <= 1409


def test_e_no_partial_slot_is_ever_truncated():
    """A day whose slot would cross the horizon boundary is excluded
    OUTRIGHT - never emitted with a shortened duration."""

    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=1500)
    night_day1 = [w for w in windows if w.window_id == "POS-UP-1-NIGHT-D1"]

    # Day 2's NIGHT slot (1470-1740) would cross 1500 - must be absent
    # entirely, not present with end_minute==1500.
    assert night_day1 == []


# ----------------------------------------------------------------------
# F. Metadata preserved across every repeated day.
# ----------------------------------------------------------------------


def test_f_section_id_and_track_id_preserved_across_all_days():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")
    registry = _registry("UP-1")

    windows = generator.generate_possession_windows(
        tracks, horizon_minutes=2880, registry=registry
    )

    expected_section_id = registry.section_ids()[0]
    for window in windows:
        assert window.track_id == "UP-1"
        assert window.section_id == expected_section_id


def test_f_window_type_preserved_per_slot_across_days():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=2880)
    by_id = {w.window_id: w for w in windows}

    assert by_id["POS-UP-1-NIGHT"].window_type == by_id["POS-UP-1-NIGHT-D1"].window_type
    assert by_id["POS-UP-1-MIDDAY"].window_type == by_id["POS-UP-1-MIDDAY-D1"].window_type
    assert by_id["POS-UP-1-EVENING"].window_type == by_id["POS-UP-1-EVENING-D1"].window_type


def test_f_generated_possession_inputs_report_synthetic_provenance(tmp_path: Path):
    from backend.app.data.provenance import ProvenanceLevel

    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"), horizon_minutes=2880
    )

    inputs = service.possession_inputs()

    assert inputs.derivation == POSSESSION_DERIVATION_GENERATED_SLOTS
    assert inputs.timetable_provenance is ProvenanceLevel.SYNTHETIC
    assert inputs.possession_provenance is ProvenanceLevel.SYNTHETIC
    assert inputs.snapshot is None  # no timetable was ever consulted


# ----------------------------------------------------------------------
# G. Determinism: repeated calls are identical; no duplicate ids/intervals.
# ----------------------------------------------------------------------


def test_g_repeated_calls_produce_byte_identical_output():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1", "DOWN-1")

    first = generator.generate_possession_windows(tracks, horizon_minutes=4320)
    second = generator.generate_possession_windows(tracks, horizon_minutes=4320)

    assert [w.to_dict() for w in first] == [w.to_dict() for w in second]


def test_g_no_duplicate_window_ids_or_intervals():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1", "DOWN-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=4320)

    ids = [w.window_id for w in windows]
    assert len(ids) == len(set(ids))

    intervals = [(w.track_id, w.start_minute, w.end_minute) for w in windows]
    assert len(intervals) == len(set(intervals))


# ----------------------------------------------------------------------
# H. No modulo: day-2+ minutes are genuinely absolute (>= 1440).
# ----------------------------------------------------------------------


def test_h_day_two_minutes_are_not_collapsed_into_day_one_clock_values():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=2880)
    by_id = {w.window_id: w for w in windows}

    # Exact absolute values - NOT the day-1 minute-of-day values (30,
    # 690, 1260). If a modulo-1440 rebasing bug were reintroduced, these
    # would collapse back to the day-1 numbers instead.
    assert by_id["POS-UP-1-NIGHT-D1"].start_minute == 1470
    assert by_id["POS-UP-1-MIDDAY-D1"].start_minute == 2130
    assert by_id["POS-UP-1-EVENING-D1"].start_minute == 2700

    for window_id in ["POS-UP-1-NIGHT-D1", "POS-UP-1-MIDDAY-D1", "POS-UP-1-EVENING-D1"]:
        window = by_id[window_id]
        assert window.start_minute >= 1440
        assert window.end_minute >= 1440


def test_h_day_three_minutes_are_genuinely_absolute():
    generator = CorridorDataGenerator()
    tracks = _tracks("UP-1")

    windows = generator.generate_possession_windows(tracks, horizon_minutes=4320)
    by_id = {w.window_id: w for w in windows}

    assert by_id["POS-UP-1-NIGHT-D2"].start_minute == 2910  # 30 + 2*1440
    assert by_id["POS-UP-1-NIGHT-D2"].start_minute % 1440 == 30  # the
    # clock-time-of-day IS 30 - the point is the absolute value is 2910,
    # never emitted as a bare 30.


# ----------------------------------------------------------------------
# I. Integration: JobService(horizon_minutes=2880) over a generated
#    corridor reaches optimize_corridor with two days of windows.
# ----------------------------------------------------------------------


def test_i_possession_inputs_carries_two_days_of_windows(tmp_path: Path):
    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"), horizon_minutes=2880
    )

    inputs = service.possession_inputs()

    day_two_windows = [w for w in inputs.windows if w.start_minute >= 1440]
    assert len(day_two_windows) > 0
    assert len(inputs.windows) == 12  # 2 tracks x 2 days x 3 slots


def test_i_optimization_request_carries_the_generated_multiday_windows(
    tmp_path: Path, monkeypatch
):
    import backend.app.jobs.optimization as optimization_module

    captured: dict = {}
    real_solve = optimization_module.solve

    def capturing_solve(request):
        captured["possession_windows"] = list(request.possession_windows)
        return real_solve(request)

    monkeypatch.setattr(optimization_module, "solve", capturing_solve)

    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"), horizon_minutes=2880
    )
    optimizer = JobOptimizationService(service)

    service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Step 6 integration check",
        ),
        actor=WORKER,
    )
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    windows = captured["possession_windows"]
    assert any(w.start_minute >= 1440 for w in windows)
    assert all(w.end_minute <= 2880 for w in windows)


# ----------------------------------------------------------------------
# J. Explicit overrides all still work.
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "horizon_minutes,expected_days",
    [(1440, 1), (2880, 2), (4320, 3)],
)
def test_j_explicit_override_gives_the_expected_number_of_days(
    tmp_path: Path, horizon_minutes: int, expected_days: int
):
    service = JobService(
        repository=JobRepository(tmp_path / f"jobs-{horizon_minutes}.db"),
        horizon_minutes=horizon_minutes,
    )

    windows = service.possession_windows()

    # 2 generated CORRIDOR_A tracks x 3 slots/day x expected_days.
    assert len(windows) == 2 * 3 * expected_days


def test_j_production_default_is_still_2880(tmp_path: Path):
    from backend.app.jobs.service import OPTIMIZATION_HORIZON_MINUTES

    assert OPTIMIZATION_HORIZON_MINUTES == 2880

    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))
    assert service.horizon_minutes == 2880
    assert len(service.possession_windows()) == 2 * 3 * 2


# ----------------------------------------------------------------------
# L. Canonical separation: the canonical path is untouched by this step.
# ----------------------------------------------------------------------


def test_l_canonical_path_never_calls_the_generator(tmp_path: Path, monkeypatch):
    """Reused pattern from test_jobs_canonical_timetable_integration.py's
    own equivalent guard - re-asserted here because Step 6 touched the
    generator module directly and must not have created any accidental
    coupling to the canonical path."""

    from backend.app.data.corridor_dataset import load_corridor_dataset

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "generate_possession_windows was called for a corridor that "
            "has a timetable; Step 6 must not couple the canonical path "
            "to the generated-slots path."
        )

    monkeypatch.setattr(
        CorridorDataGenerator, "generate_possession_windows", _forbidden
    )

    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        dataset=load_corridor_dataset(),
        horizon_minutes=1440,
    )

    inputs = service.possession_inputs()
    assert inputs.windows  # canonical windows were still produced


def test_l_generated_path_never_consults_timetable_coverage(tmp_path: Path, monkeypatch):
    """The generated-slots path has no coverage concept at all - Step 6
    must not have introduced one. Proven by ensuring
    TimetableCoverage.from_timetable_records is never called for a
    generated (no-dataset) corridor, even across a multi-day horizon."""

    from backend.app.data.timetable_coverage import TimetableCoverage

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "TimetableCoverage was consulted on the generated-slots path, "
            "which has no coverage concept."
        )

    monkeypatch.setattr(TimetableCoverage, "from_timetable_records", _forbidden)

    service = JobService(
        repository=JobRepository(tmp_path / "jobs.db"), horizon_minutes=4320
    )

    inputs = service.possession_inputs()
    assert inputs.windows
