"""Sprint 3 Step 5: canonical timetable -> train state -> possession windows.

Covers the three defects the legacy
backend.app.data.train_adapter section path carries (see the LEGACY note
on generate_section_possession_windows_from_trains):

  1. Positional zipping of corridor stations against train stops, which
     silently misattributes every reverse-direction train's occupation
     to the wrong section.
  2. Single-day HH:MM conversion, which rejects overnight runs.
  3. A hard requirement that every train stop at every corridor station.

Plus the fail-closed rules that make partial data safe rather than
merely tolerated.

Corridor used throughout: a synthetic seven-station line A..G, matching
the shape of the brief's example. Nothing here transcribes a real
timetable.
"""

from __future__ import annotations

import pytest

from backend.app.data.canonical_train import (
    CanonicalTrainState,
    CorridorTopology,
    SectionTopologyError,
    SectionTraversal,
    TrainDataProvenance,
    TrainDataSnapshot,
    TraversalBasis,
    TraversalDerivation,
    UnknownStationError,
)
from backend.app.data.timetable_adapter import (
    TimetableConversionError,
    convert_timetable,
    convert_train,
    derive_section_possession_windows,
    occupied_intervals_by_section,
    resolve_absolute_minutes,
    withheld_section_ids,
)
from backend.app.data.train_provider import (
    StaticTimetableProvider,
    possession_windows_from_provider,
)


# ----------------------------------------------------------------------
# Synthetic seven-station corridor: A -> B -> C -> D -> E -> F -> G
# ----------------------------------------------------------------------

STATIONS = [
    {"station_id": "A", "name": "Alpha", "km": 0.0},
    {"station_id": "B", "name": "Bravo", "km": 10.0},
    {"station_id": "C", "name": "Charlie", "km": 25.0},
    {"station_id": "D", "name": "Delta", "km": 40.0},
    {"station_id": "E", "name": "Echo", "km": 60.0},
    {"station_id": "F", "name": "Foxtrot", "km": 85.0},
    {"station_id": "G", "name": "Golf", "km": 100.0},
]


@pytest.fixture
def topology() -> CorridorTopology:
    return CorridorTopology.from_stations(STATIONS)


def stop(station_id: str, arrival: str, departure: str | None = None) -> dict:
    return {
        "station_id": station_id,
        "scheduled_arrival": arrival,
        "scheduled_departure": departure or arrival,
    }


def train(
    train_number: str,
    track_id: str,
    stops: list[dict],
    direction: str = "DOWN",
    **extra,
) -> dict:
    record = {
        "train_number": train_number,
        "track_assignment": track_id,
        "direction": direction,
        "station_times": stops,
    }
    record.update(extra)
    return record


# ----------------------------------------------------------------------
# Topology + section identity (Task 7)
# ----------------------------------------------------------------------


def test_section_id_is_direction_independent(topology: CorridorTopology):
    """The same physical section, whichever way a train crosses it."""

    assert topology.section_id_between_adjacent("A", "B") == "A-B"
    assert topology.section_id_between_adjacent("B", "A") == "A-B"


def test_sections_between_spans_skipped_stations(topology: CorridorTopology):
    assert topology.sections_between("C", "F") == ("C-D", "D-E", "E-F")


def test_sections_between_reverse_direction(topology: CorridorTopology):
    """Reverse leg crosses the same sections, in crossing order."""

    assert topology.sections_between("F", "C") == ("E-F", "D-E", "C-D")


def test_traversal_rejects_compound_track_id():
    with pytest.raises(ValueError, match="bare identifier"):
        SectionTraversal(
            section_id="A-B",
            track_id="UP-1:0-10",
            enter_minute=10,
            exit_minute=20,
        )


def test_traversal_carries_bare_track_id_and_section_id(
    topology: CorridorTopology,
):
    """Task 12.J - canonical output has both fields, neither compound."""

    state = convert_train(
        train("T1", "UP-1", [stop("A", "10:00"), stop("B", "10:20")]),
        topology,
    )

    traversal = state.traversals[0]

    assert traversal.track_id == "UP-1"
    assert traversal.section_id == "A-B"
    assert ":" not in traversal.track_id
    assert ":" not in traversal.section_id


# ----------------------------------------------------------------------
# Midnight rollover (Task 4 / 12.E / 12.F)
# ----------------------------------------------------------------------


def test_resolve_absolute_minutes_passes_monotonic_sequence_through():
    assert resolve_absolute_minutes([600, 620, 700]) == [600, 620, 700]


def test_resolve_absolute_minutes_rolls_over_at_midnight():
    """23:30 -> 04:30 becomes 1410 -> 1710, never 1410 -> 270."""

    assert resolve_absolute_minutes([1410, 270]) == [1410, 1710]


def test_midnight_crossing_train_produces_monotonic_traversal(
    topology: CorridorTopology,
):
    """Task 12.E - the exact failure the Step 4 report identified."""

    state = convert_train(
        train(
            "T-NIGHT",
            "DOWN-1",
            [stop("A", "23:30"), stop("B", "04:30")],
        ),
        topology,
    )

    traversal = state.traversals[0]

    assert traversal.enter_minute == 1410
    assert traversal.exit_minute == 1710
    assert traversal.exit_minute > traversal.enter_minute
    # Explicitly NOT the wrapped encoding.
    assert traversal.exit_minute != 270


def test_multi_day_movement_beyond_one_rollover_is_bounded(
    topology: CorridorTopology,
):
    """Task 12.F - a second rollover needs explicit permission.

    A long-distance service running A 23:00 -> B 05:00(+1d) ->
    C 15:00(+1d) -> D 01:00(+2d). Every individual leg stays plausible
    (6h, 10h, 10h), so only the rollover COUNT is what distinguishes
    this from a one-day train.
    """

    record = train(
        "T-LONG",
        "DOWN-1",
        [
            stop("A", "23:00"),
            stop("B", "05:00"),
            stop("C", "15:00"),
            stop("D", "01:00"),
        ],
    )

    with pytest.raises(TimetableConversionError, match="rollover"):
        convert_train(record, topology, max_rollover_days=1)

    state = convert_train(record, topology, max_rollover_days=2)

    assert [t.enter_minute for t in state.traversals] == [1380, 1740, 2340]
    assert [t.exit_minute for t in state.traversals] == [1740, 2340, 2940]

    # Times keep increasing across both midnights - never re-based.
    assert state.traversals[-1].exit_minute > 2 * 1440


def test_same_day_reversal_is_rejected_not_absorbed_as_rollover(
    topology: CorridorTopology,
):
    """Task 12.G - the bound is what makes rollover safe.

    10:00 -> 09:00 could only "resolve" by claiming a 23-hour leg. An
    unbounded add-a-day rule would silently accept it.
    """

    with pytest.raises(TimetableConversionError, match="exceeds the maximum"):
        convert_train(
            train("T-BAD", "UP-1", [stop("A", "10:00"), stop("B", "09:00")]),
            topology,
        )


def test_zero_length_leg_is_rejected(topology: CorridorTopology):
    with pytest.raises(TimetableConversionError):
        convert_train(
            train("T-ZERO", "UP-1", [stop("A", "10:00"), stop("B", "10:00")]),
            topology,
        )


def test_observed_times_win_over_scheduled_plus_delay(
    topology: CorridorTopology,
):
    """An actual timestamp is the truth; delay is not re-added to it.

    Deliberately uses a record where actual != scheduled + delay, so
    the two derivations produce DIFFERENT minutes and the preference
    rule is actually observable (rather than the two coinciding, as
    they do in the checked-in dataset).
    """

    record = train(
        "T-OBS",
        "DOWN-1",
        [
            {
                "station_id": "A",
                "scheduled_arrival": "10:00",
                "scheduled_departure": "10:00",
                "actual_arrival": "10:07",
                "actual_departure": "10:07",
            },
            {
                "station_id": "B",
                "scheduled_arrival": "10:20",
                "scheduled_departure": "10:20",
                "actual_arrival": "10:31",
                "actual_departure": "10:31",
            },
        ],
        delay_minutes=30,
    )

    observed = convert_train(record, topology).traversals[0]

    assert observed.basis == TraversalBasis.OBSERVED.value
    # The actual times, with the 30-minute delay NOT added on top.
    assert (observed.enter_minute, observed.exit_minute) == (607, 631)

    scheduled = convert_train(
        record, topology, prefer_actual=False
    ).traversals[0]

    assert scheduled.basis == TraversalBasis.SCHEDULED_PLUS_DELAY.value
    # Scheduled 600 -> 620, each shifted by the reported 30-minute delay.
    assert (scheduled.enter_minute, scheduled.exit_minute) == (630, 650)

    # The two derivations genuinely differ - this is what the dataset
    # record above cannot demonstrate.
    assert (observed.enter_minute, observed.exit_minute) != (
        scheduled.enter_minute,
        scheduled.exit_minute,
    )


def test_delay_is_applied_after_rollover_resolution(
    topology: CorridorTopology,
):
    """A delay must not manufacture a phantom midnight rollover."""

    state = convert_train(
        train(
            "T-DELAY",
            "UP-1",
            [stop("A", "23:00"), stop("B", "23:30")],
            delay_minutes=45,
        ),
        topology,
    )

    traversal = state.traversals[0]

    # 1380+45 -> 1410+45. Still one continuous run, no extra day.
    assert (traversal.enter_minute, traversal.exit_minute) == (1425, 1455)
    assert traversal.basis == TraversalBasis.SCHEDULED_PLUS_DELAY.value


# ----------------------------------------------------------------------
# Skip-stop and partial corridor (Tasks 5, 6 / 12.B, 12.C, 12.D)
# ----------------------------------------------------------------------


def test_skip_stop_train_does_not_raise_and_expands_sections(
    topology: CorridorTopology,
):
    """Task 12.B - 7-station corridor, 4-stop train, must not raise."""

    state = convert_train(
        train(
            "T-SKIP",
            "DOWN-1",
            [
                stop("A", "10:00"),
                stop("C", "10:30"),
                stop("F", "11:30"),
                stop("G", "11:50"),
            ],
        ),
        topology,
    )

    assert state.section_ids == (
        "A-B",
        "B-C",
        "C-D",
        "D-E",
        "E-F",
        "F-G",
    )


def test_skip_stop_sections_share_the_whole_leg_interval(
    topology: CorridorTopology,
):
    """No interpolated boundary time is invented for a skipped station.

    Both sections of the A->C leg carry the FULL leg interval, and are
    labelled as the over-approximation they are.
    """

    state = convert_train(
        train(
            "T-SKIP",
            "DOWN-1",
            [stop("A", "10:00"), stop("C", "10:30"), stop("D", "10:50")],
        ),
        topology,
    )

    by_section = {t.section_id: t for t in state.traversals}

    assert (by_section["A-B"].enter_minute, by_section["A-B"].exit_minute) == (
        600,
        630,
    )
    assert (by_section["B-C"].enter_minute, by_section["B-C"].exit_minute) == (
        600,
        630,
    )
    assert (
        by_section["A-B"].derivation
        == TraversalDerivation.EXPANDED_LEG_SPAN.value
    )

    # The C->D leg has a stop at both ends, so it is exact.
    assert (
        by_section["C-D"].derivation
        == TraversalDerivation.DIRECT_STOP_PAIR.value
    )


def test_partial_corridor_train_produces_only_its_own_sections(
    topology: CorridorTopology,
):
    """Task 12.C - starts at C, terminates at F. Nothing before/after."""

    state = convert_train(
        train(
            "T-PARTIAL",
            "DOWN-1",
            [
                stop("C", "10:00"),
                stop("D", "10:20"),
                stop("E", "10:45"),
                stop("F", "11:10"),
            ],
        ),
        topology,
    )

    assert state.section_ids == ("C-D", "D-E", "E-F")
    assert "A-B" not in state.section_ids
    assert "B-C" not in state.section_ids
    assert "F-G" not in state.section_ids


def test_reverse_direction_partial_train(topology: CorridorTopology):
    """Task 12.D - F down to C crosses the same sections, reversed."""

    state = convert_train(
        train(
            "T-REV",
            "UP-1",
            [
                stop("F", "10:00"),
                stop("E", "10:25"),
                stop("D", "10:50"),
                stop("C", "11:10"),
            ],
            direction="UP",
        ),
        topology,
    )

    assert state.section_ids == ("E-F", "D-E", "C-D")
    assert state.traversals[0].section_id == "E-F"
    assert state.traversals[0].enter_minute == 600


def test_reverse_direction_sections_are_not_positionally_misattributed(
    topology: CorridorTopology,
):
    """The legacy defect this step exists to fix.

    A reverse-direction train's FIRST leg (F->E) must be attributed to
    section E-F - not, as positional zipping would have it, to section
    A-B at the other end of the corridor.
    """

    state = convert_train(
        train(
            "T-REV-FULL",
            "UP-1",
            [
                stop("G", "10:00"),
                stop("F", "10:15"),
                stop("E", "10:40"),
                stop("D", "11:05"),
                stop("C", "11:25"),
                stop("B", "11:45"),
                stop("A", "12:00"),
            ],
            direction="UP",
        ),
        topology,
    )

    first = state.traversals[0]

    assert first.section_id == "F-G"
    assert first.section_id != "A-B"
    assert (first.enter_minute, first.exit_minute) == (600, 615)

    # And the LAST leg is the one at the low-km end of the corridor.
    assert state.traversals[-1].section_id == "A-B"


# ----------------------------------------------------------------------
# Fail-closed safety (Task 10 / 12.H, 12.I)
# ----------------------------------------------------------------------


def test_unknown_station_is_rejected(topology: CorridorTopology):
    """Task 12.H."""

    with pytest.raises(UnknownStationError, match="not part of this corridor"):
        convert_train(
            train("T-UNK", "UP-1", [stop("A", "10:00"), stop("ZZZ", "10:20")]),
            topology,
        )


def test_station_repeated_in_route_is_rejected(topology: CorridorTopology):
    with pytest.raises(TimetableConversionError, match="more than once"):
        convert_train(
            train(
                "T-DUP",
                "UP-1",
                [stop("A", "10:00"), stop("B", "10:20"), stop("A", "10:40")],
            ),
            topology,
        )


def test_single_stop_train_is_rejected(topology: CorridorTopology):
    with pytest.raises(TimetableConversionError, match="at least two"):
        convert_train(
            train("T-ONE", "UP-1", [stop("A", "10:00")]), topology
        )


def test_missing_track_assignment_is_rejected(topology: CorridorTopology):
    record = {
        "train_number": "T-NOTRACK",
        "station_times": [stop("A", "10:00"), stop("B", "10:20")],
    }

    with pytest.raises(TimetableConversionError, match="track_assignment"):
        convert_train(record, topology)


def test_missing_timing_information_is_rejected(topology: CorridorTopology):
    record = train(
        "T-NOTIME",
        "UP-1",
        [{"station_id": "A"}, stop("B", "10:20")],
    )

    with pytest.raises(TimetableConversionError, match="no usable arrival"):
        convert_train(record, topology)


def test_topology_needs_at_least_two_stations():
    with pytest.raises(SectionTopologyError, match="at least two stations"):
        CorridorTopology.from_stations([STATIONS[0]])


def test_non_adjacent_stations_do_not_bound_one_section(
    topology: CorridorTopology,
):
    """Task 12.I - topology mismatch is refused, not guessed at."""

    with pytest.raises(SectionTopologyError, match="not adjacent"):
        topology.section_id_between_adjacent("A", "C")


def test_one_bad_train_does_not_destroy_the_batch(topology: CorridorTopology):
    snapshot = convert_timetable(
        [
            train("T-GOOD", "DOWN-1", [stop("A", "10:00"), stop("B", "10:20")]),
            train("T-BAD", "DOWN-1", [stop("E", "10:00"), stop("ZZZ", "10:20")]),
        ],
        topology,
    )

    assert [t.train_number for t in snapshot.trains] == ["T-GOOD"]
    assert [r.train_number for r in snapshot.rejections] == ["T-BAD"]
    assert snapshot.has_rejections


def test_rejected_train_withholds_only_its_own_sections(
    topology: CorridorTopology,
):
    """A rejection must not silently make its sections look free."""

    snapshot = convert_timetable(
        [
            train(
                "T-BAD",
                "DOWN-1",
                # Reversed timing on a known route: route IS resolvable.
                [stop("C", "10:00"), stop("D", "09:00")],
            ),
        ],
        topology,
    )

    assert snapshot.rejections
    rejection = snapshot.rejections[0]

    assert rejection.route_known is True
    assert rejection.affected_section_ids == ("C-D",)
    assert withheld_section_ids(snapshot, topology) == frozenset({"C-D"})


def test_unresolvable_route_withholds_the_whole_corridor(
    topology: CorridorTopology,
):
    """If we cannot tell where a broken train ran, nothing is released."""

    snapshot = convert_timetable(
        [
            train("T-BAD", "DOWN-1", [stop("A", "10:00"), stop("ZZZ", "10:20")]),
        ],
        topology,
    )

    assert snapshot.rejections[0].route_known is False
    assert withheld_section_ids(snapshot, topology) == frozenset(
        topology.all_section_ids
    )


def test_withheld_section_produces_no_possession_window(
    topology: CorridorTopology,
):
    """Fail closed end-to-end: no window means the solver refuses work."""

    snapshot = convert_timetable(
        [
            train("T-OK", "DOWN-1", [stop("A", "10:00"), stop("B", "10:20")]),
            train("T-BAD", "DOWN-1", [stop("C", "10:00"), stop("D", "09:00")]),
        ],
        topology,
    )

    windows = derive_section_possession_windows(
        snapshot,
        topology,
        horizon_minutes=1440,
    )

    covered = {w.section_id for w in windows}

    assert "A-B" in covered
    assert "C-D" not in covered


# ----------------------------------------------------------------------
# Possession derivation (Task 9 / 12.K)
# ----------------------------------------------------------------------


def test_possession_windows_are_the_gaps_around_train_occupation(
    topology: CorridorTopology,
):
    """Task 12.K."""

    snapshot = convert_timetable(
        [train("T1", "DOWN-1", [stop("A", "10:00"), stop("B", "10:20")])],
        topology,
    )

    windows = derive_section_possession_windows(
        snapshot,
        topology,
        horizon_minutes=1440,
        safety_buffer_minutes=10,
        minimum_window_minutes=20,
    )

    ab_windows = [w for w in windows if w.section_id == "A-B"]

    # Occupation 600-620, buffered to 590-630.
    assert (ab_windows[0].start_minute, ab_windows[0].end_minute) == (0, 590)
    assert (ab_windows[1].start_minute, ab_windows[1].end_minute) == (630, 1440)


def test_possession_windows_carry_bare_track_id_and_section_id(
    topology: CorridorTopology,
):
    snapshot = convert_timetable(
        [train("T1", "DOWN-1", [stop("A", "10:00"), stop("B", "10:20")])],
        topology,
    )

    windows = derive_section_possession_windows(
        snapshot, topology, horizon_minutes=1440
    )

    for window in windows:
        assert ":" not in window.track_id
        assert window.track_id == "DOWN-1"
        assert window.section_id in topology.all_section_ids
        assert window.window_type == "TRAIN_GAP"


def test_opposite_direction_trains_are_separate_section_resources(
    topology: CorridorTopology,
):
    """Same section_id, different track_id - matching the solver's key."""

    snapshot = convert_timetable(
        [
            train("T-DOWN", "DOWN-1", [stop("A", "10:00"), stop("B", "10:20")]),
            train(
                "T-UP",
                "UP-1",
                [stop("B", "10:00"), stop("A", "10:20")],
                direction="UP",
            ),
        ],
        topology,
    )

    occupied = occupied_intervals_by_section(snapshot.trains)

    # One physical section, two independent track resources - the same
    # (track_id, section_id) identity the solver keys on.
    assert ("DOWN-1", "A-B") in occupied
    assert ("UP-1", "A-B") in occupied

    # Each carries only its OWN train's occupation; they are not merged
    # just because the section_id matches.
    assert occupied[("DOWN-1", "A-B")] == [(590, 630)]
    assert occupied[("UP-1", "A-B")] == [(590, 630)]
    assert len(occupied[("DOWN-1", "A-B")]) == 1


def test_cross_midnight_occupation_produces_windows_past_1440(
    topology: CorridorTopology,
):
    """The Step 4 absolute horizon carried through to possession output."""

    snapshot = convert_timetable(
        [train("T-NIGHT", "DOWN-1", [stop("A", "23:30"), stop("B", "04:30")])],
        topology,
    )

    windows = derive_section_possession_windows(
        snapshot,
        topology,
        horizon_minutes=2880,
        safety_buffer_minutes=10,
    )

    ab = [w for w in windows if w.section_id == "A-B"]

    # Buffered occupation 1400-1720 splits the 48h horizon in two.
    assert (ab[0].start_minute, ab[0].end_minute) == (0, 1400)
    assert (ab[1].start_minute, ab[1].end_minute) == (1720, 2880)


# ----------------------------------------------------------------------
# Provider boundary (Task 11) + provenance (Task 8)
# ----------------------------------------------------------------------


def test_static_provider_returns_a_canonical_snapshot(
    topology: CorridorTopology,
):
    provider = StaticTimetableProvider(
        [train("T1", "DOWN-1", [stop("A", "10:00"), stop("B", "10:20")])],
        topology,
    )

    snapshot = provider.fetch()

    assert isinstance(snapshot, TrainDataSnapshot)
    assert isinstance(snapshot.trains[0], CanonicalTrainState)
    assert snapshot.provenance == TrainDataProvenance.SYNTHETIC_SCHEDULED.value


def test_provider_can_label_real_scheduled_provenance(
    topology: CorridorTopology,
):
    """Task 8 - REAL_SCHEDULED is expressible; LIVE is not."""

    provider = StaticTimetableProvider(
        [train("T1", "DOWN-1", [stop("A", "10:00"), stop("B", "10:20")])],
        topology,
        provenance=TrainDataProvenance.REAL_SCHEDULED.value,
    )

    snapshot = provider.fetch()

    assert snapshot.provenance == "REAL_SCHEDULED"
    assert snapshot.trains[0].source == "REAL_SCHEDULED"


def test_provenance_has_no_live_value():
    values = {member.value for member in TrainDataProvenance}

    assert "LIVE" not in values
    assert not any("LIVE" in value for value in values)


def test_provider_rejects_unknown_provenance(topology: CorridorTopology):
    with pytest.raises(ValueError, match="no LIVE value"):
        StaticTimetableProvider([], topology, provenance="LIVE_FEED")


def test_provider_to_possession_windows_end_to_end(
    topology: CorridorTopology,
):
    provider = StaticTimetableProvider(
        [
            train("T1", "DOWN-1", [stop("A", "10:00"), stop("B", "10:20")]),
            train("T2", "DOWN-1", [stop("A", "14:00"), stop("B", "14:20")]),
        ],
        topology,
    )

    windows, snapshot = possession_windows_from_provider(
        provider,
        topology,
        horizon_minutes=1440,
    )

    assert not snapshot.has_rejections
    assert windows
    assert all(w.section_id for w in windows)
    assert all(":" not in w.track_id for w in windows)


# ----------------------------------------------------------------------
# Checked-in dataset: the legacy defect, demonstrated and fixed (12.A)
# ----------------------------------------------------------------------


def _load_dataset() -> dict:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / (
        "pashupatastra_realistic_dataset.json"
    )

    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def test_checked_in_dataset_converts_without_rejections():
    """Every train in the shipped synthetic dataset converts cleanly."""

    data = _load_dataset()
    corridor_topology = CorridorTopology.from_stations(
        data["corridor"]["stations"]
    )

    snapshot = convert_timetable(data["trains"], corridor_topology)

    # 24 synthetic trains per service date x 2 dates (Slice 4 Step 7).
    assert len(snapshot.trains) == 48
    assert snapshot.rejections == ()


def test_reverse_direction_dataset_train_is_attributed_to_the_right_section():
    """The headline Step 5 fix, on the real checked-in data.

    Train 12050 runs AGC -> NDLS, so its station_times are in the
    OPPOSITE order to the corridor's station list. Its first leg
    (AGC 01:08 -> RKM 01:14) belongs to section RKM-AGC.

    The legacy positional-zip path attributes that same leg to section
    NDLS-NZM - 190 km away, at the other end of the corridor - and
    raises nothing while doing it.
    """

    data = _load_dataset()
    corridor_topology = CorridorTopology.from_stations(
        data["corridor"]["stations"]
    )

    record = next(t for t in data["trains"] if t["train_number"] == "12050")

    assert record["direction"] == "UP"
    assert [s["station_id"] for s in record["station_times"]][0] == "AGC"

    state = convert_train(record, corridor_topology)

    first = state.traversals[0]

    # The defect being fixed: this leg is NOT the NDLS-NZM section.
    assert first.section_id == "RKM-AGC"
    assert first.section_id != "NDLS-NZM"

    # This train reports a 5-minute delay AND carries actual times, so
    # the observed timings win: AGC actual departure 01:13 (73) ->
    # RKM actual arrival 01:19 (79). delay_minutes is NOT re-applied on
    # top of an already-observed time.
    #
    # NOTE: in this particular record actual == scheduled + delay, so
    # the two bases coincide numerically. The basis label is therefore
    # the only thing that distinguishes them here; the numeric
    # divergence is pinned separately, on data where actual and
    # scheduled+delay genuinely differ, by
    # test_observed_times_win_over_scheduled_plus_delay.
    assert first.basis == TraversalBasis.OBSERVED.value
    assert (first.enter_minute, first.exit_minute) == (73, 79)

    scheduled_state = convert_train(
        record, corridor_topology, prefer_actual=False
    )

    assert (
        scheduled_state.traversals[0].basis
        == TraversalBasis.SCHEDULED_PLUS_DELAY.value
    )

    # And the NDLS-NZM section is traversed last by this train.
    assert state.traversals[-1].section_id == "NDLS-NZM"


def test_dataset_section_ids_are_bare_station_pairs_not_compound():
    data = _load_dataset()
    corridor_topology = CorridorTopology.from_stations(
        data["corridor"]["stations"]
    )

    snapshot = convert_timetable(data["trains"], corridor_topology)

    windows = derive_section_possession_windows(
        snapshot,
        corridor_topology,
        horizon_minutes=1440,
        safety_buffer_minutes=10,
        minimum_window_minutes=20,
    )

    assert windows

    for window in windows:
        assert ":" not in window.track_id
        assert window.track_id in {"UP-1", "DOWN-1"}
        assert window.section_id in corridor_topology.all_section_ids


def test_dataset_windows_reach_the_solver_and_constrain_it():
    """Task 12.L-adjacent: canonical windows drive the real solver.

    Uses the section-aware (track_id, section_id) resource identity from
    Step 3, with candidates carrying a bare track_id plus section_id.
    """

    from backend.app.optimizer.solver import solve
    from contracts import BlockCandidate, OptimizationRequest

    data = _load_dataset()
    corridor_topology = CorridorTopology.from_stations(
        data["corridor"]["stations"]
    )

    snapshot = convert_timetable(data["trains"], corridor_topology)

    windows = derive_section_possession_windows(
        snapshot,
        corridor_topology,
        horizon_minutes=1440,
        safety_buffer_minutes=10,
        minimum_window_minutes=20,
    )

    # Select explicitly rather than by emission order, so a future
    # ordering change surfaces as a real assertion failure instead of a
    # confusing StopIteration on a too-short window.
    target = next(
        w for w in windows if w.end_minute - w.start_minute >= 60
    )

    candidate = BlockCandidate(
        block_id="BLK-CANON-1",
        asset_id="AST-CANON-1",
        track_id=target.track_id,
        work_type="ROUTINE_INSPECTION",
        duration_minutes=30,
        section_id=target.section_id,
        earliest_start_minute=target.start_minute,
        latest_end_minute=target.end_minute,
        priority_score=1.0,
        risk_score=1.0,
    )

    request = OptimizationRequest(
        corridor_id=data["corridor"]["corridor_id"],
        horizon_minutes=1440,
        tracks=sorted({w.track_id for w in windows}),
        candidates=[candidate],
        possession_windows=windows,
        min_headway_minutes=15,
    )

    result = solve(request)

    assert result.status in {"OPTIMAL", "FEASIBLE"}

    scheduled = next(
        b for b in result.scheduled_blocks if b.block_id == "BLK-CANON-1"
    )

    assert scheduled.section_id == target.section_id
    assert scheduled.start_minute >= target.start_minute
    assert scheduled.end_minute <= target.end_minute
