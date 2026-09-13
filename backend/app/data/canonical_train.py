"""Canonical train-state domain model (Sprint 3 Step 5).

This is the shape every train data source must be converted INTO before
anything downstream (possession derivation, the optimizer) sees it. It
exists to remove three assumptions baked into
backend.app.data.train_adapter:

  1. That a train has a station time for EVERY corridor station.
     Real trains skip stops, start at an intermediate station and
     terminate short. A CanonicalTrainState carries only the sections
     the train actually traverses.

  2. That a train's station_times can be zipped POSITIONALLY against
     the corridor's station list. That is only true for trains running
     in corridor (increasing-km) order. For a train running the other
     way the two lists are reversed relative to each other, so index i
     of one is not index i of the other - see the defect note on
     backend.app.data.train_adapter.generate_section_possession_windows_from_trains.
     Sections here are always identified by the STATION PAIR the train
     actually runs between, never by list position.

  3. That every time value fits in a single 0..1440 day. Times here are
     integer minutes relative to a planning horizon_start (Sprint 3
     Step 4's absolute monotonic model), so an overnight run is
     1410 -> 1710, not 1410 -> 270.

Section identity vs track identity
----------------------------------
section_id is DIRECTION-INDEPENDENT: the physical piece of railway
between two adjacent stations. Both an UP and a DOWN train crossing
between NDLS and NZM traverse section "NDLS-NZM". Direction is carried
by track_id ("UP-1" / "DOWN-1"), which stays a BARE identifier.

Compound identifiers such as "UP-1:7-32" are NEVER produced here - see
backend/tests/test_station_section_contracts.py, which pins that
track_id and section_id are two separate bare fields on the canonical
contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.app.data.section_registry import format_section_id


MINUTES_PER_DAY = 1440


class TrainDataProvenance(str, Enum):
    """Where a train record's timings ultimately came from.

    Deliberately has NO "LIVE" member. Sprint 3 Step 5 implements no
    live provider, and an enum that cannot express "live" cannot be
    used to mislabel static data as live.
    """

    # Transcribed from a published/official scheduled timetable.
    REAL_SCHEDULED = "REAL_SCHEDULED"

    # Generated or hand-authored demo data that imitates a timetable.
    # Everything currently checked into this repository is this.
    SYNTHETIC_SCHEDULED = "SYNTHETIC_SCHEDULED"


class TraversalBasis(str, Enum):
    """Which timing fields a traversal's minutes were computed from."""

    # scheduled_arrival / scheduled_departure, no delay applied.
    SCHEDULED = "SCHEDULED"

    # actual_arrival / actual_departure were present and preferred.
    OBSERVED = "OBSERVED"

    # scheduled times shifted by the train's reported delay_minutes.
    SCHEDULED_PLUS_DELAY = "SCHEDULED_PLUS_DELAY"


class TraversalDerivation(str, Enum):
    """How a section's occupied interval was obtained."""

    # The train stops at both ends of this section, so the interval is
    # exactly departure(from) -> arrival(to). No approximation.
    DIRECT_STOP_PAIR = "DIRECT_STOP_PAIR"

    # The train runs through one or more stations without stopping, so
    # this section is one of several covered by a single timetable leg.
    # Every section in the leg is marked occupied for the WHOLE leg -
    # a deliberate over-approximation. See
    # backend.app.data.timetable_adapter for why no interpolation is
    # performed.
    EXPANDED_LEG_SPAN = "EXPANDED_LEG_SPAN"


@dataclass(frozen=True)
class SectionTraversal:
    """One train occupying one corridor section over one time interval.

    enter_minute/exit_minute are integer minutes relative to the
    planning horizon_start (Step 4 absolute model) and are strictly
    monotonic: exit_minute > enter_minute always, including across a
    calendar midnight.
    """

    section_id: str
    track_id: str
    enter_minute: int
    exit_minute: int
    basis: str = TraversalBasis.SCHEDULED.value
    derivation: str = TraversalDerivation.DIRECT_STOP_PAIR.value

    def __post_init__(self) -> None:
        if ":" in self.track_id:
            raise ValueError(
                "track_id must stay a bare identifier such as 'UP-1'; "
                f"got compound {self.track_id!r}. section_id is a "
                "separate field - see canonical_train module docstring."
            )

        if self.exit_minute <= self.enter_minute:
            raise ValueError(
                f"SectionTraversal for section {self.section_id!r} on "
                f"track {self.track_id!r} is not monotonic: "
                f"enter={self.enter_minute}, exit={self.exit_minute}. "
                "Cross-midnight runs must already be resolved to "
                "absolute horizon-relative minutes before this point."
            )

    @property
    def duration_minutes(self) -> int:
        return self.exit_minute - self.enter_minute

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section_id": self.section_id,
            "track_id": self.track_id,
            "enter_minute": self.enter_minute,
            "exit_minute": self.exit_minute,
            "basis": self.basis,
            "derivation": self.derivation,
        }


@dataclass(frozen=True)
class CanonicalTrainState:
    """A train reduced to the corridor sections it actually traverses.

    There is deliberately no station_times array and no requirement
    that len(traversals) relate to the corridor's station count. A
    train that only runs C -> F on a seven-station corridor has exactly
    the three traversals C-D, D-E, E-F.
    """

    train_number: str
    direction: str
    track_id: str
    traversals: Tuple[SectionTraversal, ...] = ()
    observed_at: Optional[str] = None
    source: str = TrainDataProvenance.SYNTHETIC_SCHEDULED.value

    def __post_init__(self) -> None:
        if ":" in self.track_id:
            raise ValueError(
                "track_id must stay a bare identifier such as 'UP-1'; "
                f"got compound {self.track_id!r}."
            )

    @property
    def section_ids(self) -> Tuple[str, ...]:
        return tuple(t.section_id for t in self.traversals)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "train_number": self.train_number,
            "direction": self.direction,
            "track_id": self.track_id,
            "traversals": [t.to_dict() for t in self.traversals],
            "observed_at": self.observed_at,
            "source": self.source,
        }


@dataclass(frozen=True)
class TrainRejection:
    """A train that could NOT be safely converted, and why.

    Rejections are returned as data rather than raised, so one
    malformed train does not destroy an otherwise valid batch. They are
    NOT a way to ignore bad data: the sections a rejected train might
    have occupied are withheld from possession derivation entirely -
    see backend.app.data.timetable_adapter.derive_section_possession_windows.
    """

    train_number: str
    reason: str
    # Corridor sections this train may have occupied, as far as could
    # be determined from its declared route. Empty means the route
    # itself could not be established, which is treated as "may have
    # occupied ANY section" by possession derivation.
    affected_section_ids: Tuple[str, ...] = ()
    route_known: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "train_number": self.train_number,
            "reason": self.reason,
            "affected_section_ids": list(self.affected_section_ids),
            "route_known": self.route_known,
        }


@dataclass(frozen=True)
class TrainDataSnapshot:
    """Everything one provider fetch produced: usable trains AND failures."""

    trains: Tuple[CanonicalTrainState, ...] = ()
    rejections: Tuple[TrainRejection, ...] = ()
    provenance: str = TrainDataProvenance.SYNTHETIC_SCHEDULED.value
    observed_at: Optional[str] = None

    @property
    def has_rejections(self) -> bool:
        return bool(self.rejections)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trains": [t.to_dict() for t in self.trains],
            "rejections": [r.to_dict() for r in self.rejections],
            "provenance": self.provenance,
            "observed_at": self.observed_at,
        }


class UnknownStationError(ValueError):
    """A referenced station is not part of the corridor topology."""


class SectionTopologyError(ValueError):
    """Two stations do not describe a usable corridor span."""


@dataclass(frozen=True)
class CorridorTopology:
    """Ordered corridor stations, and the sections between them.

    Built from station records shaped like the corridor.stations
    entries in pashupatastra_realistic_dataset.json (station_id/name/km)
    - the same shape backend.app.data.models.Station.from_dict accepts.

    Stations are held in increasing-km order. Section identity is the
    ordered-by-km station pair, so it is direction-independent:
    "NDLS-NZM" whichever way a train crosses it.
    """

    station_ids: Tuple[str, ...]
    station_km: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_stations(
        cls,
        stations: Sequence[Dict[str, Any]],
    ) -> CorridorTopology:
        if len(stations) < 2:
            raise SectionTopologyError(
                "A corridor needs at least two stations to have any "
                f"section; got {len(stations)}."
            )

        ordered = sorted(stations, key=lambda s: float(s["km"]))

        station_ids = tuple(str(s["station_id"]) for s in ordered)

        if len(set(station_ids)) != len(station_ids):
            raise SectionTopologyError(
                f"Duplicate station_id in corridor topology: {station_ids}"
            )

        return cls(
            station_ids=station_ids,
            station_km={
                str(s["station_id"]): float(s["km"]) for s in ordered
            },
        )

    def index_of(self, station_id: str) -> int:
        try:
            return self.station_ids.index(station_id)
        except ValueError as exc:
            raise UnknownStationError(
                f"Station {station_id!r} is not part of this corridor "
                f"(known stations: {list(self.station_ids)})."
            ) from exc

    def section_id_between_adjacent(
        self,
        first_station_id: str,
        second_station_id: str,
    ) -> str:
        """Canonical section_id for two ADJACENT corridor stations.

        Direction-independent: the pair is normalised into increasing-km
        order, so an UP train and a DOWN train crossing the same
        physical section produce the same section_id.
        """

        low = self.index_of(first_station_id)
        high = self.index_of(second_station_id)

        if abs(low - high) != 1:
            raise SectionTopologyError(
                f"Stations {first_station_id!r} and "
                f"{second_station_id!r} are not adjacent on this "
                "corridor, so they do not bound a single section."
            )

        if low > high:
            low, high = high, low

        # Built through the single canonical construction site so the
        # train adapter and the SectionRegistry can never drift into
        # two spellings of the same section - see Sprint 3 Step 7.
        return format_section_id(
            self.station_ids[low],
            self.station_ids[high],
        )

    def sections_between(
        self,
        from_station_id: str,
        to_station_id: str,
    ) -> Tuple[str, ...]:
        """Every section a train crosses running from one stop to another.

        Handles BOTH directions and skipped stations: a leg from C to F
        on A-B-C-D-E-F-G returns (C-D, D-E, E-F), and the reverse leg
        F to C returns the same three sections (section identity is
        direction-independent) in the order the train crosses them.
        """

        start = self.index_of(from_station_id)
        end = self.index_of(to_station_id)

        if start == end:
            raise SectionTopologyError(
                f"A leg cannot start and end at the same station "
                f"({from_station_id!r})."
            )

        step = 1 if end > start else -1

        positions = range(start, end, step)

        return tuple(
            self.section_id_between_adjacent(
                self.station_ids[position],
                self.station_ids[position + step],
            )
            for position in positions
        )

    @property
    def all_section_ids(self) -> Tuple[str, ...]:
        return tuple(
            format_section_id(
                self.station_ids[i],
                self.station_ids[i + 1],
            )
            for i in range(len(self.station_ids) - 1)
        )


__all__ = [
    "MINUTES_PER_DAY",
    "CanonicalTrainState",
    "CorridorTopology",
    "SectionTopologyError",
    "SectionTraversal",
    "TrainDataProvenance",
    "TrainDataSnapshot",
    "TrainRejection",
    "TraversalBasis",
    "TraversalDerivation",
    "UnknownStationError",
]
