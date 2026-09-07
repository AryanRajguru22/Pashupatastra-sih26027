"""Train timetable adapter and possession-window generation.

Converts normalized/synthetic train movement records into safe track
occupation intervals and derives the gaps available for maintenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from contracts.schemas import PossessionWindow


DEFAULT_SAFETY_BUFFER_MINUTES = 10


@dataclass(frozen=True)
class TrainMovement:
    """Normalized train movement for one track over one timetable period."""

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
        if key in data and data[key] is not None:
            return data[key]
    return None


def normalize_train(record: Dict[str, Any]) -> TrainMovement:
    """Normalize common train-data field names into TrainMovement."""

    train_no = _first_value(record, "train_no", "train_number", "train_id")
    track_id = _first_value(record, "track_id", "platform_track", "track")

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

    if train_no is None or track_id is None:
        raise ValueError("Each train record needs train_no and track_id.")

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
    """Return buffered occupied intervals grouped by track."""

    if safety_buffer_minutes < 0:
        raise ValueError("safety_buffer_minutes cannot be negative.")

    by_track: Dict[str, List[tuple[int, int]]] = {}

    for raw_train in trains:
        train = (
            raw_train
            if isinstance(raw_train, TrainMovement)
            else normalize_train(raw_train)
        )

        start = max(0, train.effective_start_minute - safety_buffer_minutes)
        end = train.effective_end_minute + safety_buffer_minutes

        if end <= start:
            raise ValueError(
                f"Train {train.train_no} has invalid movement interval."
            )

        by_track.setdefault(train.track_id, []).append((start, end))

    for track_id in by_track:
        by_track[track_id].sort()

    return by_track


def generate_possession_windows_from_trains(
    trains: Iterable[Dict[str, Any] | TrainMovement],
    horizon_minutes: int = 1440,
    safety_buffer_minutes: int = DEFAULT_SAFETY_BUFFER_MINUTES,
    minimum_window_minutes: int = 30,
) -> List[PossessionWindow]:
    """Generate maintenance possession windows from train-free gaps."""

    if horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive.")

    if minimum_window_minutes <= 0:
        raise ValueError("minimum_window_minutes must be positive.")

    occupied = occupied_train_windows(
        trains,
        safety_buffer_minutes=safety_buffer_minutes,
    )

    windows: List[PossessionWindow] = []

    for track_id, intervals in sorted(occupied.items()):
        cursor = 0

        for occupied_start, occupied_end in intervals:
            occupied_start = max(0, min(horizon_minutes, occupied_start))
            occupied_end = max(0, min(horizon_minutes, occupied_end))

            if occupied_start > cursor:
                gap = occupied_start - cursor
                if gap >= minimum_window_minutes:
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

        if cursor < horizon_minutes:
            gap = horizon_minutes - cursor
            if gap >= minimum_window_minutes:
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