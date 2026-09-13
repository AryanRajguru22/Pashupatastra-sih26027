"""Timetable -> canonical train state -> section possession windows.

This is the Sprint 3 Step 5 replacement for the corridor-length,
positionally-zipped path in backend.app.data.train_adapter. Three
behaviours differ deliberately, and each one is a safety fix:

MIDNIGHT ROLLOVER
    HH:MM carries no day. A train departing 23:30 and arriving 04:30
    has a numerically DECREASING raw sequence (1410, 270). Rather than
    modulo it, or reject it, or silently rewrite 270 as 1710, this
    module resolves the whole stop sequence into absolute
    horizon-relative minutes by walking it in the train's own running
    order and adding a full day whenever the clock wraps. 1410 -> 1710.

    The rollover rule is BOUNDED, which is what keeps it safe. An
    unbounded "add 1440 on every decrease" rule can never reject
    anything - it would silently absorb genuinely corrupt data as a
    sequence of overnight runs. Two bounds apply: max_rollover_days
    caps how many wraps one train may claim, and max_leg_minutes
    rejects any single resolved leg long enough to prove the "wrap"
    was really a reversed timestamp.

SKIP-STOP LEGS
    A train that runs A -> C without stopping at B still occupies both
    section A-B and section B-C. The timetable states only the A
    departure and the C arrival; it does NOT state when the train
    passed B. This module therefore marks EVERY section in the leg
    occupied for the WHOLE leg interval, and labels those traversals
    EXPANDED_LEG_SPAN.

    It deliberately does NOT interpolate a passing time at B from
    segment distances. Distance-proportional interpolation would
    produce a precise-looking boundary time that silently encodes
    assumptions the timetable never made (constant speed, no signal
    check, no dwell). The over-approximation errs toward "occupied",
    which is the fail-safe direction for possession derivation: it can
    only ever shrink a maintenance window, never invent one.

    Cost, stated plainly: possession windows on sections whose
    boundary station is skipped are SHORTER than reality. That is
    accepted; the opposite error would release track under a moving
    train.

SECTION IDENTITY BY STATION PAIR
    Sections are resolved from the station pair the train actually
    runs between, never from list position. This is what fixes the
    silent misattribution described on
    train_adapter.generate_section_possession_windows_from_trains.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from contracts import PossessionWindow

from backend.app.data.canonical_train import (
    MINUTES_PER_DAY,
    CanonicalTrainState,
    CorridorTopology,
    SectionTopologyError,
    SectionTraversal,
    TrainDataProvenance,
    TrainDataSnapshot,
    TrainRejection,
    TraversalBasis,
    TraversalDerivation,
    UnknownStationError,
)
from backend.app.data.horizon_anchor import (
    HorizonAnchorError,
    horizon_relative_minutes,
)


DEFAULT_SAFETY_BUFFER_MINUTES = 10
DEFAULT_MINIMUM_WINDOW_MINUTES = 20

# A train may legitimately cross midnight. Allowing one rollover per
# train covers every overnight run on a corridor of this length. Raise
# it only for genuinely multi-day services, and never to make bad data
# parse.
DEFAULT_MAX_ROLLOVER_DAYS = 1

# No single leg between two consecutive TIMETABLED STOPS may resolve to
# longer than this. This is the bound that distinguishes a real
# overnight wrap from a reversed/corrupt timestamp: 23:30 -> 04:30 is a
# 300-minute leg and passes; a same-day reversal such as 10:00 -> 09:00
# would only "resolve" by claiming a 1380-minute leg, and is rejected.
DEFAULT_MAX_LEG_MINUTES = 12 * 60


class TimetableConversionError(ValueError):
    """A train could not be safely converted into section traversals."""


def parse_clock_minutes(value: str) -> int:
    """Parse HH:MM into minutes-since-that-day's-midnight.

    This is the ONLY place a raw clock value is read, and its result is
    explicitly day-less: it must be passed through
    resolve_absolute_minutes before being used as a horizon-relative
    minute.
    """

    try:
        hours_raw, minutes_raw = str(value).strip().split(":")
        hours = int(hours_raw)
        minutes = int(minutes_raw)
    except (AttributeError, TypeError, ValueError) as exc:
        raise TimetableConversionError(
            f"Cannot parse clock time {value!r}; expected 'HH:MM'."
        ) from exc

    if not (0 <= hours <= 23) or not (0 <= minutes <= 59):
        raise TimetableConversionError(
            f"Clock time {value!r} is not a valid time of day."
        )

    return hours * 60 + minutes


def resolve_absolute_minutes(
    clock_minutes: Sequence[int],
    max_rollover_days: int = DEFAULT_MAX_ROLLOVER_DAYS,
    max_step_minutes: int = DEFAULT_MAX_LEG_MINUTES,
) -> List[int]:
    """Turn a day-less clock sequence into absolute, monotonic minutes.

    The sequence must be given in the train's own running order. Each
    time that is not greater than its predecessor is interpreted as
    having crossed midnight, and a full day is added - but only while
    that interpretation stays within BOTH bounds.

    Raises TimetableConversionError when the sequence cannot be
    explained by rollover, rather than forcing it monotonic.
    """

    if max_rollover_days < 0:
        raise ValueError("max_rollover_days cannot be negative.")

    resolved: List[int] = []
    day_offset = 0

    for position, raw in enumerate(clock_minutes):
        candidate = raw + day_offset * MINUTES_PER_DAY

        if resolved:
            previous = resolved[-1]

            # Strictly less, not <=. Equal consecutive values are
            # legitimate and common: a stop with no dwell has
            # arrival == departure. Treating equality as a midnight
            # crossing would add a phantom day to almost every train.
            if candidate < previous:
                # Interpret as a midnight crossing - if allowed to.
                if day_offset + 1 > max_rollover_days:
                    raise TimetableConversionError(
                        "Timetable sequence is not monotonic and would "
                        f"need more than {max_rollover_days} midnight "
                        f"rollover(s) to resolve (at position {position}). "
                        "Refusing to invent additional days."
                    )

                day_offset += 1
                candidate = raw + day_offset * MINUTES_PER_DAY

            if candidate < previous:
                # Still going backwards after a full day was added: the
                # data is genuinely reversed, not merely wrapped.
                raise TimetableConversionError(
                    "Timetable sequence decreases at position "
                    f"{position} and cannot be explained by a midnight "
                    "rollover."
                )

            step = candidate - previous

            if step > max_step_minutes:
                raise TimetableConversionError(
                    f"Timetable step at position {position} resolves to "
                    f"{step} minutes, which exceeds the maximum "
                    f"plausible leg of {max_step_minutes} minutes. This "
                    "is treated as reversed/corrupt timing rather than "
                    "an overnight run."
                )

        resolved.append(candidate)

    return resolved


def _stop_station_id(stop: Dict[str, Any]) -> str:
    station_id = stop.get("station_id")

    if not station_id:
        raise TimetableConversionError(
            "A timetable stop is missing its station_id."
        )

    return str(station_id)


def _stop_clock_pair(
    stop: Dict[str, Any],
    prefer_actual: bool,
) -> Tuple[int, int, str]:
    """Return (arrival_clock, departure_clock, basis) for one stop.

    A stop may legitimately state only one of the two (an originating
    station has no arrival; a terminating station has no departure), in
    which case the present one stands for both.
    """

    def _read(actual_key: str, scheduled_key: str) -> Tuple[Optional[int], bool]:
        if prefer_actual and stop.get(actual_key):
            return parse_clock_minutes(stop[actual_key]), True

        if stop.get(scheduled_key):
            return parse_clock_minutes(stop[scheduled_key]), False

        return None, False

    arrival, arrival_actual = _read("actual_arrival", "scheduled_arrival")
    departure, departure_actual = _read(
        "actual_departure", "scheduled_departure"
    )

    if arrival is None and departure is None:
        raise TimetableConversionError(
            f"Stop at {_stop_station_id(stop)} has no usable arrival or "
            "departure time."
        )

    if arrival is None:
        arrival = departure

    if departure is None:
        departure = arrival

    basis = (
        TraversalBasis.OBSERVED.value
        if (arrival_actual or departure_actual)
        else TraversalBasis.SCHEDULED.value
    )

    return int(arrival), int(departure), basis


def _stop_explicit_day_offsets(
    stop: Dict[str, Any],
) -> Tuple[Optional[int], Optional[int]]:
    """Read a stop's OWN explicit chronology, if the source supplied it.

    A source that knows which calendar day an event falls on (relative
    to the train's service_date) states it directly via
    arrival_day_offset / departure_day_offset. These are preferred over
    inference - see the "explicit chronology" section of
    convert_train's docstring - but are still cross-checked against
    what plain rollover inference would derive, and rejected on
    disagreement rather than silently trusted.
    """

    def _read(key: str) -> Optional[int]:
        value = stop.get(key)
        return None if value is None else int(value)

    return _read("arrival_day_offset"), _read("departure_day_offset")


def _route_section_ids(
    topology: CorridorTopology,
    station_ids: Sequence[str],
) -> Tuple[str, ...]:
    """Best-effort section list for a declared route, for rejections."""

    sections: List[str] = []

    for first, second in zip(station_ids, station_ids[1:]):
        sections.extend(topology.sections_between(first, second))

    return tuple(dict.fromkeys(sections))


def convert_train(
    record: Dict[str, Any],
    topology: CorridorTopology,
    prefer_actual: bool = True,
    max_rollover_days: int = DEFAULT_MAX_ROLLOVER_DAYS,
    max_leg_minutes: int = DEFAULT_MAX_LEG_MINUTES,
    provenance: str = TrainDataProvenance.SYNTHETIC_SCHEDULED.value,
    observed_at: Optional[str] = None,
    horizon_start: Optional[Union[str, datetime]] = None,
) -> CanonicalTrainState:
    """Convert ONE timetable record into a CanonicalTrainState.

    Raises TimetableConversionError / UnknownStationError /
    SectionTopologyError rather than guessing. Callers wanting
    per-train isolation should use convert_timetable, which collects
    these as TrainRejection data.

    Only the stops the train actually lists are used. There is no
    requirement that they cover the corridor, appear in corridor order,
    or number the same as the corridor's stations.

    HORIZON ANCHORING (Sprint 3 Step 8) - opt-in via horizon_start
        By default (horizon_start=None) this function is unchanged from
        Sprint 3 Step 5: traversal minutes are relative to the train's
        OWN first event, not to any planning horizon. This is preserved
        exactly so every existing caller keeps working unmodified.

        Passing horizon_start switches to the Step 6 contract: every
        resulting SectionTraversal.enter_minute/exit_minute becomes an
        integer minute relative to OptimizationRequest.horizon_start via
        backend.app.data.horizon_anchor.horizon_relative_minutes. In
        this mode record["service_date"] becomes REQUIRED (an
        ISO-8601 'YYYY-MM-DD' string) - see horizon_anchor's module
        docstring for why it is never invented. Its absence is rejected
        with an explicit reason, exactly like any other malformed
        record.

    EXPLICIT CHRONOLOGY
        A stop may carry its own arrival_day_offset / departure_day_offset
        (explicit whole-day offsets from record["service_date"]). Where
        given, these are validated against what plain rollover inference
        over the train's own stop sequence would independently derive
        (see resolve_absolute_minutes) - a MISMATCH is rejected outright,
        never silently resolved by preferring one source over the other.
        This validation runs regardless of horizon_start, since it is a
        property of the timetable's own internal consistency.
    """

    train_number = record.get("train_number") or record.get("train_no")

    if not train_number:
        raise TimetableConversionError(
            "Train record is missing train_number."
        )

    track_id = (
        record.get("track_assignment")
        or record.get("track_id")
    )

    if not track_id:
        raise TimetableConversionError(
            f"Train {train_number} is missing track_assignment."
        )

    stops = record.get("station_times") or []

    if len(stops) < 2:
        raise TimetableConversionError(
            f"Train {train_number} has {len(stops)} timetabled stop(s); "
            "at least two are needed to traverse a section."
        )

    station_ids = [_stop_station_id(stop) for stop in stops]

    # Fail closed on an unknown station: resolving sections around it
    # would silently attribute its movement to the wrong track.
    for station_id in station_ids:
        topology.index_of(station_id)

    # A stop repeated in one route makes "which section" ambiguous.
    if len(set(station_ids)) != len(station_ids):
        raise TimetableConversionError(
            f"Train {train_number} lists a station more than once: "
            f"{station_ids}."
        )

    # Interleave arrival/departure into the single running sequence the
    # rollover resolver walks, so a midnight crossing DURING a dwell is
    # handled as naturally as one during a leg. explicit_day_offsets
    # mirrors this same interleaving so position i in both sequences
    # always refers to the same event.
    clock_sequence: List[int] = []
    bases: List[str] = []
    explicit_day_offsets: List[Optional[int]] = []

    for stop in stops:
        arrival, departure, basis = _stop_clock_pair(stop, prefer_actual)
        clock_sequence.append(arrival)
        clock_sequence.append(departure)
        bases.append(basis)

        arrival_offset, departure_offset = _stop_explicit_day_offsets(stop)
        explicit_day_offsets.append(arrival_offset)
        explicit_day_offsets.append(departure_offset)

    resolved = resolve_absolute_minutes(
        clock_sequence,
        max_rollover_days=max_rollover_days,
        max_step_minutes=max_leg_minutes,
    )

    # Every event's day_offset relative to its OWN first event - exact
    # by construction of resolve_absolute_minutes (each entry is
    # raw_clock + day_offset * MINUTES_PER_DAY for some integer
    # day_offset >= 0).
    inferred_day_offsets = [
        (resolved[i] - clock_sequence[i]) // MINUTES_PER_DAY
        for i in range(len(clock_sequence))
    ]

    # Explicit chronology, where the source supplied it, is preferred -
    # but only after confirming it agrees with what plain rollover
    # inference over the train's own stop sequence independently
    # derives. Disagreement is a genuine data-consistency failure, not
    # a case to silently resolve by picking a side.
    for position, explicit_offset in enumerate(explicit_day_offsets):
        if explicit_offset is None:
            continue

        if explicit_offset != inferred_day_offsets[position]:
            stop_index = position // 2
            field = "arrival" if position % 2 == 0 else "departure"

            raise TimetableConversionError(
                f"Train {train_number} declares an explicit "
                f"{field}_day_offset of {explicit_offset} at "
                f"{station_ids[stop_index]}, but the train's own stop "
                "sequence implies day_offset "
                f"{inferred_day_offsets[position]} at that point. "
                "Refusing to silently prefer one over the other."
            )

    # day_offsets_used is now provably equal to inferred_day_offsets at
    # every position (explicit values that disagreed were rejected
    # above), so resolved already reflects the correct chronology
    # whichever source it came from.

    if horizon_start is not None:
        service_date_raw = record.get("service_date")

        if service_date_raw is None:
            raise TimetableConversionError(
                f"Train {train_number} has no service_date, which is "
                "required to anchor its times to horizon_start. A "
                "service_date is never invented - see "
                "backend.app.data.horizon_anchor module docstring."
            )

        try:
            resolved = [
                horizon_relative_minutes(
                    service_date_raw,
                    clock_sequence[i],
                    inferred_day_offsets[i],
                    horizon_start,
                )
                for i in range(len(clock_sequence))
            ]
        except HorizonAnchorError as exc:
            raise TimetableConversionError(
                f"Train {train_number}: {exc}"
            ) from exc

    # Delay is applied AFTER rollover/horizon resolution. Applying it
    # first can push a time past midnight and manufacture a phantom
    # rollover.
    delay_minutes = int(record.get("delay_minutes", 0) or 0)

    observed_basis = (
        TraversalBasis.OBSERVED.value
        if TraversalBasis.OBSERVED.value in bases
        else (
            TraversalBasis.SCHEDULED_PLUS_DELAY.value
            if delay_minutes
            else TraversalBasis.SCHEDULED.value
        )
    )

    # An OBSERVED time is already the real one; only shift when the
    # timings are scheduled.
    shift = (
        0
        if observed_basis == TraversalBasis.OBSERVED.value
        else delay_minutes
    )

    departures = [resolved[i * 2 + 1] + shift for i in range(len(stops))]
    arrivals = [resolved[i * 2] + shift for i in range(len(stops))]

    traversals: List[SectionTraversal] = []

    for leg_index in range(len(stops) - 1):
        from_station = station_ids[leg_index]
        to_station = station_ids[leg_index + 1]

        leg_start = departures[leg_index]
        leg_end = arrivals[leg_index + 1]

        if leg_end <= leg_start:
            raise TimetableConversionError(
                f"Train {train_number} leg {from_station} -> "
                f"{to_station} has non-positive duration "
                f"({leg_start} -> {leg_end}) after rollover resolution."
            )

        leg_sections = topology.sections_between(from_station, to_station)

        derivation = (
            TraversalDerivation.DIRECT_STOP_PAIR.value
            if len(leg_sections) == 1
            else TraversalDerivation.EXPANDED_LEG_SPAN.value
        )

        # Every section in the leg is occupied for the whole leg. See
        # the module docstring: no interpolation, deliberately.
        for section_id in leg_sections:
            traversals.append(
                SectionTraversal(
                    section_id=section_id,
                    track_id=str(track_id),
                    enter_minute=leg_start,
                    exit_minute=leg_end,
                    basis=observed_basis,
                    derivation=derivation,
                )
            )

    return CanonicalTrainState(
        train_number=str(train_number),
        direction=str(record.get("direction", "")),
        track_id=str(track_id),
        traversals=tuple(traversals),
        observed_at=observed_at,
        source=provenance,
    )


def convert_timetable(
    records: Iterable[Dict[str, Any]],
    topology: CorridorTopology,
    prefer_actual: bool = True,
    max_rollover_days: int = DEFAULT_MAX_ROLLOVER_DAYS,
    max_leg_minutes: int = DEFAULT_MAX_LEG_MINUTES,
    provenance: str = TrainDataProvenance.SYNTHETIC_SCHEDULED.value,
    observed_at: Optional[str] = None,
    horizon_start: Optional[Union[str, datetime]] = None,
) -> TrainDataSnapshot:
    """Convert a whole timetable, isolating per-train failures.

    A malformed train is recorded as a TrainRejection instead of
    aborting the batch - but it is NOT thereby ignored. Possession
    derivation withholds every section a rejected train might have
    occupied, so a rejection can only ever remove maintenance
    opportunity, never create it.

    horizon_start (Sprint 3 Step 8), when given, is passed through to
    every convert_train call - see that function's docstring. A train
    missing the now-required service_date is rejected individually,
    exactly like any other malformed record; it does not abort the
    batch.
    """

    trains: List[CanonicalTrainState] = []
    rejections: List[TrainRejection] = []

    for record in records:
        identifier = str(
            record.get("train_number")
            or record.get("train_no")
            or "UNKNOWN"
        )

        try:
            trains.append(
                convert_train(
                    record,
                    topology,
                    prefer_actual=prefer_actual,
                    max_rollover_days=max_rollover_days,
                    max_leg_minutes=max_leg_minutes,
                    provenance=provenance,
                    observed_at=observed_at,
                    horizon_start=horizon_start,
                )
            )
        except (
            TimetableConversionError,
            UnknownStationError,
            SectionTopologyError,
        ) as exc:
            affected: Tuple[str, ...] = ()
            route_known = False

            # Try to establish which sections this train would have
            # touched, so the blast radius of the rejection is as small
            # as it can honestly be.
            try:
                declared_route = [
                    _stop_station_id(stop)
                    for stop in (record.get("station_times") or [])
                ] or [str(s) for s in (record.get("route") or [])]

                if len(declared_route) >= 2:
                    affected = _route_section_ids(topology, declared_route)
                    route_known = True
            except (
                TimetableConversionError,
                UnknownStationError,
                SectionTopologyError,
            ):
                affected = ()
                route_known = False

            rejections.append(
                TrainRejection(
                    train_number=identifier,
                    reason=str(exc),
                    affected_section_ids=affected,
                    route_known=route_known,
                )
            )

    return TrainDataSnapshot(
        trains=tuple(trains),
        rejections=tuple(rejections),
        provenance=provenance,
        observed_at=observed_at,
    )


def occupied_intervals_by_section(
    trains: Iterable[CanonicalTrainState],
    safety_buffer_minutes: int = DEFAULT_SAFETY_BUFFER_MINUTES,
) -> Dict[Tuple[str, str], List[Tuple[int, int]]]:
    """Buffered train occupation, keyed by (track_id, section_id).

    The key is the same (track_id, section_id) resource identity the
    solver uses - see backend.app.optimizer.solver._resource_key - so
    an UP and a DOWN train on the same physical section stay separate
    resources, exactly as they are operationally.

    Horizon-anchored traversals (Sprint 3 Step 8) may carry a negative
    enter_minute/exit_minute - an event that occurred before
    horizon_start. Such an interval is clipped to start at minute 0
    exactly as before, UNLESS it lies entirely before the horizon even
    with its safety buffer (buffered exit still <= 0), in which case it
    is dropped rather than clipped - clipping start to 0 while leaving
    a negative end would otherwise produce an inverted (start > end)
    interval and corrupt every interval-merge downstream.
    """

    if safety_buffer_minutes < 0:
        raise ValueError("safety_buffer_minutes cannot be negative.")

    by_section: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}

    for train in trains:
        for traversal in train.traversals:
            end = traversal.exit_minute + safety_buffer_minutes

            if end <= 0:
                # Entirely before the horizon, even buffered. No part
                # of this occupation falls inside [0, horizon_minutes),
                # so there is nothing to record.
                continue

            start = max(0, traversal.enter_minute - safety_buffer_minutes)

            by_section.setdefault(
                (traversal.track_id, traversal.section_id),
                [],
            ).append((start, end))

    for intervals in by_section.values():
        intervals.sort()

    return by_section


def _merge_intervals(
    intervals: Sequence[Tuple[int, int]],
) -> List[Tuple[int, int]]:
    """Merge overlapping or touching occupied intervals."""

    if not intervals:
        return []

    ordered = sorted(intervals)
    merged = [ordered[0]]

    for start, end in ordered[1:]:
        previous_start, previous_end = merged[-1]

        if start <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))

    return merged


def derive_section_possession_windows(
    snapshot: TrainDataSnapshot,
    topology: CorridorTopology,
    horizon_minutes: int,
    safety_buffer_minutes: int = DEFAULT_SAFETY_BUFFER_MINUTES,
    minimum_window_minutes: int = DEFAULT_MINIMUM_WINDOW_MINUTES,
) -> List[PossessionWindow]:
    """Train-free gaps per (track_id, section_id), as PossessionWindows.

    Emits canonical contract objects: BARE track_id plus a separate
    section_id. No compound "UP-1:7-32" identifier is produced.

    Fail-closed on rejected trains: any section a rejected train might
    have occupied produces NO window at all. Under the solver's
    possession rules an absent window means work on that section is
    refused, so an unconvertible train can only ever cost maintenance
    opportunity - never grant it.
    """

    if horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive.")

    if minimum_window_minutes <= 0:
        raise ValueError("minimum_window_minutes must be positive.")

    withheld = withheld_section_ids(snapshot, topology)

    occupied = occupied_intervals_by_section(
        snapshot.trains,
        safety_buffer_minutes=safety_buffer_minutes,
    )

    windows: List[PossessionWindow] = []

    for track_id, section_id in sorted(occupied):
        if section_id in withheld:
            continue

        merged = _merge_intervals(occupied[(track_id, section_id)])

        cursor = 0

        def _emit(start: int, end: int) -> None:
            if end - start >= minimum_window_minutes:
                windows.append(
                    PossessionWindow(
                        window_id=(
                            f"POS-{track_id}-{section_id}-"
                            f"{len(windows) + 1:03d}"
                        ),
                        track_id=track_id,
                        start_minute=start,
                        end_minute=end,
                        section_id=section_id,
                        window_type="TRAIN_GAP",
                    )
                )

        for occupied_start, occupied_end in merged:
            clipped_start = max(0, min(horizon_minutes, occupied_start))
            clipped_end = max(0, min(horizon_minutes, occupied_end))

            if clipped_start > cursor:
                _emit(cursor, clipped_start)

            cursor = max(cursor, clipped_end)

        if horizon_minutes > cursor:
            _emit(cursor, horizon_minutes)

    return windows


def withheld_section_ids(
    snapshot: TrainDataSnapshot,
    topology: CorridorTopology,
) -> frozenset[str]:
    """Sections no possession window may be derived for.

    A rejected train whose route IS known contaminates only the
    sections on that route. A rejected train whose route could not even
    be established could have been anywhere, so the whole corridor is
    withheld - the only honest answer when the input is that broken.
    """

    withheld: set[str] = set()

    for rejection in snapshot.rejections:
        if rejection.route_known and rejection.affected_section_ids:
            withheld.update(rejection.affected_section_ids)
        else:
            return frozenset(topology.all_section_ids)

    return frozenset(withheld)


__all__ = [
    "DEFAULT_MAX_ROLLOVER_DAYS",
    "DEFAULT_MAX_LEG_MINUTES",
    "DEFAULT_MINIMUM_WINDOW_MINUTES",
    "DEFAULT_SAFETY_BUFFER_MINUTES",
    "TimetableConversionError",
    "convert_timetable",
    "convert_train",
    "derive_section_possession_windows",
    "occupied_intervals_by_section",
    "parse_clock_minutes",
    "resolve_absolute_minutes",
    "withheld_section_ids",
]
