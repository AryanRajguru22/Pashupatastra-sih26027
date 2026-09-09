from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobType(str, Enum):
    TRACK_RENEWAL = "TRACK_RENEWAL"
    BALLAST_TAMPING = "BALLAST_TAMPING"
    OHE_MAINTENANCE = "OHE_MAINTENANCE"
    SIGNALLING_INTERLOCKING = "SIGNALLING_INTERLOCKING"
    ROUTINE_INSPECTION = "ROUTINE_INSPECTION"
    EMERGENCY_REPAIR = "EMERGENCY_REPAIR"


class JobStatus(str, Enum):
    REPORTED = "reported"
    SCHEDULED = "scheduled"
    NOTIFIED = "notified"
    COMPLETED = "completed"


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str = Field(min_length=1)
    job_type: JobType

    distance_start: float = Field(ge=0)
    distance_end: float = Field(gt=0)

    workers_min: int = Field(ge=1)
    workers_max: int = Field(ge=1)

    description: str = Field(min_length=1, max_length=1000)

    @field_validator("distance_end")
    @classmethod
    def validate_distance_range(cls, value: float, info):
        start = info.data.get("distance_start")

        if start is not None and value <= start:
            raise ValueError(
                "distance_end must be greater than distance_start"
            )

        return value

    @field_validator("workers_max")
    @classmethod
    def validate_worker_range(cls, value: int, info):
        minimum = info.data.get("workers_min")

        if minimum is not None and value < minimum:
            raise ValueError(
                "workers_max must be greater than or equal to workers_min"
            )

        return value


class JobResponse(BaseModel):
    job_id: str
    track_id: str
    work_type: str

    distance_start: float
    distance_end: float

    workers_min: int
    workers_max: int

    description: str

    status: JobStatus

    priority_score: float
    risk_score: float

    schedule_start_minute: Optional[int] = None
    schedule_end_minute: Optional[int] = None

    created_at: str
    updated_at: Optional[str] = None

    # Last optimization outcome for this job. Both stay None until an
    # optimization run has actually considered it, so "never optimized"
    # is distinguishable from "considered and refused".
    last_solver_status: Optional[str] = None
    last_refusal_reason: Optional[str] = None

    block_candidate: dict


class JobActionResponse(BaseModel):
    job: JobResponse
    message: str


class ScheduledJobOutcome(BaseModel):
    job_id: str
    track_id: str
    start_minute: int
    end_minute: int
    is_committed: bool


class UnscheduledJobOutcome(BaseModel):
    job_id: str
    track_id: str
    # Verbatim from the solver. Never synthesised to look like success.
    reason: str


class OptimizationCounts(BaseModel):
    considered: int
    committed: int
    scheduled: int
    unscheduled: int


class JobOptimizationResponse(BaseModel):
    """Honest outcome of one corridor optimization batch.

    Reports scheduled and unscheduled work separately so a partially
    successful run can never read as a fully successful one.
    """

    corridor_id: str
    solver_status: str
    solve_time_seconds: float

    # Provenance of the possession data the schedule was built against.
    # Sprint 2 uses deterministic generated windows, reported as
    # GENERATED_STATIC. It is never described as live or real-time.
    possession_source: str
    possession_window_count: int

    # Tracks carrying work that no possession window covers. The solver
    # refuses those blocks rather than scheduling them unprotected.
    uncovered_tracks: list[str]

    generated_at: str

    counts: OptimizationCounts
    scheduled: list[ScheduledJobOutcome]
    unscheduled: list[UnscheduledJobOutcome]
    infeasibility_reasons: list[str]