"""Shared data schemas and contracts for Pashupatastra.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# Sprint 3 Step 4: absolute monotonic planning horizon.
#
# All solver-facing time values (start_minute, end_minute,
# earliest_start_minute, latest_end_minute, horizon_minutes, ...) stay
# integer minutes RELATIVE TO horizon_start - never floating-point
# timestamps, and never re-based to midnight. A window such as
# 23:30 -> 04:30 next day is simply 1410 -> 1710: monotonically
# increasing, no wraparound, no silent discard.
#
# horizon_start anchors that relative axis to a real wall-clock moment
# and must carry an explicit UTC offset (Asia/Kolkata, +05:30, for the
# railway domain modelled here) so "minute 1410" is unambiguous. A
# naive datetime cannot express which midnight minute 0 actually is,
# so it is rejected outright rather than assumed.
DEFAULT_HORIZON_START = "2026-09-10T00:00:00+05:30"


def _validate_horizon_start(value: str) -> str:
    """Validate an explicit-offset ISO-8601 horizon_start string.

    Kept as a plain string field (not a datetime) so OptimizationRequest
    stays JSON-round-trippable through to_dict/from_dict and through the
    FastAPI request body without a second, coercing datetime type -
    consistent with every other field on this contract being a plain
    int/str/float. This function is the single place that enforces the
    "explicit offset, never naive" rule at construction time.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "horizon_start must be an ISO-8601 datetime string "
            f"with an explicit UTC offset, got {value!r}"
        ) from exc

    if parsed.tzinfo is None:
        raise ValueError(
            "horizon_start must carry an explicit UTC offset (e.g. "
            f"'+05:30' for Asia/Kolkata); got naive datetime {value!r}"
        )

    return value


class WorkType(str, Enum):
    TRACK_RENEWAL = "TRACK_RENEWAL"
    BALLAST_TAMPING = "BALLAST_TAMPING"
    OHE_MAINTENANCE = "OHE_MAINTENANCE"
    SIGNALLING_INTERLOCKING = "SIGNALLING_INTERLOCKING"
    ROUTINE_INSPECTION = "ROUTINE_INSPECTION"
    EMERGENCY_REPAIR = "EMERGENCY_REPAIR"


class DisruptionType(str, Enum):
    TRACK_UNAVAILABLE = "TRACK_UNAVAILABLE"
    EMERGENCY_WORK = "EMERGENCY_WORK"
    POSSESSION_CURTAILMENT = "POSSESSION_CURTAILMENT"
    ASSET_BREAKDOWN = "ASSET_BREAKDOWN"


class SolverStatus(str, Enum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    NO_SOLUTION = "NO_SOLUTION"


class BlockStatus(str, Enum):
    PLANNED = "PLANNED"
    SCHEDULED = "SCHEDULED"
    COMMITTED = "COMMITTED"
    AFFECTED = "AFFECTED"
    UNSCHEDULED = "UNSCHEDULED"
    CANCELLED = "CANCELLED"


@dataclass
class PossessionWindow:
    """A maintenance possession window on one track.

    start_minute/end_minute are integer minutes relative to the
    enclosing OptimizationRequest.horizon_start - an absolute monotonic
    axis, not minutes-from-midnight. A window is free to cross a
    calendar midnight (e.g. 23:30 -> 04:30 next day is start_minute
    1410, end_minute 1710) or span multiple days; nothing here re-bases
    to 0 at each day boundary. The only invalid shape is
    end_minute <= start_minute (zero or negative duration), which the
    solver's possession-window handling in
    backend.app.optimizer.solver.solve treats as an empty/impossible
    window and refuses to schedule into - see the "Skip impossible/
    empty windows" comment there. That check needed no change for
    multi-day horizons: it was never a midnight check, only a
    zero-duration guard.
    """

    window_id: str
    track_id: str
    start_minute: int
    end_minute: int
    # Bare section identifier (e.g. "NDLS-NZM"), never a compound of
    # track_id and a km range. See contracts.BlockCandidate.section_id.
    section_id: Optional[str] = None
    window_type: str = "MAINTENANCE_POSSESSION"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PossessionWindow:
        return cls(
            window_id=data["window_id"],
            track_id=data["track_id"],
            start_minute=int(data["start_minute"]),
            end_minute=int(data["end_minute"]),
            section_id=data.get("section_id"),
            window_type=data.get("window_type", "MAINTENANCE_POSSESSION"),
        )


@dataclass
class BlockCandidate:
    block_id: str
    asset_id: str
    track_id: str
    work_type: str
    duration_minutes: int
    # Bare section identifier. track_id stays the bare physical/logical
    # track id (e.g. "UP-1") - never a compound such as "UP-1:7-32".
    #
    # CANONICAL MEANING (Sprint 3 Step 6 architecture gate): section_id
    # is a STABLE OPAQUE KEY for one inter-station section. Its
    # authoritative definition - endpoints, chainage, track - lives in a
    # backend.app.data.models.Section record; the "<start>-<end>" form
    # (e.g. "NDLS-NZM") is the default naming CONVENTION for that key,
    # not the identity itself. The solver never parses it: matching is
    # exact string equality (see solver._possession_window_covers_block),
    # which is why the format can stay a convention.
    #
    # RESOLVED in Sprint 3 Step 7: there is now exactly ONE section
    # vocabulary. The generator previously wrote TrackSegment.section_id
    # ("SEC-CORRIDOR_A-UP-1") here - a per-track alias with no span,
    # which could never match the station-span sections the timetable
    # adapter produces, so a block keyed one way against a window keyed
    # the other was always refused. Both paths now obtain section ids
    # from backend.app.data.section_registry, and TrackSegment's alias
    # field has been renamed segment_name so it cannot be mistaken for
    # a section again.
    section_id: Optional[str] = None
    earliest_start_minute: int = 0
    # NOT re-validated against horizon_minutes here - Sprint 3 Step 4
    # audit note: this default of 1440 is a single-day-of-work
    # convenience for a candidate that omits the field, matching every
    # existing fixture and the jobs pipeline (see
    # backend.app.jobs.service.OPTIMIZATION_HORIZON_MINUTES, which is
    # coupled to this same 1440 value). It is deliberately left
    # unchanged: bumping it would silently change which candidates are
    # window_infeasible for every checked-in fixture. A caller building
    # a multi-day request MUST set latest_end_minute explicitly past
    # 1440 - see backend/tests/test_horizon_midnight_crossing.py for
    # the pattern.
    latest_end_minute: int = 1440
    priority_score: float = 0.5
    risk_score: float = 0.5
    dependencies: List[str] = field(default_factory=list)
    mutual_exclusion_group: Optional[str] = None
    is_committed: bool = False
    status: str = BlockStatus.PLANNED.value
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BlockCandidate:
        return cls(
            block_id=data["block_id"],
            asset_id=data["asset_id"],
            track_id=data["track_id"],
            work_type=data["work_type"],
            duration_minutes=int(data["duration_minutes"]),
            section_id=data.get("section_id"),
            earliest_start_minute=int(data.get("earliest_start_minute", 0)),
            latest_end_minute=int(data.get("latest_end_minute", 1440)),
            priority_score=float(data.get("priority_score", 0.5)),
            risk_score=float(data.get("risk_score", 0.5)),
            dependencies=list(data.get("dependencies", [])),
            mutual_exclusion_group=data.get("mutual_exclusion_group"),
            is_committed=bool(data.get("is_committed", False)),
            status=data.get("status", BlockStatus.PLANNED.value),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ScheduledBlock:
    block_id: str
    track_id: str
    start_minute: int
    end_minute: int
    work_type: str
    # Bare section identifier, carried through from the scheduled
    # BlockCandidate. See contracts.BlockCandidate.section_id.
    section_id: Optional[str] = None
    priority_score: float = 0.5
    risk_score: float = 0.5
    is_committed: bool = False
    status: str = BlockStatus.SCHEDULED.value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ScheduledBlock:
        return cls(
            block_id=data["block_id"],
            track_id=data["track_id"],
            start_minute=int(data["start_minute"]),
            end_minute=int(data["end_minute"]),
            work_type=data.get("work_type", "UNKNOWN"),
            section_id=data.get("section_id"),
            priority_score=float(data.get("priority_score", 0.5)),
            risk_score=float(data.get("risk_score", 0.5)),
            is_committed=bool(data.get("is_committed", False)),
            status=data.get("status", BlockStatus.SCHEDULED.value),
        )


@dataclass
class OptimizationRequest:
    """The input the CP-SAT solver solves.

    horizon_start + horizon_minutes together define the absolute
    monotonic planning horizon: horizon_start anchors minute 0 to a
    real wall-clock moment (explicit UTC offset required - see
    DEFAULT_HORIZON_START / _validate_horizon_start), and every
    solver-facing time field elsewhere in this contract
    (BlockCandidate.earliest_start_minute/latest_end_minute,
    PossessionWindow.start_minute/end_minute, ScheduledBlock.
    start_minute/end_minute, ...) is an integer minute offset from that
    anchor. horizon_minutes may exceed 1440; nothing in this contract
    assumes a single calendar day.

    horizon_start carries a default (DEFAULT_HORIZON_START) purely so
    every pre-Sprint-3-Step-4 caller - the corridor generator, the jobs
    pipeline, every existing fixture and test - keeps constructing
    valid requests without naming it explicitly. A caller that cares
    about the real anchor moment should always pass it explicitly.
    """

    corridor_id: str
    horizon_minutes: int
    tracks: List[str]
    candidates: List[BlockCandidate]
    possession_windows: List[PossessionWindow] = field(default_factory=list)
    existing_committed_blocks: List[BlockCandidate] = field(default_factory=list)
    min_headway_minutes: int = 15
    train_timetable: List[Dict[str, Any]] = field(default_factory=list)
    horizon_start: str = DEFAULT_HORIZON_START

    def __post_init__(self) -> None:
        self.horizon_start = _validate_horizon_start(self.horizon_start)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "corridor_id": self.corridor_id,
            "horizon_start": self.horizon_start,
            "horizon_minutes": self.horizon_minutes,
            "tracks": self.tracks,
            "candidates": [c.to_dict() for c in self.candidates],
            "possession_windows": [w.to_dict() for w in self.possession_windows],
            "existing_committed_blocks": [c.to_dict() for c in self.existing_committed_blocks],
            "min_headway_minutes": self.min_headway_minutes,
            "train_timetable": self.train_timetable,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OptimizationRequest:
        return cls(
            corridor_id=data["corridor_id"],
            horizon_start=data.get("horizon_start", DEFAULT_HORIZON_START),
            horizon_minutes=int(data.get("horizon_minutes", 1440)),
            tracks=list(data.get("tracks", [])),
            candidates=[BlockCandidate.from_dict(c) for c in data.get("candidates", [])],
            possession_windows=[PossessionWindow.from_dict(w) for w in data.get("possession_windows", [])],
            existing_committed_blocks=[
                BlockCandidate.from_dict(c) for c in data.get("existing_committed_blocks", [])
            ],
            min_headway_minutes=int(data.get("min_headway_minutes", 15)),
            train_timetable=list(data.get("train_timetable", [])),
        )


@dataclass
class OptimizationResult:
    corridor_id: str
    status: str
    scheduled_blocks: List[ScheduledBlock] = field(default_factory=list)
    unscheduled_blocks: List[BlockCandidate] = field(default_factory=list)
    total_priority_scheduled: float = 0.0
    total_risk_mitigated: float = 0.0
    solve_time_seconds: float = 0.0
    infeasibility_reasons: List[str] = field(default_factory=list)
    rejection_reasons: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "corridor_id": self.corridor_id,
            "status": self.status,
            "scheduled_blocks": [b.to_dict() for b in self.scheduled_blocks],
            "unscheduled_blocks": [b.to_dict() for b in self.unscheduled_blocks],
            "total_priority_scheduled": round(self.total_priority_scheduled, 3),
            "total_risk_mitigated": round(self.total_risk_mitigated, 3),
            "solve_time_seconds": round(self.solve_time_seconds, 4),
            "infeasibility_reasons": self.infeasibility_reasons,
            "rejection_reasons": self.rejection_reasons,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OptimizationResult:
        return cls(
            corridor_id=data["corridor_id"],
            status=data["status"],
            scheduled_blocks=[ScheduledBlock.from_dict(b) for b in data.get("scheduled_blocks", [])],
            unscheduled_blocks=[BlockCandidate.from_dict(b) for b in data.get("unscheduled_blocks", [])],
            total_priority_scheduled=float(data.get("total_priority_scheduled", 0.0)),
            total_risk_mitigated=float(data.get("total_risk_mitigated", 0.0)),
            solve_time_seconds=float(data.get("solve_time_seconds", 0.0)),
            infeasibility_reasons=list(data.get("infeasibility_reasons", [])),
            rejection_reasons=dict(data.get("rejection_reasons", {})),
        )


@dataclass
class DisruptionEvent:
    disruption_id: str
    disruption_type: str
    corridor_id: str
    track_id: Optional[str] = None
    start_minute: int = 0
    # Same single-day-default rationale as BlockCandidate.latest_end_minute
    # above - a disruption that omits end_minute defaults to covering
    # only the first day of a request's horizon, not the whole thing.
    # Left unchanged for Sprint 3 Step 4 (Task 9: no fixture behavior
    # change); a caller disrupting a multi-day horizon must pass
    # end_minute explicitly.
    end_minute: int = 1440
    affected_asset_id: Optional[str] = None
    new_candidate: Optional[BlockCandidate] = None
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.new_candidate:
            d["new_candidate"] = self.new_candidate.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DisruptionEvent:
        new_cand = None
        if data.get("new_candidate"):
            new_cand = BlockCandidate.from_dict(data["new_candidate"])
        return cls(
            disruption_id=data["disruption_id"],
            disruption_type=data["disruption_type"],
            corridor_id=data["corridor_id"],
            track_id=data.get("track_id"),
            start_minute=int(data.get("start_minute", 0)),
            end_minute=int(data.get("end_minute", 1440)),
            affected_asset_id=data.get("affected_asset_id"),
            new_candidate=new_cand,
            description=data.get("description", ""),
        )


@dataclass
class RecoveryRequest:
    """POST /recover request body: a plan plus the disruption to apply to it."""

    request: OptimizationRequest
    disruption: DisruptionEvent

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "disruption": self.disruption.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RecoveryRequest:
        return cls(
            request=OptimizationRequest.from_dict(data["request"]),
            disruption=DisruptionEvent.from_dict(data["disruption"]),
        )


@dataclass
class RecoveryResponse:
    """POST /recover response body: the disrupted plan and its recovery result."""

    disruption: DisruptionEvent
    updated_request: OptimizationRequest
    recovery_result: OptimizationResult

    def to_dict(self) -> Dict[str, Any]:
        return {
            "disruption": self.disruption.to_dict(),
            "updated_request": self.updated_request.to_dict(),
            "recovery_result": self.recovery_result.to_dict(),
        }
