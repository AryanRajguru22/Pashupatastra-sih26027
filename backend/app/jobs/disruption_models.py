"""Request/response models for the /v1 disruption routes."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.app.jobs.disruption import DisruptionKind


class RegisterDisruptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: DisruptionKind
    track_id: str = Field(min_length=1, max_length=64)
    # Required for POSSESSION_CURTAILMENT; optional for TRACK_UNAVAILABLE
    # (omitted = every section of the track).
    section_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    # ISO-8601 with an explicit UTC offset, e.g. 2026-09-10T01:00:00+05:30.
    starts_at: str = Field(min_length=1, max_length=64)
    ends_at: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)


class ClearDisruptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class AffectedJob(BaseModel):
    job_id: str
    status: str
    track_id: str
    section_id: Optional[str] = None
    start_minute: int
    end_minute: int
    disruption_ids: list[str]
    # PROPOSAL_REPLAN | RELEASE_REQUIRED | NOT_COMPLETED_REPORT_REQUIRED
    impact: str
    required_action: str


class DisruptionResponse(BaseModel):
    disruption_id: str
    kind: DisruptionKind
    corridor_id: str
    track_id: str
    section_id: Optional[str] = None
    starts_at: str
    ends_at: str
    reason: str
    declared_by: str
    declared_role: str
    registered_at: str
    # Always SIMULATED_DECLARED: a disruption is a declared planning
    # input, never an observed railway event.
    basis: str
    status: str
    cleared_at: Optional[str] = None
    cleared_by: Optional[str] = None
    clear_reason: Optional[str] = None
    recovery_runs: list[str]
    affected_jobs: list[AffectedJob]
    blocking_recovery: list[str]
    events: Optional[list[dict[str, Any]]] = None


class DisruptionListResponse(BaseModel):
    corridor_id: str
    items: list[DisruptionResponse]


class DisruptionRecoveryResponse(BaseModel):
    disruption: DisruptionResponse
    # The same shape POST /optimize-jobs returns for the recovery run.
    optimization: dict[str, Any]
    # unchanged / moved / newly_unscheduled / newly_scheduled, keyed by job.
    comparison: dict[str, Any]
    active_disruption_ids: list[str]
