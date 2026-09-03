"""Shared data schemas and contracts for Pashupatastra.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


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
    window_id: str
    track_id: str
    start_minute: int
    end_minute: int
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
            window_type=data.get("window_type", "MAINTENANCE_POSSESSION"),
        )


@dataclass
class BlockCandidate:
    block_id: str
    asset_id: str
    track_id: str
    work_type: str
    duration_minutes: int
    earliest_start_minute: int = 0
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
            priority_score=float(data.get("priority_score", 0.5)),
            risk_score=float(data.get("risk_score", 0.5)),
            is_committed=bool(data.get("is_committed", False)),
            status=data.get("status", BlockStatus.SCHEDULED.value),
        )


@dataclass
class OptimizationRequest:
    corridor_id: str
    horizon_minutes: int
    tracks: List[str]
    candidates: List[BlockCandidate]
    possession_windows: List[PossessionWindow] = field(default_factory=list)
    existing_committed_blocks: List[BlockCandidate] = field(default_factory=list)
    min_headway_minutes: int = 15
    train_timetable: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "corridor_id": self.corridor_id,
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
