from __future__ import annotations

from datetime import date as _date
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.data.models import DefectSeverity


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
    # Sprint 3 Slice 5: field work on an approved (committed) block has
    # started. Committed like NOTIFIED - see backend.app.jobs.lifecycle.
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class FieldLocation(BaseModel):
    """A location in the terms a field worker actually has (Slice 8).

    Offsets are metres from `from_station_id` toward `toward_station_id`
    along the one section joining those two stations. Converted to
    corridor-absolute metres by backend.app.jobs.field_location through
    the existing SectionRegistry - never stored as a second location
    model. See JobCreateRequest.field_location for how it is used.
    """

    model_config = ConfigDict(extra="forbid")

    from_station_id: str = Field(min_length=1, max_length=64)
    toward_station_id: str = Field(min_length=1, max_length=64)
    offset_start_m: float = Field(ge=0, allow_inf_nan=False)
    offset_end_m: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("offset_end_m")
    @classmethod
    def validate_offset_range(cls, value: float, info):
        start = info.data.get("offset_start_m")

        if start is not None and value <= start:
            raise ValueError(
                "offset_end_m must be greater than offset_start_m"
            )

        return value


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str = Field(min_length=1)
    job_type: JobType

    distance_start: float = Field(ge=0)
    distance_end: float = Field(gt=0)

    workers_min: int = Field(ge=1)
    workers_max: int = Field(ge=1)

    description: str = Field(min_length=1, max_length=1000)

    # Sprint 3 Slice 2. Optional so every pre-Slice-2 caller (22 existing
    # construction sites, none of which set this) keeps working
    # unchanged. When given, this is the WORKER's own assessment of the
    # defect and is what the scorer is run against - see JobService.
    # create_job - instead of falling back to whatever condition was
    # last recorded against the nearest asset. Typed as the enum, not a
    # free string, so an unrecognised value is a 422 at the boundary
    # rather than a silently wrong score.
    severity: Optional[DefectSeverity] = None

    # A pointer to inspection evidence (e.g. a photo/report id) already
    # held by another system. This module does not store or validate the
    # evidence itself - only the reference, inside block_candidate.
    # metadata (see backend.app.jobs.service), which is where the
    # existing architecture already carries free-form job context.
    evidence_reference: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=200,
    )

    # Optional defence-in-depth: this deployment already serves exactly
    # one corridor (JobService.corridor), so this field is never used to
    # ROUTE the request - it is validated, when given, against the
    # service's own corridor_id and rejected (400) on a mismatch, so a
    # caller that names the wrong corridor fails closed rather than
    # having its job silently filed against a corridor it did not name.
    corridor_id: Optional[str] = Field(default=None, min_length=1)

    # Sprint 3 Slice 8. BOTH new fields are optional, so every request
    # valid before Slice 8 is still valid and behaves identically, and
    # JobCreateRequest.required (which the frozen v1 contract guard
    # compares) is unchanged.
    #
    # field_location: the span in human terms. distance_start/
    # distance_end stay REQUIRED (relaxing them would change `required`,
    # a v1 contract change), so this is a CROSS-CHECK: it is converted
    # through SectionRegistry and must describe exactly the declared
    # span, else the request is refused (LOCATION_INPUT_CONFLICT). It is
    # never a second source of location truth.
    field_location: Optional[FieldLocation] = None

    # idempotency_key: makes a retried submission safe. The same key from
    # the same actor with the same request returns the job the first
    # submission created (no second job); the same key with a different
    # request is refused (409 IDEMPOTENCY_KEY_CONFLICT). Scoped to the
    # declared actor_id, so it requires actor headers. See
    # JobService.create_job_with_outcome for the guarantee and its limits.
    idempotency_key: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:\-]+$",
    )

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


class JobListResponse(BaseModel):
    """GET /v1/jobs - one page of jobs, newest first.

    next_cursor is opaque: pass it back verbatim as ?cursor= to fetch the
    following page. None means this was the last page.
    """

    items: list[JobResponse]
    next_cursor: Optional[str] = None


class JobActionResponse(BaseModel):
    job: JobResponse
    message: str


class CommitBlockRequest(BaseModel):
    """Body for POST /v1/jobs/{job_id}/notify.

    expected_proposal_run_id is REQUIRED (Slice 7): the v1 contract has
    no unpinned commit path. It pins the commit to the proposal the
    caller reviewed - if the job's current proposal came from a different
    run, the commit is refused with 409 STALE_PROPOSAL and the attempt is
    recorded. The in-process JobService.notify keeps its optional
    parameter for direct callers; only the HTTP contract requires it.
    """

    model_config = ConfigDict(extra="forbid")

    expected_proposal_run_id: str = Field(
        min_length=1,
        max_length=128,
    )


class ApproveProposalRequest(BaseModel):
    """Body for POST /jobs/{job_id}/proposal/approve (Sprint 3 Slice 3).

    Like CommitBlockRequest, expected_proposal_run_id is REQUIRED here: an
    authority approving a reviewed proposal must always name it, so an
    approval can never silently commit whatever happens to be current.
    Approval itself delegates to the existing commit machinery
    (JobService.notify) rather than a second commit implementation -
    see JobService.approve_proposal.
    """

    model_config = ConfigDict(extra="forbid")

    expected_proposal_run_id: str = Field(min_length=1, max_length=128)


class RejectProposalRequest(BaseModel):
    """Body for POST /jobs/{job_id}/proposal/reject (Sprint 3 Slice 3).

    Both fields are mandatory: a rejection must name the exact proposal
    it refuses and state why.
    """

    model_config = ConfigDict(extra="forbid")

    expected_proposal_run_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)


class PostponeProposalRequest(BaseModel):
    """Body for POST /jobs/{job_id}/proposal/postpone (Sprint 3 Slice 3).

    selected_date is a calendar date (YYYY-MM-DD), converted to a
    not-before minute through the one authoritative horizon-anchoring
    conversion - backend.app.data.horizon_anchor.horizon_relative_minutes
    - at local midnight of that date (see JobService.postpone_proposal).
    It is never a specific time of day: postponing expresses "not before
    this date", not "not before this instant".
    """

    model_config = ConfigDict(extra="forbid")

    expected_proposal_run_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)
    selected_date: str = Field(min_length=1, max_length=10)

    @field_validator("selected_date")
    @classmethod
    def validate_selected_date(cls, value: str) -> str:
        try:
            _date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"selected_date must be an ISO-8601 date (YYYY-MM-DD), "
                f"got {value!r}"
            ) from exc

        return value


class ReleaseBlockRequest(BaseModel):
    """Body for POST /jobs/{job_id}/proposal/release (Sprint 3 Slice 6).

    Releases an APPROVED (committed) block whose execution cannot begin -
    possession not granted, crew or safety restriction, cancellation
    before START. Both fields are mandatory: the release must name the
    exact commitment it withdraws and say why. There is no status field
    and no new JobStatus: the job returns to 'reported', and reason is
    what distinguishes one release from another.
    """

    model_config = ConfigDict(extra="forbid")

    expected_proposal_run_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)


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


class ProposalExplanationItem(BaseModel):
    """One structured reason behind a BlockProposal (Sprint 3 Slice 2).

    A fixed {code, detail} shape rather than free-form generated text,
    per the Slice 2 explainability requirement: `code` is a stable,
    machine-checkable label a test or a UI can switch on; `detail` is
    the human-readable statement of the same fact.
    """

    code: str
    detail: str


class BlockProposalResponse(BaseModel):
    """A NEW scheduling proposal for ONE maintenance job (Sprint 3 Slice 2).

    Derived, not separately persisted: built from the job's own row,
    the specific BLOCK_PROPOSED/BLOCK_REPROPOSED/COMMITTED_BLOCK_PRESERVED
    history event that produced the current placement, and the
    optimization_runs record for that run. See backend.app.jobs.proposal.

    A proposal is never a commitment: is_committed is always False here
    - committing happens through POST /jobs/{job_id}/notify, a distinct
    action recorded as BLOCK_COMMITTED, never implied by this object's
    existence.
    """

    proposal_id: str
    job_id: str
    optimization_run_id: str
    corridor_id: str

    track_id: str
    section_id: Optional[str] = None

    start_minute: int
    end_minute: int
    duration_minutes: int

    work_type: str
    priority_score: float
    risk_score: float
    objective_score: float

    is_committed: bool

    explanation: list[ProposalExplanationItem]
    provenance: ProvenanceResponse

    generated_at: str


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


# ----------------------------------------------------------------------
# Field execution and evidence (Sprint 3 Slice 5 Step 3)
# ----------------------------------------------------------------------


class EvidenceInput(BaseModel):
    """One piece of caller-supplied execution evidence.

    Shape-level validation only (length, coordinate pairing). The
    deeper observation-time rules - explicit UTC offset, not in the
    future, ordering against actual_start_at/actual_end_at, no before-
    reference reused as after - are enforced once, by
    backend.app.jobs.execution.EvidenceItem, which JobService
    constructs from this model's dict rather than reimplementing the
    same checks here (a doubly-validated model, not a second one).
    Field names mirror EvidenceItem's own so that conversion needs no
    renaming layer.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_reference: str = Field(min_length=1, max_length=200)
    evidence_kind: Literal["PHOTO", "VIDEO", "DOCUMENT", "MEASUREMENT"]
    captured_at: str = Field(min_length=1)
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("longitude")
    @classmethod
    def validate_coordinate_pair(cls, value: Optional[float], info):
        latitude = info.data.get("latitude")

        if (latitude is None) != (value is None):
            raise ValueError(
                "latitude and longitude must be given together or not at all"
            )

        return value


class StartExecutionRequest(BaseModel):
    """Body for POST /jobs/{job_id}/execution/start.

    expected_proposal_run_id pins the start to the block the caller
    reviewed, exactly as CommitBlockRequest/ApproveProposalRequest pin a
    commit - see JobService.start_execution.
    """

    model_config = ConfigDict(extra="forbid")

    expected_proposal_run_id: str = Field(min_length=1, max_length=128)
    actual_start_at: str = Field(min_length=1)
    before_work_evidence: list[EvidenceInput] = Field(min_length=1, max_length=10)


class CompleteExecutionRequest(BaseModel):
    """Body for POST /jobs/{job_id}/execution/complete."""

    model_config = ConfigDict(extra="forbid")

    execution_id: str = Field(min_length=1)
    actual_end_at: str = Field(min_length=1)
    after_work_evidence: list[EvidenceInput] = Field(min_length=1, max_length=10)


class ExecutionNotCompletedRequest(BaseModel):
    """Body for POST /jobs/{job_id}/execution/not-completed."""

    model_config = ConfigDict(extra="forbid")

    execution_id: str = Field(min_length=1)
    actual_end_at: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[EvidenceInput] = Field(default_factory=list, max_length=10)


class RecordedEvidenceResponse(BaseModel):
    evidence_id: str
    phase: str
    evidence_reference: str
    evidence_kind: str
    captured_at: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    note: Optional[str] = None


class ExecutionDeviationsResponse(BaseModel):
    started_before_planned_start: bool
    ended_after_planned_end: Optional[bool] = None


class ExecutionResponse(BaseModel):
    """One execution attempt of one approved block (Sprint 3 Slice 5).

    Derived, not separately persisted - built straight from
    backend.app.jobs.execution.ExecutionRecord.to_dict(), the job's own
    append-only history, exactly as BlockProposalResponse is built from
    BlockProposal.
    """

    execution_id: str
    job_id: str
    attempt_number: int
    optimization_run_id: str
    proposal_id: str

    track_id: str
    section_id: Optional[str] = None

    planned_start_minute: int
    planned_end_minute: int
    committed_block_digest: str

    status: str

    started_by: ActorResponse
    started_recorded_at: str
    actual_start_at: str
    actual_start_minute: int
    before_work_evidence: list[RecordedEvidenceResponse]

    ended_by: Optional[ActorResponse] = None
    ended_recorded_at: Optional[str] = None
    actual_end_at: Optional[str] = None
    actual_end_minute: Optional[int] = None
    after_work_evidence: list[RecordedEvidenceResponse] = Field(default_factory=list)
    not_completed_reason: Optional[str] = None
    failure_evidence: list[RecordedEvidenceResponse] = Field(default_factory=list)

    deviations: ExecutionDeviationsResponse


class ExecutionActionResponse(BaseModel):
    job: JobResponse
    execution: ExecutionResponse
    message: str


class JobExecutionsResponse(BaseModel):
    """GET /jobs/{job_id}/execution - every execution of this job, oldest first."""

    job_id: str
    executions: list[ExecutionResponse]

# ----------------------------------------------------------------------
# Accountability: derived obligations (Sprint 3 Slice 9)
# ----------------------------------------------------------------------


class ObligationResponse(BaseModel):
    """What one job currently owes, at one moment. Derived, never stored.

    Built straight from backend.app.jobs.obligations.JobObligation.
    to_dict(), the job row plus its append-only history plus an SLA
    policy - exactly as BlockProposalResponse is built from BlockProposal
    and ExecutionResponse from ExecutionRecord. No obligation is
    persisted anywhere, so nothing here can disagree with the history it
    came from.

    THE TWO HONESTY FIELDS
      owed_role       AUTHORITY / WORKER / ENGINEER, or null. NEVER a
                      person: no user table, authority directory or
                      contact detail exists, so naming an individual
                      would be fiction. This obligation says a ROLE owes
                      an action; it never asserts that somebody failed.
      policy_assumed  true. The SLA durations behind due_at are ASSUMED
                      DEMO ENGINEERING VALUES, not Indian Railways
                      policy, and not reviewed by any railway authority.
                      A client displaying a deadline must display this.

    is_past_due is the predicate for "is this late?" - state OVERDUE
    alone is not that question, because a policy whose first escalation
    step is the deadline itself reports ESCALATED_L1 directly.
    """

    job_id: str
    obligation_type: str
    state: str
    owed_role: Optional[str] = None

    #: The one moment this whole evaluation was made at. Every obligation
    #: in a page shares it.
    evaluated_at: str

    clock_started_at: Optional[str] = None
    due_at: Optional[str] = None
    escalation_level: int = 0
    elapsed_seconds: Optional[float] = None
    sla_seconds: Optional[float] = None

    policy_version: str
    policy_assumed: bool

    #: The event that started the clock, and the run it belongs to - what
    #: an auditor recomputes this obligation from.
    anchor_event_id: Optional[str] = None
    optimization_run_id: Optional[str] = None

    reason_code: str
    reason: str

    is_open: bool
    is_past_due: bool

    #: Orthogonal to the obligation's own clock: a condition that needs
    #: an engineer to reconcile it (a conflict the optimizer could not
    #: honour, or a recorded integrity refusal).
    attention_required: bool = False
    attention_reason_code: Optional[str] = None
    attention_event_id: Optional[str] = None
    attention_role: Optional[str] = None


class ObligationListResponse(BaseModel):
    """GET /v1/obligations - one page of derived obligations.

    PAGING IS OVER JOBS, NOT OVER OBLIGATIONS. Obligations are derived
    after a page of candidate jobs is read, so a filtered request returns
    the matches WITHIN each page: a page may hold fewer than `limit`
    items - even zero - while next_cursor is still set. Keep following
    next_cursor until it is null. The cursor itself is the same opaque
    (created_at, job_id) keyset cursor GET /v1/jobs uses.

    evaluated_at is the single moment the whole page was evaluated at.
    """

    items: list[ObligationResponse]
    next_cursor: Optional[str] = None
    evaluated_at: str
    policy_version: str
    #: Always true - see ObligationResponse.policy_assumed.
    policy_assumed: bool


class OptimizationRunResponse(BaseModel):
    """GET /v1/optimization-runs/{run_id} - one run's own audit record.

    The immutable counterpart to a job's last_solver_status: that says
    the CURRENT outcome for one job, overwritten by every later run;
    this says what one run actually saw and returned. Append-only at the
    SQL layer, with no update or delete path anywhere in the application.

    request_json / result_json / provenance_snapshot_json are returned as
    the opaque pre-serialized strings they are stored as, rather than
    re-parsed into a shape this contract would then have to freeze.

    `actor` is a bare string here, not an ActorResponse: optimization_runs
    predates the Actor model and stores no role or assurance. It is
    reported as stored rather than upgraded into a richer claim than the
    record actually supports.
    """

    run_id: str
    actor: str
    trigger: str
    corridor_id: Optional[str] = None
    requested_at: str
    completed_at: str
    solver_status: str
    solve_time_seconds: Optional[float] = None
    request_json: str
    result_json: Optional[str] = None
    provenance_snapshot_json: Optional[str] = None
    error: Optional[str] = None
