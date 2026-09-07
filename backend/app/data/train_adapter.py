"""Train-data adapter and dynamic possession-window generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from contracts.schemas import PossessionWindow


DEFAULT_SAFETY_BUFFER_MINUTES = 10


def _to_minutes(value: str) -> int:
    """Convert HH:MM into minutes from midnight."""
    hours, minutes = map(int, value.split(":"))
    return hours * 60 + minutes


@dataclass(frozen=True)
class TrainMovement:
    """Corridor-level normalized movement for one train."""

    train_no: str
    track_id: str
    scheduled_start_minute: int
    scheduled_end_minute: int
    live_start_minute: int | None = None
    live_end_minute: int | None = None
    delay_minutes: int = 0

    @property
    def effective_start_minute(self) -> int:
        if self.live_start_minute is not None:
            return self.live_start_minute
        return self.scheduled_start_minute + self.delay_minutes

    @property
    def effective_end_minute(self) -> int:
        if self.live_end_minute is not None:
            return self.live_end_minute
        return self.scheduled_end_minute + self.delay_minutes


def _first_value(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if data.get(key) is not None:
            return data[key]
    return None


def normalize_train(record: Dict[str, Any]) -> TrainMovement:
    """Normalize both the project dataset and the earlier flat test format."""

    train_no = _first_value(record, "train_number", "train_no", "train_id")
    track_id = _first_value(
        record,
        "track_assignment",
        "track_id",
        "platform_track",
        "track",
    )

    if train_no is None or track_id is None:
        raise ValueError("Each train record needs train_number and track_assignment.")

    # Project dataset format:
    # station_times[0] = origin
    # station_times[-1] = destination
    station_times = record.get("station_times")

    if station_times:
        first = station_times[0]
        last = station_times[-1]

        scheduled_start = _to_minutes(first["scheduled_departure"])
        scheduled_end = _to_minutes(last["scheduled_arrival"])

        live_start_raw = first.get("actual_departure")
        live_end_raw = last.get("actual_arrival")

        live_start = (
            _to_minutes(live_start_raw) if live_start_raw else None
        )
        live_end = _to_minutes(live_end_raw) if live_end_raw else None

        delay = int(record.get("delay_minutes", 0) or 0)

        return TrainMovement(
            train_no=str(train_no),
            track_id=str(track_id),
            scheduled_start_minute=scheduled_start,
            scheduled_end_minute=scheduled_end,
            live_start_minute=live_start,
            live_end_minute=live_end,
            delay_minutes=delay,
        )

    # Backward-compatible flat format for unit tests and adapters.
    scheduled_start = _first_value(
        record,
        "scheduled_start_minute",
        "scheduled_arrival_minute",
        "scheduled_arrival",
        "scheduled_start",
    )
    scheduled_end = _first_value(
        record,
        "scheduled_end_minute",
        "scheduled_departure_minute",
        "scheduled_departure",
        "scheduled_end",
    )

    if scheduled_start is None or scheduled_end is None:
        raise ValueError(
            f"Train {train_no} needs scheduled start and end times."
        )

    live_start = _first_value(
        record,
        "live_start_minute",
        "live_arrival_minute",
        "live_arrival",
        "live_start",
    )
    live_end = _first_value(
        record,
        "live_end_minute",
        "live_departure_minute",
        "live_departure",
        "live_end",
    )

    delay = int(_first_value(record, "delay_minutes", "delay") or 0)

    return TrainMovement(
        train_no=str(train_no),
        track_id=str(track_id),
        scheduled_start_minute=int(scheduled_start),
        scheduled_end_minute=int(scheduled_end),
        live_start_minute=None if live_start is None else int(live_start),
        live_end_minute=None if live_end is None else int(live_end),
        delay_minutes=delay,
    )


def occupied_train_windows(
    trains: Iterable[Dict[str, Any] | TrainMovement],
    safety_buffer_minutes: int = DEFAULT_SAFETY_BUFFER_MINUTES,
) -> Dict[str, List[tuple[int, int]]]:
    """Return safety-buffered train occupation intervals grouped by track."""

    if safety_buffer_minutes < 0:
        raise ValueError("safety_buffer_minutes cannot be negative.")

    by_track: Dict[str, List[tuple[int, int]]] = {}

    for raw_train in trains:
        train = (
            raw_train
            if isinstance(raw_train, TrainMovement)
            else normalize_train(raw_train)
        )

        start = max(
            0,
            train.effective_start_minute - safety_buffer_minutes,
        )
        end = train.effective_end_minute + safety_buffer_minutes

        if end <= start:
            raise ValueError(
                f"Train {train.train_no} has invalid movement interval."
            )

        by_track.setdefault(train.track_id, []).append((start, end))

    for intervals in by_track.values():
        intervals.sort()

    return by_track


def _merge_intervals(
    intervals: List[tuple[int, int]],
) -> List[tuple[int, int]]:
    """Merge overlapping or touching occupied intervals."""
    if not intervals:
        return []

    merged = [intervals[0]]

    for start, end in intervals[1:]:
        prev_start, prev_end = merged[-1]

        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    return merged


def generate_possession_windows_from_trains(
    trains: Iterable[Dict[str, Any] | TrainMovement],
    horizon_minutes: int = 1440,
    safety_buffer_minutes: int = DEFAULT_SAFETY_BUFFER_MINUTES,
    minimum_window_minutes: int = 30,
) -> List[PossessionWindow]:
    """Generate maintenance possession windows from train-free track gaps."""

    if horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive.")

    if minimum_window_minutes <= 0:
        raise ValueError("minimum_window_minutes must be positive.")

    occupied = occupied_train_windows(
        trains,
        safety_buffer_minutes=safety_buffer_minutes,
    )

    windows: List[PossessionWindow] = []

    for track_id in sorted(occupied):
        intervals = _merge_intervals(occupied[track_id])

        cursor = 0

        for occupied_start, occupied_end in intervals:
            occupied_start = max(0, min(horizon_minutes, occupied_start))
            occupied_end = max(0, min(horizon_minutes, occupied_end))

            if occupied_start > cursor:
                if occupied_start - cursor >= minimum_window_minutes:
                    windows.append(
                        PossessionWindow(
                            window_id=f"POS-{track_id}-{len(windows) + 1:03d}",
                            track_id=track_id,
                            start_minute=cursor,
                            end_minute=occupied_start,
                            window_type="TRAIN_GAP",
                        )
                    )

            cursor = max(cursor, occupied_end)

        if horizon_minutes - cursor >= minimum_window_minutes:
            windows.append(
                PossessionWindow(
                    window_id=f"POS-{track_id}-{len(windows) + 1:03d}",
                    track_id=track_id,
                    start_minute=cursor,
                    end_minute=horizon_minutes,
                    window_type="TRAIN_GAP",
                )
            )

    return windows

def section_track_id(
    track_id: str,
    start_km: float,
    end_km: float,
) -> str:
    """Build the canonical section-qualified track identifier."""

    return f"{track_id}:{start_km:g}-{end_km:g}"


def _effective_station_time(
    station: Dict[str, Any],
    actual_key: str,
    scheduled_key: str,
    delay_minutes: int,
) -> int:
    """Use actual time when available, otherwise scheduled + delay."""

    actual_value = station.get(actual_key)
    if actual_value:
        return _to_minutes(actual_value)

    scheduled_value = station.get(scheduled_key)
    if not scheduled_value:
        raise ValueError(
            f"Missing {actual_key} and {scheduled_key} for station "
            f"{station.get('station_id', 'UNKNOWN')}."
        )

    return _to_minutes(scheduled_value) + delay_minutes


def _merge_named_intervals(
    intervals: List[tuple[int, int]],
) -> List[tuple[int, int]]:
    """Merge overlapping/touching intervals."""

    if not intervals:
        return []

    intervals = sorted(intervals)
    merged = [intervals[0]]

    for start, end in intervals[1:]:
        previous_start, previous_end = merged[-1]

        if start <= previous_end:
            merged[-1] = (
                previous_start,
                max(previous_end, end),
            )
        else:
            merged.append((start, end))

    return merged


def generate_section_possession_windows_from_trains(
    trains: Iterable[Dict[str, Any]],
    stations: List[Dict[str, Any]],
    horizon_minutes: int = 1440,
    safety_buffer_minutes: int = DEFAULT_SAFETY_BUFFER_MINUTES,
    minimum_window_minutes: int = 20,
) -> List[PossessionWindow]:
    """Generate possession windows independently for each track section.

    Each consecutive station pair defines one corridor section. A train
    occupies that section from departure at the first station to arrival
    at the next station. Actual timestamps are preferred; scheduled +
    delay is used when actual data is unavailable.
    """

    if horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive.")

    if safety_buffer_minutes < 0:
        raise ValueError("safety_buffer_minutes cannot be negative.")

    if minimum_window_minutes <= 0:
        raise ValueError("minimum_window_minutes must be positive.")

    if len(stations) < 2:
        raise ValueError("At least two stations are required.")

    # Each key is a section-qualified track_id.
    occupied_by_section: Dict[str, List[tuple[int, int]]] = {}

    station_pairs = list(zip(stations, stations[1:]))

    for raw_train in trains:
        track_id = raw_train.get("track_assignment")
        if not track_id:
            raise ValueError("Train is missing track_assignment.")

        station_times = raw_train.get("station_times", [])
        if len(station_times) != len(stations):
            raise ValueError(
                f"Train {raw_train.get('train_number', 'UNKNOWN')} has "
                f"{len(station_times)} station times but the corridor has "
                f"{len(stations)} stations."
            )

        delay = int(raw_train.get("delay_minutes", 0) or 0)

        for index, (start_station, end_station) in enumerate(station_pairs):
            movement_start = _effective_station_time(
                station_times[index],
                actual_key="actual_departure",
                scheduled_key="scheduled_departure",
                delay_minutes=delay,
            )

            movement_end = _effective_station_time(
                station_times[index + 1],
                actual_key="actual_arrival",
                scheduled_key="scheduled_arrival",
                delay_minutes=delay,
            )

            if movement_end <= movement_start:
                raise ValueError(
                    f"Train {raw_train.get('train_number', 'UNKNOWN')} "
                    f"has invalid passage time for "
                    f"{start_station['station_id']} -> "
                    f"{end_station['station_id']}."
                )

            buffered_start = max(
                0,
                movement_start - safety_buffer_minutes,
            )
            buffered_end = min(
                horizon_minutes,
                movement_end + safety_buffer_minutes,
            )

            qualified_track_id = section_track_id(
                track_id,
                float(start_station["km"]),
                float(end_station["km"]),
            )

            occupied_by_section.setdefault(
                qualified_track_id,
                [],
            ).append((buffered_start, buffered_end))

    windows: List[PossessionWindow] = []

    for qualified_track_id in sorted(occupied_by_section):
        merged = _merge_named_intervals(
            occupied_by_section[qualified_track_id]
        )

        cursor = 0

        for occupied_start, occupied_end in merged:
            if occupied_start > cursor:
                if occupied_start - cursor >= minimum_window_minutes:
                    windows.append(
                        PossessionWindow(
                            window_id=(
                                f"POS-{qualified_track_id}-"
                                f"{len(windows) + 1:03d}"
                            ),
                            track_id=qualified_track_id,
                            start_minute=cursor,
                            end_minute=occupied_start,
                            window_type="TRAIN_GAP",
                        )
                    )

            cursor = max(cursor, occupied_end)

        if horizon_minutes - cursor >= minimum_window_minutes:
            windows.append(
                PossessionWindow(
                    window_id=(
                        f"POS-{qualified_track_id}-"
                        f"{len(windows) + 1:03d}"
                    ),
                    track_id=qualified_track_id,
                    start_minute=cursor,
                    end_minute=horizon_minutes,
                    window_type="TRAIN_GAP",
                )
            )

    return windows


def section_for_km(
    stations: List[Dict[str, Any]],
    km: float,
) -> tuple[str, float, float]:
    """Return the section containing a kilometer location.

    Exact station boundaries belong to the section beginning at that
    station.
    """

    for start, end in zip(stations, stations[1:]):
        start_km = float(start["km"])
        end_km = float(end["km"])

        if start_km <= km < end_km:
            return (
                f"{start['station_id']}-{end['station_id']}",
                start_km,
                end_km,
            )

    # Give the final station boundary to the final section.
    if km == float(stations[-1]["km"]):
        start = stations[-2]
        end = stations[-1]
        return (
            f"{start['station_id']}-{end['station_id']}",
            float(start["km"]),
            float(end["km"]),
        )

    raise ValueError(f"Kilometer {km} is outside the corridor.")


def qualified_track_for_job(
    job: Dict[str, Any],
    stations: List[Dict[str, Any]],
) -> str:
    """Map a maintenance job to its section-qualified track_id."""

    location = job["location_km_range"]
    midpoint = (
        float(location["start"]) + float(location["end"])
    ) / 2.0

    _, start_km, end_km = section_for_km(
        stations,
        midpoint,
    )

    return section_track_id(
        job["track_id"],
        start_km,
        end_km,
    )