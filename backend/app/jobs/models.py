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

    # The optimization run that produced the CURRENT placement - the
    # optimization_runs.run_id holding that solve's request and result.
    # None when the job has no placement, or when its placement was
    # assigned directly rather than proposed by an optimization run.
    proposal_run_id: Optional[str] = None

    block_candidate: dict


class JobActionResponse(BaseModel):
    job: JobResponse
    message: str


class CommitBlockRequest(BaseModel):
    """Optional body for POST /jobs/{job_id}/notify.

    expected_proposal_run_id pins the commit to the proposal the caller
    reviewed: if the job's current proposal came from a different run,
    the commit is refused with 409 and the attempt is recorded. Omitting
    the body keeps the pre-Slice-1 behaviour (commit the current
    proposal).
    """

    model_config = ConfigDict(extra="forbid")

    expected_proposal_run_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
    )


class ActorResponse(BaseModel):
    actor_id: str
    role: str
    kind: str
    # How much the backend knows about this identity. Until an
    # authentication slice exists this is never "authenticated":
    # DECLARED_UNVERIFIED (caller-asserted), NONE (no identity given) or
    # SYSTEM_INTERNAL (the application itself).
    assurance: str


class JobStateResponse(BaseModel):
    status: str
    block_status: str
    is_committed: bool
    schedule_start_minute: Optional[int] = None
    schedule_end_minute: Optional[int] = None
    proposal_run_id: Optional[str] = None


class JobEventResponse(BaseModel):
    """One job lifecycle event, as recorded. Read-only."""

    sequence: int
    event_id: str
    schema_version: int
    entity_type: str
    job_id: str
    event_type: str
    occurred_at: str
    actor: ActorResponse
    reason: Optional[str] = None
    # optimization_runs.run_id this event belongs to, when any.
    optimization_run_id: Optional[str] = None
    before_state: Optional[JobStateResponse] = None
    after_state: Optional[JobStateResponse] = None
    metadata: dict


class JobHistoryResponse(BaseModel):
    job_id: str
    events: list[JobEventResponse]


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


class ProvenanceResponse(BaseModel):
    """Sprint 3 Step 9 canonical provenance, mirroring
    backend.app.data.provenance.ProvenanceProfile.to_dict().

    Four independent axes plus the derived effective value. `effective`
    is always the weakest-link result of the other four fields - it is
    never set independently, because the dict this model validates
    comes straight out of ProvenanceProfile.to_dict(), which computes
    it fresh every time rather than storing it. See
    backend/app/data/provenance.py for the full rule.
    """

    topology: str
    timetable: str
    asset_condition: str
    possession: str
    effective: str


class JobOptimizationResponse(BaseModel):
    """Honest outcome of one corridor optimization batch.

    Reports scheduled and unscheduled work separately so a partially
    successful run can never read as a fully successful one.
    """

    corridor_id: str

    # optimization_runs.run_id of this batch. Every job lifecycle event
    # the batch produced carries the same id.
    optimization_run_id: str

    solver_status: str
    solve_time_seconds: float

    # LEGACY, kept verbatim for backward compatibility - see
    # backend.app.data.provenance.from_possession_source, which is the
    # single place this string is now derived from the canonical
    # `provenance.possession` axis below rather than being an
    # independent source of truth. Sprint 2 uses deterministic
    # generated windows, reported as GENERATED_STATIC. It is never
    # described as live or real-time.
    possession_source: str

    # HOW those windows were computed (Sprint 3 Step 10):
    # CANONICAL_TIMETABLE_DERIVED (train-free gaps from canonical
    # section traversals) or GENERATED_STATIC_SLOTS (fixed operational
    # slots modelling no train movement). A derivation label, NOT a
    # provenance value - both derivations are SYNTHETIC on the
    # provenance axis, so possession_source alone cannot tell them
    # apart. See backend.app.jobs.service.
    possession_derivation: str

    possession_window_count: int

    # Canonical four-axis provenance (Sprint 3 Step 9). possession_source
    # above is mechanically derived from provenance.possession, not set
    # independently - see backend/app/jobs/optimization.py.
    provenance: ProvenanceResponse

    # Tracks carrying work that no possession window covers. The solver
    # refuses those blocks rather than scheduling them unprotected.
    uncovered_tracks: list[str]

    generated_at: str

    counts: OptimizationCounts
    scheduled: list[ScheduledJobOutcome]
    unscheduled: list[UnscheduledJobOutcome]
    infeasibility_reasons: list[str]