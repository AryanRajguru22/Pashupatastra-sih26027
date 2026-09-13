"""Sprint 3 Step 8: horizon anchoring integrated into the canonical
timetable adapter (convert_train / convert_timetable / possession
derivation).

The low-level conversion arithmetic is pinned separately in
test_horizon_anchor.py. This file covers:

  - convert_train producing horizon-relative minutes when horizon_start
    is supplied, and remaining byte-identical to Sprint 3 Step 5 when it
    is not (backward compatibility - Step 5's own test suite is the
    primary proof of that, this file adds a direct comparison).
  - service_date required, never invented, when horizon anchoring is
    requested.
  - explicit day_offset vs inferred rollover: agreement accepted,
    disagreement rejected.
  - reverse-direction and multi-day trains under horizon anchoring.
  - no clamping to [0, 1440] anywhere on this path.
  - possession derivation correctly consuming horizon-relative
    (including negative) traversal minutes.
"""

from __future__ import annotations

import pytest

from backend.app.data.canonical_train import CorridorTopology
from backend.app.data.timetable_adapter import (
    TimetableConversionError,
    convert_timetable,
    convert_train,
    derive_section_possession_windows,
)


STATIONS = [
    {"station_id": "A", "name": "Alpha", "km": 0.0},
    {"station_id": "B", "name": "Bravo", "km": 10.0},
    {"station_id": "C", "name": "Charlie", "km": 25.0},
    {"station_id": "D", "name": "Delta", "km": 40.0},
]


@pytest.fixture
def topology() -> CorridorTopology:
    return CorridorTopology.from_stations(STATIONS)


def stop(station_id: str, arrival: str, departure: str | None = None, **extra) -> dict:
    record = {
        "station_id": station_id,
        "scheduled_arrival": arrival,
        "scheduled_departure": departure or arrival,
    }
    record.update(extra)
    return record


def train(train_number: str, track_id: str, stops: list[dict], **extra) -> dict:
    record = {
        "train_number": train_number,
        "track_assignment": track_id,
        "direction": extra.pop("direction", "DOWN"),
        "station_times": stops,
    }
    record.update(extra)
    return record


HORIZON_MIDNIGHT = "2026-09-10T00:00:00+05:30"
HORIZON_06 = "2026-09-10T06:00:00+05:30"


# ----------------------------------------------------------------------
# Backward compatibility: horizon_start=None is unchanged from Step 5.
# ----------------------------------------------------------------------


def test_default_mode_is_byte_identical_to_step5(topology: CorridorTopology):
    record = train(
        "T1", "DOWN-1", [stop("A", "23:30"), stop("B", "04:30")]
    )

    legacy = convert_train(record, topology)
    explicit_none = convert_train(record, topology, horizon_start=None)

    assert legacy.traversals == explicit_none.traversals
    assert legacy.traversals[0].enter_minute == 1410
    assert legacy.traversals[0].exit_minute == 1710


# ----------------------------------------------------------------------
# A/C: same-day and non-midnight-horizon conversion through convert_train
# ----------------------------------------------------------------------


def test_same_day_train_anchored_to_midnight_horizon(topology: CorridorTopology):
    record = train(
        "T1", "DOWN-1",
        [stop("A", "08:00"), stop("B", "08:30")],
        service_date="2026-09-10",
    )

    state = convert_train(record, topology, horizon_start=HORIZON_MIDNIGHT)

    assert state.traversals[0].enter_minute == 480
    assert state.traversals[0].exit_minute == 510


def test_non_midnight_horizon_shifts_minutes(topology: CorridorTopology):
    """CASE C from the brief, through the full adapter."""

    record = train(
        "T1", "DOWN-1",
        [stop("A", "08:30"), stop("B", "09:00")],
        service_date="2026-09-10",
    )

    state = convert_train(record, topology, horizon_start=HORIZON_06)

    # 08:30 -> minute 510 from midnight; horizon starts at 06:00 (360).
    assert state.traversals[0].enter_minute == 150
    assert state.traversals[0].exit_minute == 180


# ----------------------------------------------------------------------
# D: event before horizon -> negative minutes, not clamped/dropped
# ----------------------------------------------------------------------


def test_event_before_horizon_produces_negative_minutes(
    topology: CorridorTopology,
):
    record = train(
        "T1", "DOWN-1",
        [stop("A", "04:00"), stop("B", "04:30")],
        service_date="2026-09-10",
    )

    state = convert_train(record, topology, horizon_start=HORIZON_06)

    assert state.traversals[0].enter_minute == -120
    assert state.traversals[0].exit_minute == -90
    assert state.traversals[0].exit_minute > state.traversals[0].enter_minute


# ----------------------------------------------------------------------
# G: missing service_date is rejected when horizon anchoring requested
# ----------------------------------------------------------------------


def test_missing_service_date_is_rejected_when_anchoring(
    topology: CorridorTopology,
):
    record = train("T1", "DOWN-1", [stop("A", "08:00"), stop("B", "08:30")])
    # No service_date supplied.

    with pytest.raises(TimetableConversionError, match="service_date"):
        convert_train(record, topology, horizon_start=HORIZON_MIDNIGHT)


def test_missing_service_date_does_not_abort_the_whole_batch(
    topology: CorridorTopology,
):
    """convert_timetable isolates the failure per-train."""

    good = train(
        "T-GOOD", "DOWN-1",
        [stop("A", "08:00"), stop("B", "08:30")],
        service_date="2026-09-10",
    )
    bad = train("T-BAD", "DOWN-1", [stop("C", "08:00"), stop("D", "08:30")])

    snapshot = convert_timetable(
        [good, bad], topology, horizon_start=HORIZON_MIDNIGHT
    )

    assert [t.train_number for t in snapshot.trains] == ["T-GOOD"]
    assert [r.train_number for r in snapshot.rejections] == ["T-BAD"]


def test_convert_train_without_horizon_start_does_not_require_service_date(
    topology: CorridorTopology,
):
    """Legacy mode never needed service_date and still doesn't."""

    record = train("T1", "DOWN-1", [stop("A", "08:00"), stop("B", "08:30")])

    state = convert_train(record, topology)  # horizon_start=None

    assert state.traversals


def test_service_date_is_preserved_as_source_data_not_rewritten(
    topology: CorridorTopology,
):
    """The record's service_date must be read verbatim, never mutated
    or re-derived - it is the input the caller's record dict carries."""

    record = train(
        "T1", "DOWN-1",
        [stop("A", "08:00"), stop("B", "08:30")],
        service_date="2026-09-10",
    )

    original_service_date = record["service_date"]

    convert_train(record, topology, horizon_start=HORIZON_MIDNIGHT)

    assert record["service_date"] == original_service_date == "2026-09-10"


# ----------------------------------------------------------------------
# F/I: explicit day_offset - agreement accepted, disagreement rejected
# ----------------------------------------------------------------------


def test_explicit_day_offset_matching_inference_is_accepted(
    topology: CorridorTopology,
):
    record = train(
        "T1", "DOWN-1",
        [
            stop("A", "23:30", arrival_day_offset=0, departure_day_offset=0),
            stop("B", "04:30", arrival_day_offset=1, departure_day_offset=1),
        ],
        service_date="2026-09-10",
    )

    state = convert_train(record, topology, horizon_start=HORIZON_MIDNIGHT)

    assert state.traversals[0].enter_minute == 1410
    assert state.traversals[0].exit_minute == 1710


def test_explicit_day_offset_conflicting_with_inference_is_rejected(
    topology: CorridorTopology,
):
    """Task 9.I - the contradiction case.

    The stop sequence's clock times (23:30 -> 04:30) can ONLY be
    explained by a single-day rollover (inferred day_offset 0 then 1).
    Declaring an explicit day_offset of 2 for the second stop
    contradicts that inference and must be rejected outright.
    """

    record = train(
        "T1", "DOWN-1",
        [
            stop("A", "23:30", arrival_day_offset=0, departure_day_offset=0),
            stop("B", "04:30", arrival_day_offset=2, departure_day_offset=2),
        ],
        service_date="2026-09-10",
    )

    with pytest.raises(
        TimetableConversionError, match="Refusing to silently prefer"
    ):
        convert_train(record, topology, horizon_start=HORIZON_MIDNIGHT)


def test_explicit_day_offset_conflict_is_rejected_even_without_horizon_start(
    topology: CorridorTopology,
):
    """Chronology self-consistency is checked regardless of anchoring -
    it is a property of the timetable's own internal data, not of
    whether the caller wants horizon-relative output."""

    record = train(
        "T1", "DOWN-1",
        [
            stop("A", "10:00", arrival_day_offset=0, departure_day_offset=0),
            stop("B", "10:30", arrival_day_offset=5, departure_day_offset=5),
        ],
    )

    with pytest.raises(
        TimetableConversionError, match="Refusing to silently prefer"
    ):
        convert_train(record, topology)


def test_partial_explicit_day_offset_only_some_stops(topology: CorridorTopology):
    """Only some stops need declare explicit offsets; the rest infer."""

    record = train(
        "T1", "DOWN-1",
        [
            stop("A", "23:30"),  # no explicit offset - inferred
            stop("B", "04:30", arrival_day_offset=1, departure_day_offset=1),
        ],
        service_date="2026-09-10",
    )

    state = convert_train(record, topology, horizon_start=HORIZON_MIDNIGHT)

    assert state.traversals[0].enter_minute == 1410
    assert state.traversals[0].exit_minute == 1710


# ----------------------------------------------------------------------
# J: reverse-direction train under horizon anchoring
# ----------------------------------------------------------------------


def test_reverse_direction_train_with_horizon_anchoring(
    topology: CorridorTopology,
):
    record = train(
        "T1", "UP-1",
        [stop("D", "08:00"), stop("C", "08:20"), stop("B", "08:45")],
        direction="UP",
        service_date="2026-09-10",
    )

    state = convert_train(record, topology, horizon_start=HORIZON_MIDNIGHT)

    assert state.section_ids == ("C-D", "B-C")
    assert state.traversals[0].section_id == "C-D"
    assert state.traversals[0].enter_minute == 480
    assert state.traversals[0].exit_minute == 500


# ----------------------------------------------------------------------
# K/L: midnight rollover and multi-day traversal in canonical adapter
# ----------------------------------------------------------------------


def test_midnight_rollover_via_canonical_adapter(topology: CorridorTopology):
    """Task 9.K - the exact brief example, end to end."""

    record = train(
        "T1", "DOWN-1",
        [stop("A", "23:30"), stop("B", "04:30")],
        service_date="2026-09-10",
    )

    state = convert_train(record, topology, horizon_start=HORIZON_MIDNIGHT)

    assert (state.traversals[0].enter_minute, state.traversals[0].exit_minute) == (
        1410,
        1710,
    )


def test_multi_day_traversal_under_horizon_anchoring(topology: CorridorTopology):
    """Task 9.L - a train running two days after service_date."""

    record = train(
        "T1", "DOWN-1",
        [
            stop("A", "23:00"),
            stop("B", "05:00"),
            stop("C", "15:00"),
        ],
        service_date="2026-09-10",
    )

    state = convert_train(
        record, topology, horizon_start=HORIZON_MIDNIGHT, max_rollover_days=2
    )

    # Day 0 23:00 (1380) -> Day 1 05:00 (1740) -> Day 1 15:00 (2340).
    assert [t.enter_minute for t in state.traversals] == [1380, 1740]
    assert [t.exit_minute for t in state.traversals] == [1740, 2340]
    assert state.traversals[-1].exit_minute > 1440


def test_multi_day_horizon_start_offset_combines_with_rollover(
    topology: CorridorTopology,
):
    """A train starting the day AFTER horizon_start's own service_date."""

    record = train(
        "T1", "DOWN-1",
        [stop("A", "08:00"), stop("B", "08:30")],
        service_date="2026-09-12",
    )

    state = convert_train(record, topology, horizon_start=HORIZON_MIDNIGHT)

    # 2 full days (2880) + 08:00 (480) = 3360.
    assert state.traversals[0].enter_minute == 3360
    assert state.traversals[0].exit_minute == 3390


# ----------------------------------------------------------------------
# M: no clamping at 1440 anywhere on this path
# ----------------------------------------------------------------------


def test_no_clamping_at_1440_with_non_midnight_horizon(
    topology: CorridorTopology,
):
    """Combines a non-midnight horizon with a multi-day service_date so
    the result aligns with no 1440 boundary at all - proving nothing
    downstream silently wraps or clips to a day boundary."""

    record = train(
        "T1", "DOWN-1",
        [stop("A", "07:15"), stop("B", "07:45")],
        service_date="2026-09-11",
    )

    state = convert_train(record, topology, horizon_start=HORIZON_06)

    # service_date midnight (2026-09-11T00:00) is 1440 min after horizon
    # start (2026-09-10T06:00... wait, minus 6h = 1080). 07:15 = 435.
    # 1440 - 360 + 435 = 1515.
    assert state.traversals[0].enter_minute == 1515
    assert state.traversals[0].exit_minute == 1545
    # Neither value sits on a clean day boundary or gets wrapped.
    assert state.traversals[0].enter_minute % 1440 != 0


# ----------------------------------------------------------------------
# N: possession derivation from horizon-anchored (>24h, negative) traversals
# ----------------------------------------------------------------------


def test_possession_derivation_from_over_24h_horizon_anchored_traversals(
    topology: CorridorTopology,
):
    """Task 9.N - end to end through possession window derivation."""

    day1 = train(
        "T-DAY1", "DOWN-1",
        [stop("A", "01:00"), stop("B", "01:20")],
        service_date="2026-09-10",
    )
    day2 = train(
        "T-DAY2", "DOWN-1",
        [stop("A", "01:00"), stop("B", "01:20")],
        service_date="2026-09-11",
    )

    snapshot = convert_timetable(
        [day1, day2], topology, horizon_start=HORIZON_MIDNIGHT
    )

    assert not snapshot.has_rejections

    windows = derive_section_possession_windows(
        snapshot, topology, horizon_minutes=2880, safety_buffer_minutes=10,
    )

    ab_windows = sorted(
        (w.start_minute, w.end_minute)
        for w in windows
        if w.section_id == "A-B"
    )

    # Day 1 occupation buffered: 50-90. Day 2 occupation buffered:
    # 1490-1530. Gaps: 0-50, 90-1490, 1530-2880.
    assert ab_windows == [(0, 50), (90, 1490), (1530, 2880)]
    assert ab_windows[-1][1] == 2880  # extends to the full 48h horizon


def test_possession_derivation_drops_occupation_entirely_before_horizon(
    topology: CorridorTopology,
):
    """The occupied_intervals_by_section fix: an event whose buffered
    interval never reaches minute 0 must be dropped, not clipped into a
    bogus (start > end) interval that would corrupt interval merging."""

    record = train(
        "T-PAST", "DOWN-1",
        [stop("A", "01:00"), stop("B", "01:20")],
        service_date="2026-09-01",  # 9 days before horizon_start
    )

    snapshot = convert_timetable(
        [record], topology, horizon_start=HORIZON_MIDNIGHT
    )

    assert not snapshot.has_rejections
    assert snapshot.trains[0].traversals[0].enter_minute < 0

    windows = derive_section_possession_windows(
        snapshot, topology, horizon_minutes=1440,
    )

    # No train has any IN-horizon occupation on A-B, so nothing is
    # derived for it at all - a section with zero recorded occupation
    # yields zero windows (pre-existing Step 5 behavior). What matters
    # is that this stays true rather than surfacing a corrupted window.
    assert [w for w in windows if w.section_id == "A-B"] == []


def test_past_occupation_does_not_corrupt_windows_for_the_same_section(
    topology: CorridorTopology,
):
    """A train with occupation entirely before the horizon must not
    disturb possession derivation for a SECOND train on the same
    section that does have in-horizon occupation."""

    past = train(
        "T-PAST", "DOWN-1",
        [stop("A", "01:00"), stop("B", "01:20")],
        service_date="2026-09-01",  # entirely before the horizon
    )
    present = train(
        "T-PRESENT", "DOWN-1",
        [stop("A", "10:00"), stop("B", "10:20")],
        service_date="2026-09-10",
    )

    snapshot = convert_timetable(
        [past, present], topology, horizon_start=HORIZON_MIDNIGHT
    )

    assert not snapshot.has_rejections

    windows = derive_section_possession_windows(
        snapshot, topology, horizon_minutes=1440, safety_buffer_minutes=10,
    )

    ab_windows = sorted(
        (w.start_minute, w.end_minute)
        for w in windows
        if w.section_id == "A-B"
    )

    # Only T-PRESENT's occupation (600-620, buffered 590-630) is
    # in-horizon; the past train contributes nothing.
    assert ab_windows == [(0, 590), (630, 1440)]


def test_possession_derivation_handles_straddling_minute_zero(
    topology: CorridorTopology,
):
    """An occupation that starts before the horizon and ends after it."""

    record = train(
        "T-STRADDLE", "DOWN-1",
        [stop("A", "23:50"), stop("B", "00:05")],
        service_date="2026-09-09",
    )

    snapshot = convert_timetable(
        [record], topology, horizon_start=HORIZON_MIDNIGHT, max_rollover_days=1
    )

    assert not snapshot.has_rejections

    traversal = snapshot.trains[0].traversals[0]
    assert traversal.enter_minute < 0 < traversal.exit_minute

    windows = derive_section_possession_windows(
        snapshot, topology, horizon_minutes=1440, safety_buffer_minutes=5,
    )

    ab_windows = [w for w in windows if w.section_id == "A-B"]

    # Occupation clipped to start at 0; buffered exit is 5+5=10.
    assert ab_windows[0].start_minute == 10
