from __future__ import annotations

import base64
import binascii
import json
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    Query,
)

from backend.app.api.deps import request_actor

from backend.app.api.errors import V1_PREFIX, ApiError, ErrorCode

from backend.app.identity.actor import Actor

from backend.app.identity.authorization import AuthorizationDenied

from backend.app.jobs.execution import (
    EvidenceValidationError,
    ExecutionIntegrityError,
    ExecutionRecord,
)

from backend.app.jobs.history import StoredJobEvent

from backend.app.jobs.lifecycle import (
    CommittedJobError,
    CommittedStateIntegrityError,
    ConcurrentJobModificationError,
    InvalidTransitionError,
    StaleExecutionError,
    StaleProposalError,
)

from backend.app.jobs.models import (
    ApproveProposalRequest,
    BlockProposalResponse,
    CommitBlockRequest,
    CompleteExecutionRequest,
    ExecutionActionResponse,
    ExecutionNotCompletedRequest,
    ExecutionResponse,
    JobActionResponse,
    JobCreateRequest,
    JobEventResponse,
    JobExecutionsResponse,
    JobHistoryResponse,
    JobListResponse,
    JobOptimizationResponse,
    JobResponse,
    JobStatus,
    PostponeProposalRequest,
    ProposalExplanationItem,
    RejectProposalRequest,
    ReleaseBlockRequest,
    StartExecutionRequest,
)

from backend.app.jobs.optimization import (
    JobOptimizationService,
    NoEligibleJobsError,
    PossessionDataUnavailableError,
)

from backend.app.jobs.proposal import BlockProposal, NoBlockProposalError

from backend.app.jobs.repository import (
    TerminalJobError,
)

from backend.app.jobs.service import (
    JobService,
    TimetableCoverageGapError,
    as_public_job,
)


# The v1 contract (Slice 7): every jobs-lifecycle route lives under
# /v1. The legacy Milestone-1 demo routes (/optimize, /recover) and
# /health are NOT part of it and stay where they are - see main.py.
router = APIRouter(
    prefix=V1_PREFIX,
    tags=["jobs"],
)

service = JobService()

# Shares the JobService above so both routers see the same repository,
# corridor and lifecycle lock (JobService.lifecycle_lock).
optimization_service = JobOptimizationService(service)


# Refusals that are conflicts with the job's current state rather than
# malformed requests. Each maps to 409. CommittedStateIntegrityError means
# the STORED state is inconsistent: a refusal with the violation in the
# detail, never an unhandled 500 and never a silent repair.
_CONFLICTS = (
    TerminalJobError,
    CommittedJobError,
    CommittedStateIntegrityError,
    StaleProposalError,
    StaleExecutionError,
    ExecutionIntegrityError,
    ConcurrentJobModificationError,
)


# Every domain exception the routes translate, mapped to (status, code).
# ORDER MATTERS: the first isinstance match wins, so a subclass must
# precede its base (ExecutionTokenError/ReleaseTokenError are
# CommittedJobError subclasses; EvidenceValidationError and
# InvalidTransitionError are ValueError subclasses). Statuses are the
# ones the routes returned before Slice 7 - only the code is new.
_TRANSLATIONS: tuple[tuple[type, int, ErrorCode], ...] = (
    (AuthorizationDenied, 403, ErrorCode.AUTHORIZATION_DENIED),
    (NoEligibleJobsError, 409, ErrorCode.NO_ELIGIBLE_JOBS),
    (PossessionDataUnavailableError, 409, ErrorCode.POSSESSION_DATA_UNAVAILABLE),
    (TimetableCoverageGapError, 409, ErrorCode.TIMETABLE_COVERAGE_GAP),
    (NoBlockProposalError, 409, ErrorCode.NO_CURRENT_PROPOSAL),
    (TerminalJobError, 409, ErrorCode.JOB_TERMINAL),
    (CommittedJobError, 409, ErrorCode.JOB_COMMITTED),
    (
        CommittedStateIntegrityError,
        409,
        ErrorCode.COMMITTED_STATE_INCONSISTENT,
    ),
    (StaleProposalError, 409, ErrorCode.STALE_PROPOSAL),
    (StaleExecutionError, 409, ErrorCode.STALE_EXECUTION),
    (
        ExecutionIntegrityError,
        409,
        ErrorCode.EXECUTION_HISTORY_INCONSISTENT,
    ),
    (
        ConcurrentJobModificationError,
        409,
        ErrorCode.CONCURRENT_MODIFICATION,
    ),
    (EvidenceValidationError, 400, ErrorCode.EVIDENCE_INVALID),
    (InvalidTransitionError, 400, ErrorCode.INVALID_TRANSITION),
    (ValueError, 400, ErrorCode.INVALID_REQUEST),
)

_HANDLED = tuple(entry[0] for entry in _TRANSLATIONS) + (KeyError,)


def _api_error(
    exc: Exception,
    *,
    not_found: ErrorCode = ErrorCode.JOB_NOT_FOUND,
) -> ApiError:
    """The ApiError (status + stable code + prose) for a domain exception.

    KeyError is the 404 every route already used; `not_found` names WHAT
    was not found (a job by default, a corridor for optimize-jobs).
    """

    if isinstance(exc, KeyError):
        # str(KeyError("x")) is the quoted repr "'x'"; the message is args[0].
        message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else str(exc)
        return ApiError(404, not_found, message)

    # The lifecycle plans re-raise EvidenceValidationError as an
    # InvalidTransitionError (lifecycle._observations) so an evidence or
    # observation-time problem is a refused transition. The original is
    # kept as __cause__; a client fixing a photo timestamp needs
    # EVIDENCE_INVALID, not a generic "invalid transition".
    if isinstance(exc, InvalidTransitionError) and isinstance(
        exc.__cause__, EvidenceValidationError
    ):
        return ApiError(400, ErrorCode.EVIDENCE_INVALID, str(exc))

    for exc_type, status, code in _TRANSLATIONS:
        if isinstance(exc, exc_type):
            return ApiError(status, code, str(exc))

    raise exc


def _encode_cursor(created_at: str, job_id: str) -> str:
    raw = json.dumps([created_at, job_id], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        created_at, job_id = json.loads(
            base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        )

        if not isinstance(created_at, str) or not isinstance(job_id, str):
            raise ValueError("cursor parts must be strings")

    except (ValueError, TypeError, binascii.Error, UnicodeError) as exc:
        raise ApiError(
            400,
            ErrorCode.INVALID_CURSOR,
            "cursor is not a value returned by this API",
        ) from exc

    return created_at, job_id


@router.post(
    "/jobs",
    response_model=JobResponse,
    status_code=201,
)
def create_job(
    request: JobCreateRequest,
    actor: Actor = Depends(request_actor),
) -> JobResponse:

    try:
        job = service.create_job(request, actor=actor)

    except _HANDLED as exc:
        raise _api_error(exc) from exc

    return JobResponse(
        **as_public_job(job)
    )


MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


@router.get(
    "/jobs",
    response_model=JobListResponse,
)
def list_jobs(
    status: JobStatus | None = Query(
        default=None,
        description=(
            "Optional status filter, e.g. 'reported' for the "
            "authority review queue."
        ),
    ),
    limit: int = Query(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Page size (1-200, default 50).",
    ),
    cursor: Optional[str] = Query(
        default=None,
        description=(
            "Opaque cursor: the next_cursor of the previous page, passed "
            "back verbatim. Omit for the first page."
        ),
    ),
) -> JobListResponse:
    """One page of jobs, newest first: {items, next_cursor}.

    Ordered by (created_at, job_id) descending, so paging never repeats
    or skips a job that existed when paging began. next_cursor is null on
    the last page.
    """

    after = _decode_cursor(cursor) if cursor is not None else None

    # One extra row tells us whether another page exists.
    rows = service.repository.list_page(
        limit=limit + 1,
        status=status.value if status is not None else None,
        after=after,
    )

    page, more = rows[:limit], len(rows) > limit

    return JobListResponse(
        items=[JobResponse(**as_public_job(job)) for job in page],
        next_cursor=(
            _encode_cursor(page[-1]["created_at"], page[-1]["job_id"])
            if more
            else None
        ),
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
)
def get_job(
    job_id: str,
) -> JobResponse:

    job = service.repository.get(job_id)

    if job is None:
        raise ApiError(
            404,
            ErrorCode.JOB_NOT_FOUND,
            f"Job '{job_id}' not found",
        )

    return JobResponse(
        **as_public_job(job)
    )


@router.get(
    "/jobs/{job_id}/history",
    response_model=JobHistoryResponse,
)
def get_job_history(
    job_id: str,
    actor: Actor = Depends(request_actor),
) -> JobHistoryResponse:
    """Chronological, append-only lifecycle history of one job.

    Read-only: there is no route that updates or deletes history, and
    the underlying table rejects UPDATE and DELETE at the SQL layer.
    Events contain actor identifiers, roles and assurance levels - no
    credentials, tokens or secrets are recorded anywhere in history.
    """

    try:
        stored = service.job_history(job_id, actor=actor)

    except _HANDLED as exc:
        raise _api_error(exc) from exc

    return JobHistoryResponse(
        job_id=job_id,
        events=[_event_response(item) for item in stored],
    )


def _event_response(item: StoredJobEvent) -> JobEventResponse:
    return JobEventResponse(
        sequence=item.sequence,
        **item.event.to_dict(),
    )


@router.get(
    "/jobs/{job_id}/proposal",
    response_model=BlockProposalResponse,
)
def get_job_proposal(
    job_id: str,
    actor: Actor = Depends(request_actor),
) -> BlockProposalResponse:
    """The job's current NEW-scheduling proposal (Sprint 3 Slice 2).

    409 when the job exists but has no current proposal right now -
    never yet optimized, considered and left UNSCHEDULED, or already
    committed/completed. A proposal is never a commitment: this route
    never reports is_committed=True (POST /jobs/{job_id}/notify is the
    only route that commits work).
    """

    try:
        proposal = service.current_proposal(job_id, actor=actor)

    except _HANDLED as exc:
        raise _api_error(exc) from exc

    return _proposal_response(proposal)


def _proposal_response(proposal: BlockProposal) -> BlockProposalResponse:
    data = proposal.to_dict()

    return BlockProposalResponse(
        **{
            **data,
            "explanation": [
                ProposalExplanationItem(**item) for item in data["explanation"]
            ],
        }
    )


@router.post(
    "/corridors/{corridor_id}/optimize-jobs",
    response_model=JobOptimizationResponse,
    tags=["jobs"],
)
def optimize_corridor_jobs(
    corridor_id: str,
    actor: Actor = Depends(request_actor),
) -> JobOptimizationResponse:
    """Run the existing CP-SAT solver over this corridor's active jobs.

    Returns an honest batch outcome: scheduled and unscheduled work are
    reported separately, with the solver's own reasons, and the
    provenance of the possession data used is stated explicitly.
    """

    try:
        outcome = optimization_service.optimize_corridor(
            corridor_id,
            actor=actor,
        )

    except _HANDLED as exc:
        # PossessionDataUnavailableError and TimetableCoverageGapError
        # fail closed: nothing was solved and nothing was scheduled (no
        # possession window was derived for any date in the request -
        # see TimetableCoverageGapError's own docstring). KeyError here
        # means the corridor is unknown.
        raise _api_error(exc, not_found=ErrorCode.CORRIDOR_NOT_FOUND) from exc

    return JobOptimizationResponse(**outcome)


@router.post(
    "/jobs/{job_id}/notify",
    response_model=JobActionResponse,
)
def notify_job(
    job_id: str,
    body: CommitBlockRequest,
    actor: Actor = Depends(request_actor),
) -> JobActionResponse:
    """Commit the job's CURRENT proposal, pinned to the run the caller reviewed.

    expected_proposal_run_id is REQUIRED (Slice 7): there is no unpinned
    commit path in the v1 contract. Equivalent to /proposal/approve, which
    is the preferred route for the authority review flow.
    """

    try:
        job = service.notify(
            job_id,
            actor=actor,
            expected_proposal_run_id=body.expected_proposal_run_id,
        )

    except _HANDLED as exc:
        raise _api_error(exc) from exc

    return JobActionResponse(
        job=JobResponse(
            **as_public_job(job)
        ),
        message="Job notified",
    )


@router.post(
    "/jobs/{job_id}/proposal/approve",
    response_model=JobActionResponse,
)
def approve_proposal(
    job_id: str,
    body: ApproveProposalRequest,
    actor: Actor = Depends(request_actor),
) -> JobActionResponse:
    """Approve (commit) the job's CURRENT NEW-block proposal.

    Delegates to the same commit machinery as POST /jobs/{job_id}/notify
    (JobAction.COMMIT_BLOCK; no separate APPROVE_PROPOSAL permission
    exists) - see JobService.approve_proposal. expected_proposal_run_id
    is mandatory here, unlike /notify's optional body.
    """

    try:
        job = service.approve_proposal(
            job_id,
            actor=actor,
            expected_proposal_run_id=body.expected_proposal_run_id,
        )

    except _HANDLED as exc:
        raise _api_error(exc) from exc

    return JobActionResponse(
        job=JobResponse(
            **as_public_job(job)
        ),
        message="Proposal approved",
    )


@router.post(
    "/jobs/{job_id}/proposal/reject",
    response_model=JobActionResponse,
)
def reject_proposal(
    job_id: str,
    body: RejectProposalRequest,
    actor: Actor = Depends(request_actor),
) -> JobActionResponse:
    """Reject the job's CURRENT NEW-block proposal outright.

    scheduled -> reported. The job stays eligible for the next
    optimization attempt to produce a genuinely new proposal - see
    JobService.reject_proposal.
    """

    try:
        job = service.reject_proposal(
            job_id,
            actor=actor,
            expected_proposal_run_id=body.expected_proposal_run_id,
            reason=body.reason,
        )

    except _HANDLED as exc:
        raise _api_error(exc) from exc

    return JobActionResponse(
        job=JobResponse(
            **as_public_job(job)
        ),
        message="Proposal rejected",
    )


@router.post(
    "/jobs/{job_id}/proposal/postpone",
    response_model=JobActionResponse,
)
def postpone_proposal(
    job_id: str,
    body: PostponeProposalRequest,
    actor: Actor = Depends(request_actor),
) -> JobActionResponse:
    """Postpone the job's CURRENT NEW-block proposal to a not-before date.

    scheduled -> reported, with the block's earliest_start_minute raised
    so the next optimization attempt cannot place it earlier than
    selected_date - see JobService.postpone_proposal. A date outside
    this deployment's supported optimization horizon fails closed (400)
    rather than silently accepted.
    """

    try:
        job = service.postpone_proposal(
            job_id,
            actor=actor,
            expected_proposal_run_id=body.expected_proposal_run_id,
            reason=body.reason,
            selected_date=body.selected_date,
        )

    except _HANDLED as exc:
        raise _api_error(exc) from exc

    return JobActionResponse(
        job=JobResponse(
            **as_public_job(job)
        ),
        message="Proposal postponed",
    )


@router.post(
    "/jobs/{job_id}/proposal/release",
    response_model=JobActionResponse,
)
def release_committed_block(
    job_id: str,
    body: ReleaseBlockRequest,
    actor: Actor = Depends(request_actor),
) -> JobActionResponse:
    """Release the job's APPROVED (committed) block: notified -> reported.

    For a block that was already approved but whose execution cannot
    begin - possession not granted, crew or safety restriction, authority
    cancellation before START. Distinct from /proposal/reject and
    /proposal/postpone, which act on an UNCOMMITTED proposal, and from
    /execution/not-completed, which reports work that actually started;
    once the job is 'in_progress' this route refuses it (409/400) and
    not-completed is the correct operation. See
    JobService.release_committed_block.

    The released job returns to 'reported' and becomes eligible for the
    next optimization run; no proposal is fabricated and no optimization
    is triggered here.
    """

    try:
        job = service.release_committed_block(
            job_id,
            actor=actor,
            expected_proposal_run_id=body.expected_proposal_run_id,
            reason=body.reason,
        )

    except _HANDLED as exc:
        raise _api_error(exc) from exc

    return JobActionResponse(
        job=JobResponse(
            **as_public_job(job)
        ),
        message="Committed block released",
    )


# ----------------------------------------------------------------------
# Field execution (Sprint 3 Slice 5 Step 3)
# ----------------------------------------------------------------------


def _execution_response(execution: ExecutionRecord) -> ExecutionResponse:
    return ExecutionResponse(**execution.to_dict())


def _execution_action_response(
    job: dict, execution: ExecutionRecord, message: str
) -> ExecutionActionResponse:
    return ExecutionActionResponse(
        job=JobResponse(**as_public_job(job)),
        execution=_execution_response(execution),
        message=message,
    )


@router.post(
    "/jobs/{job_id}/execution/start",
    response_model=ExecutionActionResponse,
)
def start_execution(
    job_id: str,
    body: StartExecutionRequest,
    actor: Actor = Depends(request_actor),
) -> ExecutionActionResponse:
    """notified -> in_progress: field work on the approved block has started.

    Approval is permission to execute, not completion - see
    JobService.start_execution. Requires 1-10 before-work evidence items
    and the expected_proposal_run_id of the block being executed.
    """

    try:
        job, execution = service.start_execution(
            job_id,
            actor=actor,
            expected_proposal_run_id=body.expected_proposal_run_id,
            actual_start_at=body.actual_start_at,
            before_work_evidence=[
                item.model_dump() for item in body.before_work_evidence
            ],
        )

    except _HANDLED as exc:
        raise _api_error(exc) from exc

    return _execution_action_response(job, execution, "Execution started")


@router.post(
    "/jobs/{job_id}/execution/complete",
    response_model=ExecutionActionResponse,
)
def complete_execution(
    job_id: str,
    body: CompleteExecutionRequest,
    actor: Actor = Depends(request_actor),
) -> ExecutionActionResponse:
    """in_progress -> completed: field work finished, with after-work evidence.

    Terminal - see JobService.complete_execution. Requires 1-10
    after-work evidence items; none may reuse a before-work reference of
    the same execution.
    """

    try:
        job, execution = service.complete_execution(
            job_id,
            actor=actor,
            execution_id=body.execution_id,
            actual_end_at=body.actual_end_at,
            after_work_evidence=[
                item.model_dump() for item in body.after_work_evidence
            ],
        )

    except _HANDLED as exc:
        raise _api_error(exc) from exc

    return _execution_action_response(job, execution, "Execution completed")


@router.post(
    "/jobs/{job_id}/execution/not-completed",
    response_model=ExecutionActionResponse,
)
def report_execution_not_completed(
    job_id: str,
    body: ExecutionNotCompletedRequest,
    actor: Actor = Depends(request_actor),
) -> ExecutionActionResponse:
    """in_progress -> reported: work not completed; released for replanning.

    The committed block is released, never rewritten into a new
    proposal - see JobService.report_execution_not_completed. Evidence is
    optional (0-10 items); reason is mandatory.
    """

    try:
        job, execution = service.report_execution_not_completed(
            job_id,
            actor=actor,
            execution_id=body.execution_id,
            actual_end_at=body.actual_end_at,
            reason=body.reason,
            failure_evidence=[item.model_dump() for item in body.evidence],
        )

    except _HANDLED as exc:
        raise _api_error(exc) from exc

    return _execution_action_response(job, execution, "Execution not completed")


@router.get(
    "/jobs/{job_id}/execution",
    response_model=JobExecutionsResponse,
)
def get_job_execution(
    job_id: str,
    actor: Actor = Depends(request_actor),
) -> JobExecutionsResponse:
    """Every execution of this job, oldest first, derived from history.

    200 with an empty executions list when the job exists but has never
    had a field execution - never a 404 - see JobService.get_execution.
    404 only when the job itself does not exist.
    """

    try:
        executions = service.get_execution(job_id, actor=actor)

    except _HANDLED as exc:
        raise _api_error(exc) from exc

    return JobExecutionsResponse(
        job_id=job_id,
        executions=[_execution_response(item) for item in executions],
    )
