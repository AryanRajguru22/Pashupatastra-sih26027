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

    block_candidate: dict


class JobActionResponse(BaseModel):
    job: JobResponse
    message: str