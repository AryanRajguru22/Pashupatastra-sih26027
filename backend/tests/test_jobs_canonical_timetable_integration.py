"""Sprint 3 Step 10: the canonical train/timetable path, wired into jobs.

Steps 5-9 built the canonical train adapter, the SectionRegistry, the
horizon anchor and the provenance model as separate systems. Step 9's
audit found the jobs pipeline still called
CorridorDataGenerator.generate_possession_windows directly, so none of
them were actually in the production path. These tests cover the wiring.

THE BOUNDARY UNDER TEST (see JobService.possession_inputs and
backend.app.jobs.service.DEFAULT_POSSESSION_COMPATIBILITY):

    corridor HAS a timetable -> CANONICAL_TIMETABLE_DERIVED
        provider -> canonical adapter -> SectionTraversal ->
        train-free gaps per (track_id, section_id)

    corridor has NO timetable -> GENERATED_STATIC_SLOTS
        the pre-Step-10 fixed operational slots, kept deliberately for
        the abstract synthetic corridors, which have no station topology
        and for which authoring train movements would fabricate railway
        operations.

Everything here is synthetic. No network, no live provider, no real
railway feed.
"""

from __future__ import annotations

import copy
import json
import socket
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pytest

from backend.app.data.canonical_train import CorridorTopology
from backend.app.data.corridor_dataset import (
    CorridorDataset,
    CorridorDatasetError,
    load_corridor_dataset,
)
from backend.app.data.generator import CorridorDataGenerator
from backend.app.data.models import Corridor, TrackSegment
from backend.app.data.provenance import ProvenanceLevel
from backend.app.data.section_registry import SectionRegistry
from backend.app.data.train_provider import (
    SectionResolutionError,
    StaticTimetableProvider,
    possession_windows_from_provider,
    validate_windows_against_registry,
)
from backend.app.jobs.models import JobCreateRequest, JobOptimizationResponse
from backend.app.jobs.optimization import (
    JobOptimizationService,
    PossessionDataUnavailableError,
)
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import (
    OPTIMIZATION_HORIZON_START,
    POSSESSION_DERIVATION_CANONICAL_TIMETABLE,
    POSSESSION_DERIVATION_GENERATED_SLOTS,
    JobService,
    TimetableCoverageGapError,
    derived_possession_provenance,
)
from contracts import PossessionWindow

from backend.tests.execution_helpers import execute_to_completion

# Slice 9: approving a proposal requires an identified human actor -
# accountability, not authentication (see backend.app.jobs.lifecycle.
# _require_identified_human). An in-process caller must now name one.
from backend.app.identity.actor import ActorRole as _ActorRole
from backend.app.identity.actor import human_actor as _human_actor

APPROVING_AUTHORITY = _human_actor("AUTHORITY-017", _ActorRole.AUTHORITY)



HORIZON_START = OPTIMIZATION_HORIZON_START  # 2026-09-10T00:00:00+05:30
SERVICE_DATE = "2026-09-10"

# This suite's own fixtures are deliberately single/dual-day (hand-built
# trains dated 2026-09-10/11) - a fixed, local "one calendar day" horizon
# length, independent of the jobs-pipeline's PRODUCTION deployment
# default (backend.app.jobs.service.OPTIMIZATION_HORIZON_MINUTES), which
# Slice 4 Step 5 widened to 2880. Before Step 5 the two values happened
# to be numerically equal, so every call site below used to import the
# production constant directly and pass it in as "the horizon length";
# that coupling was coincidental, not intentional - see
# SLICE4_MULTIDAY_SCHEDULING_DESIGN.md Sec.11. Every one of those call
# sites now reads THIS constant instead, so this suite keeps testing
# exactly the single/dual-day scenarios it always tested, unaffected by
# future changes to the production default.
ONE_DAY_MINUTES = 1440


# ----------------------------------------------------------------------
# Small hand-built corridor, for the cases the shipped dataset does not
# contain (overnight run, pre-horizon train, multi-day offsets).
# ----------------------------------------------------------------------


STATIONS = [
    {"station_id": "AAA", "name": "Alpha", "km": 0.0},
    {"station_id": "BBB", "name": "Bravo", "km": 20.0},
    {"station_id": "CCC", "name": "Charlie", "km": 45.0},
]

TRACK_IDS = ("UP-1", "DOWN-1")


def _tracks(corridor_id: str) -> List[TrackSegment]:
    return [
        TrackSegment(
            track_id=track_id,
            corridor_id=corridor_id,
            segment_name=f"SEG-{corridor_id}-{track_id}",
            section_name=f"{track_id} main",
            direction="UP" if track_id.startswith("UP") else "DOWN",
            km_start=0.0,
            km_end=45.0,
        )
        for track_id in TRACK_IDS
    ]


def stop(
    station_id: str,
    arrival: str,
    departure: str | None = None,
) -> Dict[str, Any]:
    return {
        "station_id": station_id,
        "scheduled_arrival": arrival,
        "scheduled_departure": departure or arrival,
    }


def train(
    train_number: str,
    track_id: str,
    stops: List[Dict[str, Any]],
    service_date: str | None = SERVICE_DATE,
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "train_number": train_number,
        "track_assignment": track_id,
        "direction": "UP" if track_id.startswith("UP") else "DOWN",
        "station_times": stops,
    }

    if service_date is not None:
        record["service_date"] = service_date

    return record


def make_dataset(
    records: List[Dict[str, Any]],
    corridor_id: str = "TEST_CORRIDOR",
) -> CorridorDataset:
    """A CorridorDataset built in memory - no file, no I/O.

    Topology and registry come from the SAME station list, exactly as
    corridor_dataset.load_corridor_dataset builds them, so section ids
    the adapter produces resolve in the registry.
    """

    tracks = _tracks(corridor_id)

    return CorridorDataset(
        corridor=Corridor(
            corridor_id=corridor_id,
            name=corridor_id,
            tracks=tracks,
            # Generated exactly as the real loader does, so job creation
            # (which resolves the nearest asset) behaves identically.
            assets=CorridorDataGenerator(seed=42).generate_assets(
                tracks,
                num_assets_per_track=4,
            ),
        ),
        topology=CorridorTopology.from_stations(STATIONS),
        registry=SectionRegistry.from_stations(
            corridor_id,
            STATIONS,
            track_ids=[t.track_id for t in tracks],
        ),
        timetable_records=tuple(records),
    )


def service_with(
    dataset: CorridorDataset,
    tmp_path: Path,
    **kwargs,
) -> JobService:
    return JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        dataset=dataset,
        **kwargs,
    )


# ----------------------------------------------------------------------
# 1-2. The provider boundary is what the jobs pipeline goes through.
# ----------------------------------------------------------------------


def test_job_service_obtains_canonical_state_through_provider_boundary(
    tmp_path: Path,
):
    """The jobs pipeline reaches CanonicalTrainState/SectionTraversal,
    not raw records: possession_inputs returns the snapshot the provider
    produced, carrying converted trains and their traversals."""

    dataset = make_dataset(
        [
            train(
                "T1",
                "UP-1",
                [stop("AAA", "08:00"), stop("BBB", "08:30")],
            )
        ]
    )

    inputs = service_with(dataset, tmp_path).possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    assert inputs.derivation == POSSESSION_DERIVATION_CANONICAL_TIMETABLE
    assert inputs.snapshot is not None

    canonical_train = inputs.snapshot.trains[0]

    assert canonical_train.train_number == "T1"
    assert canonical_train.traversals[0].section_id == "AAA-BBB"
    assert canonical_train.traversals[0].track_id == "UP-1"


def test_shipped_synthetic_dataset_flows_through_the_canonical_adapter(
    tmp_path: Path,
):
    """The checked-in synthetic timetable is usable end to end: all 48
    trains (24 per service date, Slice 4 Step 7) convert, nothing is
    rejected, and every window is derived. Conversion is horizon-length
    independent, so the day-2 trains convert even at this one-day
    horizon; derivation clips them outside [0, 1440)."""

    dataset = load_corridor_dataset()

    inputs = service_with(dataset, tmp_path).possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    assert inputs.derivation == POSSESSION_DERIVATION_CANONICAL_TIMETABLE
    assert len(inputs.snapshot.trains) == 48
    assert inputs.rejections == ()
    assert inputs.windows
    assert all(window.window_type == "TRAIN_GAP" for window in inputs.windows)


def test_dataset_loader_refuses_data_not_declared_synthetic(tmp_path: Path):
    """Fail closed on provenance: a dataset that does not declare itself
    synthetic is refused rather than loaded and labelled by assumption."""

    source = json.loads(
        Path("pashupatastra_realistic_dataset.json").read_text(
            encoding="utf-8"
        )
    )
    source["synthetic"] = False

    path = tmp_path / "not_declared_synthetic.json"
    path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(CorridorDatasetError, match="synthetic"):
        load_corridor_dataset(path)


# ----------------------------------------------------------------------
# 3-4. Passing through a canonical adapter does not promote provenance.
# ----------------------------------------------------------------------


def test_synthetic_timetable_stays_synthetic_through_the_adapter(
    tmp_path: Path,
):
    dataset = load_corridor_dataset()

    inputs = service_with(dataset, tmp_path).possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    assert inputs.timetable_provenance is ProvenanceLevel.SYNTHETIC
    assert inputs.possession_provenance is ProvenanceLevel.SYNTHETIC


def test_derived_possession_provenance_refuses_to_promote_real_inputs():
    """A derived window is not automatically as real as its inputs. The
    non-synthetic case is deliberately unsettled and fails closed rather
    than being resolved by guessing - see derived_possession_provenance.
    """

    assert (
        derived_possession_provenance(ProvenanceLevel.SYNTHETIC)
        is ProvenanceLevel.SYNTHETIC
    )

    for level in (
        ProvenanceLevel.REAL_SCHEDULED,
        ProvenanceLevel.REAL_STATIC,
    ):
        with pytest.raises(ValueError, match="no settled provenance"):
            derived_possession_provenance(level)


# ----------------------------------------------------------------------
# 5-6. Horizon and service_date genuinely reach the conversion.
# ----------------------------------------------------------------------


def test_horizon_start_reaches_the_canonical_conversion(tmp_path: Path):
    """Traversal minutes are relative to the horizon actually passed.

    The same train, anchored to a horizon starting six hours later,
    must resolve 360 minutes EARLIER. This is what proves horizon_start
    is threaded through the provider rather than defaulted somewhere -
    with the shipped dataset alone it would be invisible, because its
    service_date coincides with the default horizon's own date and the
    offsets happen to equal minutes-from-midnight.

    Slice 4 Step 4: a 1440-minute horizon starting at 06:00 (not
    midnight) genuinely touches TWO calendar dates (2026-09-10 from
    06:00 onward, 2026-09-11 up to 06:00) - see
    backend.app.data.timetable_coverage.horizon_calendar_dates. The
    timetable coverage gate now correctly requires both to be covered,
    so a second train dated 2026-09-11 is added purely to satisfy that
    gate; it does not participate in either assertion below, both of
    which read only T1's own traversal (index 0, since T1 is listed
    first).
    """

    dataset = make_dataset(
        [
            train(
                "T1",
                "UP-1",
                [stop("AAA", "08:00"), stop("BBB", "08:30")],
            ),
            train(
                "T2-COVERAGE-ONLY",
                "UP-1",
                [stop("AAA", "10:00"), stop("BBB", "10:30")],
                service_date="2026-09-11",
            ),
        ]
    )

    service = service_with(dataset, tmp_path)

    midnight = service.possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )
    six_am = service.possession_inputs(
        "2026-09-10T06:00:00+05:30",
        ONE_DAY_MINUTES,
    )

    midnight_traversal = midnight.snapshot.trains[0].traversals[0]
    six_am_traversal = six_am.snapshot.trains[0].traversals[0]

    assert midnight_traversal.enter_minute == 8 * 60
    assert six_am_traversal.enter_minute == 2 * 60
    assert (
        midnight_traversal.enter_minute - six_am_traversal.enter_minute
        == 360
    )


def test_missing_service_date_is_rejected_not_invented(tmp_path: Path):
    """Step 8's explicit service_date contract survives the wiring: a
    record without one is rejected individually, never given a date
    derived from horizon_start or today.

    Slice 4 Step 4: a corridor whose ENTIRE timetable has no dated
    record at all now fails at the (earlier, more informative)
    TimetableCoverageGapError gate - see
    test_missing_service_date_with_no_coverage_at_all_fails_the_coverage_gate
    below, which is the direct regression test for that case. This test
    keeps proving the ORIGINAL, lower-level claim - a dateless record is
    rejected INDIVIDUALLY rather than given an invented date - by adding
    a second, properly-dated train that satisfies the coverage gate, so
    NO_DATE's own per-train rejection (not the coverage gate) is what
    this test now observes.
    """

    dataset = make_dataset(
        [
            train(
                "NO_DATE",
                "UP-1",
                [stop("AAA", "08:00"), stop("BBB", "08:30")],
                service_date=None,
            ),
            train(
                "HAS_DATE",
                "UP-1",
                [stop("AAA", "09:00"), stop("BBB", "09:30")],
            ),
        ]
    )

    inputs = service_with(dataset, tmp_path).possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    assert [t.train_number for t in inputs.snapshot.trains] == ["HAS_DATE"]
    assert [r.train_number for r in inputs.rejections] == ["NO_DATE"]
    assert "service_date" in inputs.rejections[0].reason


def test_missing_service_date_with_no_coverage_at_all_fails_the_coverage_gate(
    tmp_path: Path,
):
    """The Slice 4 Step 4 regression for the scenario the test above USED
    to cover directly: a corridor whose entire timetable has no dated
    record fails the coverage gate outright (TimetableCoverageGapError),
    before convert_timetable's own per-train rejection is ever reached -
    a record with no service_date contributes to covered_service_dates
    not at all (see backend.app.data.timetable_coverage.
    covered_service_dates), so the horizon's one required date has zero
    coverage.
    """

    dataset = make_dataset(
        [
            train(
                "NO_DATE",
                "UP-1",
                [stop("AAA", "08:00"), stop("BBB", "08:30")],
                service_date=None,
            )
        ]
    )

    service = service_with(dataset, tmp_path)

    with pytest.raises(TimetableCoverageGapError) as excinfo:
        service.possession_inputs(HORIZON_START, ONE_DAY_MINUTES)

    assert excinfo.value.uncovered_dates == (date(2026, 9, 10),)
    assert excinfo.value.covered_dates == ()


def test_shipped_dataset_carries_explicit_service_dates():
    """The fixture states its own chronology. Without this the canonical
    path could not be anchored at all, and the adapter would refuse to
    invent it."""

    dataset = load_corridor_dataset()

    # Every record states its date, and exactly the two synthetic
    # service dates the Slice 4 Step 7 demo dataset carries are present.
    assert {
        record["service_date"] for record in dataset.timetable_records
    } == {SERVICE_DATE, "2026-09-11"}


# ----------------------------------------------------------------------
# 7-8, 13-14. Chronology cases, through the wired path.
# ----------------------------------------------------------------------


def test_overnight_train_produces_monotonic_horizon_relative_traversal(
    tmp_path: Path,
):
    """23:30 -> 00:30 is 1410 -> 1470, never 1410 -> 30."""

    dataset = make_dataset(
        [
            train(
                "NIGHT",
                "UP-1",
                [stop("AAA", "23:30"), stop("BBB", "00:30")],
            )
        ]
    )

    inputs = service_with(dataset, tmp_path).possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    traversal = inputs.snapshot.trains[0].traversals[0]

    assert (traversal.enter_minute, traversal.exit_minute) == (1410, 1470)
    assert traversal.exit_minute > traversal.enter_minute


def test_reverse_direction_train_is_attributed_to_the_right_section(
    tmp_path: Path,
):
    """A DOWN train runs CCC -> AAA, i.e. against corridor km order. Its
    first leg is section BBB-CCC, and section identity is unchanged by
    direction."""

    dataset = make_dataset(
        [
            train(
                "REVERSE",
                "DOWN-1",
                [
                    stop("CCC", "09:00"),
                    stop("BBB", "09:30"),
                    stop("AAA", "10:00"),
                ],
            )
        ]
    )

    inputs = service_with(dataset, tmp_path).possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    traversals = inputs.snapshot.trains[0].traversals

    assert [t.section_id for t in traversals] == ["BBB-CCC", "AAA-BBB"]
    assert all(t.track_id == "DOWN-1" for t in traversals)


def test_train_entirely_before_horizon_does_not_corrupt_windows(
    tmp_path: Path,
):
    """A train on the PREVIOUS service date resolves to negative minutes.
    It must not produce an inverted interval or eat the whole horizon."""

    dataset = make_dataset(
        [
            train(
                "YESTERDAY",
                "UP-1",
                [stop("AAA", "08:00"), stop("BBB", "08:30")],
                service_date="2026-09-09",
            ),
            train(
                "TODAY",
                "UP-1",
                [stop("AAA", "12:00"), stop("BBB", "12:30")],
            ),
        ]
    )

    inputs = service_with(dataset, tmp_path).possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    yesterday = next(
        t for t in inputs.snapshot.trains if t.train_number == "YESTERDAY"
    )

    assert yesterday.traversals[0].enter_minute < 0
    assert inputs.rejections == ()

    for window in inputs.windows:
        assert 0 <= window.start_minute < window.end_minute
        assert window.end_minute <= ONE_DAY_MINUTES


def test_traversal_beyond_one_day_produces_valid_possession(
    tmp_path: Path,
):
    """A train on the NEXT service date resolves past 1440. The horizon
    is one day, so it must clip cleanly rather than wrap.

    Slice 4 Step 4: the horizon's one required date (2026-09-10) must
    itself be covered by at least one dated record, or the coverage gate
    refuses the whole request before any traversal is even resolved -
    see test_traversal_beyond_one_day_with_no_same_day_coverage_fails_
    closed below for that direct regression. A same-day train is added
    here purely to satisfy the gate; the TOMORROW train (looked up by
    name, not index, since it is no longer the only train) is still what
    both assertions below exercise, unchanged.
    """

    dataset = make_dataset(
        [
            train(
                "TODAY",
                "UP-1",
                [stop("AAA", "06:00"), stop("BBB", "06:30")],
            ),
            train(
                "TOMORROW",
                "UP-1",
                [stop("AAA", "08:00"), stop("BBB", "08:30")],
                service_date="2026-09-11",
            ),
        ]
    )

    inputs = service_with(dataset, tmp_path).possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    tomorrow_train = next(
        t for t in inputs.snapshot.trains if t.train_number == "TOMORROW"
    )
    traversal = tomorrow_train.traversals[0]

    assert traversal.enter_minute == 1440 + 8 * 60
    assert inputs.windows

    for window in inputs.windows:
        assert 0 <= window.start_minute < window.end_minute
        assert window.end_minute <= ONE_DAY_MINUTES


def test_traversal_beyond_one_day_with_no_same_day_coverage_fails_closed(
    tmp_path: Path,
):
    """The Slice 4 Step 4 regression for the scenario the test above USED
    to exercise directly: a corridor whose ONLY timetable record is
    dated for the day AFTER the horizon's required date must fail the
    coverage gate - the horizon's own required date (2026-09-10) has
    zero coverage, regardless of what a later-dated record describes.
    """

    dataset = make_dataset(
        [
            train(
                "TOMORROW",
                "UP-1",
                [stop("AAA", "08:00"), stop("BBB", "08:30")],
                service_date="2026-09-11",
            )
        ]
    )

    service = service_with(dataset, tmp_path)

    with pytest.raises(TimetableCoverageGapError) as excinfo:
        service.possession_inputs(HORIZON_START, ONE_DAY_MINUTES)

    assert excinfo.value.uncovered_dates == (date(2026, 9, 10),)
    assert excinfo.value.covered_dates == (date(2026, 9, 11),)


# ----------------------------------------------------------------------
# 9-11. SectionRegistry integration and fail-closed resolution.
# ----------------------------------------------------------------------


def test_every_derived_window_resolves_in_the_section_registry(
    tmp_path: Path,
):
    dataset = load_corridor_dataset()

    inputs = service_with(dataset, tmp_path).possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    for window in inputs.windows:
        assert dataset.registry.has(window.section_id)

        section = dataset.registry.get(window.section_id)

        # Direction never alters section identity, and identity is never
        # a compound of track and section.
        assert ":" not in window.track_id
        assert ":" not in window.section_id
        assert section.section_id == window.section_id


def test_window_track_id_is_a_running_line_of_its_section(tmp_path: Path):
    dataset = load_corridor_dataset()

    inputs = service_with(dataset, tmp_path).possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    for window in inputs.windows:
        assert window.track_id in dataset.registry.track_ids_for(
            window.section_id
        )


def test_registry_validation_rejects_an_unregistered_section():
    """The guard itself, exercised directly: a window naming a section
    the registry does not know is an error, never repaired by guessing."""

    registry = SectionRegistry.from_stations(
        "TEST_CORRIDOR",
        STATIONS,
        track_ids=list(TRACK_IDS),
    )

    with pytest.raises(SectionResolutionError, match="not registered"):
        validate_windows_against_registry(
            [
                PossessionWindow(
                    window_id="W1",
                    track_id="UP-1",
                    start_minute=0,
                    end_minute=60,
                    section_id="AAA-CCC",  # not adjacent, never registered
                )
            ],
            registry,
        )


def test_registry_validation_rejects_a_track_not_serving_the_section():
    registry = SectionRegistry.from_stations(
        "TEST_CORRIDOR",
        STATIONS,
        track_ids=["UP-1"],
    )

    with pytest.raises(SectionResolutionError, match="does not record"):
        validate_windows_against_registry(
            [
                PossessionWindow(
                    window_id="W1",
                    track_id="LOOP-9",
                    start_minute=0,
                    end_minute=60,
                    section_id="AAA-BBB",
                )
            ],
            registry,
        )


def test_unknown_station_fails_closed_all_the_way_to_the_request(
    tmp_path: Path,
):
    """A train referencing a station outside the corridor cannot be
    placed. Its route is unknown, so EVERY section is withheld, no
    window survives, and the jobs pipeline refuses to optimize rather
    than scheduling work whose train occupation was never verified."""

    dataset = make_dataset(
        [
            train(
                "GHOST",
                "UP-1",
                [stop("AAA", "08:00"), stop("ZZZ", "08:30")],
            )
        ]
    )

    # horizon_minutes pinned to 1440: this fixture's one train is
    # single-day (SERVICE_DATE) - see ONE_DAY_MINUTES's own module-level
    # comment. This test is about rejection/withholding propagation, not
    # horizon width, and optimize_corridor below reads the SERVICE's own
    # self.horizon_minutes (not the explicit ONE_DAY_MINUTES passed to
    # possession_inputs above), so it must be pinned too.
    service = service_with(dataset, tmp_path, horizon_minutes=1440)

    inputs = service.possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    assert inputs.windows == []
    assert len(inputs.rejections) == 1

    service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="ROUTINE_INSPECTION",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="work on an unverifiable corridor",
        )
    )

    with pytest.raises(PossessionDataUnavailableError):
        JobOptimizationService(service).optimize_corridor("TEST_CORRIDOR")


def test_malformed_record_does_not_corrupt_the_valid_trains(
    tmp_path: Path,
):
    """One bad record is isolated as a rejection; the rest of the batch
    still converts. The bad train's OWN section is withheld, so the
    rejection costs maintenance opportunity rather than creating it."""

    dataset = make_dataset(
        [
            train(
                "BROKEN",
                "UP-1",
                [stop("AAA", "not-a-time"), stop("BBB", "08:30")],
            ),
            train(
                "GOOD",
                "UP-1",
                [stop("BBB", "09:00"), stop("CCC", "09:30")],
            ),
        ]
    )

    inputs = service_with(dataset, tmp_path).possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    assert [t.train_number for t in inputs.snapshot.trains] == ["GOOD"]
    assert [r.train_number for r in inputs.rejections] == ["BROKEN"]

    covered = {window.section_id for window in inputs.windows}

    assert "AAA-BBB" not in covered
    assert "BBB-CCC" in covered


# ----------------------------------------------------------------------
# 15-18. Job lifecycle and audit over the canonical path.
# ----------------------------------------------------------------------


def _dataset_service(tmp_path: Path) -> JobService:
    # horizon_minutes pinned to 1440: these tests exercise the canonical
    # path's lifecycle/audit wiring over the real checked-in dataset,
    # not horizon width, so they are pinned to a fixed single-day
    # horizon independent of OPTIMIZATION_HORIZON_MINUTES (the
    # production default, widened to 2880 by Slice 4 Step 5) - a change
    # to that production default must not change what this suite
    # exercises.
    return JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        dataset=load_corridor_dataset(),
        horizon_minutes=1440,
    )


def _report(service: JobService, description: str = "canonical job") -> dict:
    return service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description=description,
        )
    )


def test_optimization_over_canonical_windows_persists_the_schedule(
    tmp_path: Path,
):
    service = _dataset_service(tmp_path)
    job = _report(service)

    outcome = JobOptimizationService(service).optimize_corridor(
        "CORR-NDLS-AGC"
    )

    assert outcome["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert (
        outcome["possession_derivation"]
        == POSSESSION_DERIVATION_CANONICAL_TIMETABLE
    )
    assert outcome["counts"]["scheduled"] == 1

    persisted = service.repository.get(job["job_id"])

    assert persisted["status"] == "scheduled"
    assert persisted["schedule_start_minute"] is not None
    assert persisted["schedule_end_minute"] is not None

    # The schedule was written back into the block candidate too, or a
    # notified job could not be rebuilt as a committed block.
    assert (
        persisted["block_candidate"]["earliest_start_minute"]
        <= persisted["schedule_start_minute"]
    )

    response = JobOptimizationResponse(**outcome)

    assert (
        response.possession_derivation
        == POSSESSION_DERIVATION_CANONICAL_TIMETABLE
    )


def test_notified_block_stays_pinned_across_canonical_reoptimization(
    tmp_path: Path,
):
    service = _dataset_service(tmp_path)
    optimizer = JobOptimizationService(service)

    job = _report(service)

    optimizer.optimize_corridor("CORR-NDLS-AGC")

    service.notify(job["job_id"], actor=APPROVING_AUTHORITY)

    pinned = service.repository.get(job["job_id"])
    pinned_start = pinned["schedule_start_minute"]

    assert pinned["block_candidate"]["is_committed"] is True

    _report(service, "second job")

    outcome = optimizer.optimize_corridor("CORR-NDLS-AGC")

    after = service.repository.get(job["job_id"])

    assert after["status"] == "notified"
    assert after["schedule_start_minute"] == pinned_start

    committed = [
        entry
        for entry in outcome["scheduled"]
        if entry["job_id"] == job["job_id"]
    ]

    assert committed and committed[0]["is_committed"] is True


def test_completed_job_stays_terminal_across_canonical_reoptimization(
    tmp_path: Path,
):
    service = _dataset_service(tmp_path)
    optimizer = JobOptimizationService(service)

    job = _report(service)

    optimizer.optimize_corridor("CORR-NDLS-AGC")
    service.notify(job["job_id"], actor=APPROVING_AUTHORITY)
    execute_to_completion(service, job["job_id"])

    _report(service, "keeps the batch non-empty")

    optimizer.optimize_corridor("CORR-NDLS-AGC")

    after = service.repository.get(job["job_id"])

    assert after["status"] == "completed"


def test_audit_record_captures_provenance_and_derivation(tmp_path: Path):
    from backend.app.audit.repository import AuditRepository

    db_path = tmp_path / "jobs.db"

    # horizon_minutes pinned to 1440 - see _dataset_service's comment
    # above; this test is about audit-record content, not horizon width.
    service = JobService(
        repository=JobRepository(db_path),
        dataset=load_corridor_dataset(),
        horizon_minutes=1440,
    )

    _report(service)

    outcome = JobOptimizationService(service).optimize_corridor(
        "CORR-NDLS-AGC"
    )

    runs = AuditRepository(db_path).list_by_corridor("CORR-NDLS-AGC")

    assert len(runs) == 1

    run = runs[0]

    assert run.solver_status == outcome["solver_status"]
    assert run.solve_time_seconds is not None
    assert run.request_json and run.result_json
    assert run.error is None

    snapshot = json.loads(run.provenance_snapshot_json)

    assert snapshot["provenance"] == outcome["provenance"]
    assert snapshot["provenance"]["effective"] == "SYNTHETIC"
    assert (
        snapshot["possession_derivation"]
        == POSSESSION_DERIVATION_CANONICAL_TIMETABLE
    )
    assert snapshot["possession_window_count"] == len(
        service.possession_inputs(
            OPTIMIZATION_HORIZON_START,
            ONE_DAY_MINUTES,
        ).windows
    )


def test_audit_rejection_summary_is_bounded_and_omits_timetable_content(
    tmp_path: Path,
):
    """Rejections are recorded as a count plus identifiers - enough to
    explain a withheld window, without copying timetable payload into
    the audit trail."""

    from backend.app.audit.repository import AuditRepository

    db_path = tmp_path / "jobs.db"

    dataset = make_dataset(
        [
            train(
                "BROKEN",
                "UP-1",
                [stop("AAA", "nonsense"), stop("BBB", "08:30")],
            ),
            train(
                "GOOD",
                "UP-1",
                [stop("BBB", "09:00"), stop("CCC", "09:30")],
            ),
        ]
    )

    # horizon_minutes pinned to 1440: this fixture's trains are single-day
    # (SERVICE_DATE) - see ONE_DAY_MINUTES's own module-level comment.
    service = JobService(
        repository=JobRepository(db_path),
        dataset=dataset,
        horizon_minutes=1440,
    )

    service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="ROUTINE_INSPECTION",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="job beside a rejected train",
        )
    )

    JobOptimizationService(service).optimize_corridor("TEST_CORRIDOR")

    snapshot = json.loads(
        AuditRepository(db_path)
        .list_by_corridor("TEST_CORRIDOR")[0]
        .provenance_snapshot_json
    )

    assert snapshot["train_rejection_count"] == 1
    assert snapshot["rejected_train_numbers"] == ["BROKEN"]

    # Identifiers and counts only - no station times, no route payload.
    assert "station_times" not in json.dumps(snapshot)


# ----------------------------------------------------------------------
# 20 + the Step 10 regression requirement.
# ----------------------------------------------------------------------


def test_canonical_path_makes_no_network_calls(tmp_path: Path):
    """No live provider exists and none is reached. Any socket creation
    during a full optimize would fail this outright."""

    service = _dataset_service(tmp_path)
    _report(service)

    original = socket.socket

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "The canonical possession path attempted a network "
            "connection. No live provider exists and none may be added."
        )

    socket.socket = _forbidden
    try:
        outcome = JobOptimizationService(service).optimize_corridor(
            "CORR-NDLS-AGC"
        )
    finally:
        socket.socket = original

    assert outcome["solver_status"] in {"OPTIMAL", "FEASIBLE"}


def test_generator_slots_are_not_the_source_when_a_timetable_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """THE Step 10 regression check, stated as a boundary rather than as
    the absence of a function name.

    For a corridor that HAS a timetable, the generator's possession
    slots are not merely unused - the generator is never asked. This
    fails loudly if anything reintroduces
    CorridorDataGenerator.generate_possession_windows into that path.
    """

    from backend.app.data.generator import CorridorDataGenerator

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "generate_possession_windows was called for a corridor that "
            "has a timetable; the canonical path must be the source."
        )

    monkeypatch.setattr(
        CorridorDataGenerator,
        "generate_possession_windows",
        _forbidden,
    )

    inputs = service_with(
        load_corridor_dataset(),
        tmp_path,
    ).possession_inputs(HORIZON_START, ONE_DAY_MINUTES)

    assert inputs.derivation == POSSESSION_DERIVATION_CANONICAL_TIMETABLE
    assert inputs.windows

    # And the windows are genuinely train-derived, not the three fixed
    # operational slots the generator emits.
    assert len(inputs.windows) > 3
    assert all(window.window_type == "TRAIN_GAP" for window in inputs.windows)


def test_corridor_without_a_timetable_keeps_the_compatibility_source(
    tmp_path: Path,
):
    """The other half of the boundary, stated explicitly.

    CORRIDOR_A is an abstract synthetic corridor with no station
    topology and no timetable. It deliberately keeps the generated-slot
    source: authoring train movements for it would fabricate the
    operational content that decides when maintenance may run.
    """

    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))

    assert service.dataset is None
    assert service.corridor.corridor_id == "CORRIDOR_A"

    inputs = service.possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    assert inputs.derivation == POSSESSION_DERIVATION_GENERATED_SLOTS
    assert inputs.snapshot is None
    assert {window.window_type for window in inputs.windows} == {
        "NIGHT_TRAFFIC_BLOCK",
        "MIDDAY_MAINTENANCE_SLOT",
        "EVENING_OFF_PEAK",
    }

    # Provenance is unchanged by which path ran: still synthetic on
    # every axis.
    assert inputs.timetable_provenance is ProvenanceLevel.SYNTHETIC
    assert inputs.possession_provenance is ProvenanceLevel.SYNTHETIC


def test_possession_windows_wrapper_still_returns_just_windows(
    tmp_path: Path,
):
    """The pre-Step-10 entry point keeps working for existing callers."""

    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))

    windows = service.possession_windows()

    assert windows
    assert all(isinstance(window, PossessionWindow) for window in windows)
    assert windows == service.possession_inputs().windows


def test_canonical_windows_are_section_scoped_per_track(tmp_path: Path):
    """Section-specific resource identity survives the wiring: windows
    are keyed per (track_id, section_id), not per whole track or whole
    corridor."""

    dataset = load_corridor_dataset()

    inputs = service_with(dataset, tmp_path).possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    resources = {
        (window.track_id, window.section_id) for window in inputs.windows
    }

    assert len(resources) == 12  # 2 running lines x 6 sections
    assert len({track for track, _ in resources}) == 2
    assert len({section for _, section in resources}) == 6


def test_job_blocks_are_now_section_scoped_not_track_wide(
    tmp_path: Path,
):
    """The Step 10 KNOWN GAP, closed by Step 11.

    This test previously pinned - deliberately, as a known gap - that
    JobService-created blocks carried section_id=None and were matched
    by track_id alone across every section, including sections tens or
    hundreds of km away from the job's own asset. Sprint 3 Step 11
    (backend.app.jobs.resource_resolution.resolve_job_resource, wired
    into JobService.create_job) closes that gap: a job now resolves a
    real section_id from its own declared distance_start/distance_end,
    and is matched ONLY by windows on that same section.

    See test_optimization_is_section_aware_not_track_wide below for the
    full multi-section coverage/non-coverage proof through the actual
    solver path, not just this matching-helper check.
    """

    from backend.app.optimizer.solver import _possession_window_covers_block
    from contracts import BlockCandidate

    service = _dataset_service(tmp_path)
    job = _report(service)

    block = BlockCandidate.from_dict(job["block_candidate"])

    # Resolved, not None - and resolved to the section the job's own
    # declared distance range (1000-1400 m = km 1.0-1.4) actually falls
    # in, not merely "some section on this track".
    assert block.section_id == "NDLS-NZM"
    assert block.track_id == "UP-1"

    inputs = service.possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    covering = [
        window
        for window in inputs.windows
        if _possession_window_covers_block(window, block)
    ]

    # Matched ONLY by windows on this block's own section - never a
    # window belonging to a different section of the same track.
    assert covering
    assert {window.section_id for window in covering} == {"NDLS-NZM"}
    assert all(window.track_id == "UP-1" for window in covering)


def test_multiple_trains_on_one_section_merge_into_gaps(tmp_path: Path):
    """Two trains over the same section produce the gap BETWEEN them,
    not one window per train."""

    dataset = make_dataset(
        [
            train(
                "FIRST",
                "UP-1",
                [stop("AAA", "08:00"), stop("BBB", "08:30")],
            ),
            train(
                "SECOND",
                "UP-1",
                [stop("AAA", "12:00"), stop("BBB", "12:30")],
            ),
        ]
    )

    inputs = service_with(dataset, tmp_path).possession_inputs(
        HORIZON_START,
        ONE_DAY_MINUTES,
    )

    gaps = sorted(
        (window.start_minute, window.end_minute)
        for window in inputs.windows
        if window.section_id == "AAA-BBB"
    )

    # 08:00-08:30 and 12:00-12:30 occupied (plus a 10-minute buffer
    # either side), so the mid-day gap runs 08:40 -> 11:50.
    assert (520, 710) in gaps
