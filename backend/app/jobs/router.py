from __future__ import annotations

from typing import Optional

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
)

from backend.app.api.deps import request_actor

from backend.app.identity.actor import Actor

from backend.app.identity.authorization import AuthorizationDenied

from backend.app.jobs.history import StoredJobEvent

from backend.app.jobs.lifecycle import (
    CommittedJobError,
    CommittedStateIntegrityError,
    ConcurrentJobModificationError,
    StaleProposalError,
)

from backend.app.jobs.models import (
    ApproveProposalRequest,
    BlockProposalResponse,
    CommitBlockRequest,
    JobActionResponse,
    JobCreateRequest,
    JobEventResponse,
    JobHistoryResponse,
    JobOptimizationResponse,
    JobResponse,
    JobStatus,
    PostponeProposalRequest,
    ProposalExplanationItem,
    RejectProposalRequest,
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
    as_public_job,
)


router = APIRouter(
    tags=["jobs"]
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
    ConcurrentJobModificationError,
)


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

    except AuthorizationDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    return JobResponse(
        **as_public_job(job)
    )


@router.get(
    "/jobs",
    response_model=list[JobResponse],
)
def list_jobs(
    status: JobStatus | None = Query(
        default=None,
        description=(
            "Optional status filter, e.g. 'reported' for the "
            "authority review queue."
        ),
    ),
) -> list[JobResponse]:

    jobs = (
        service.repository.list_by_status(status.value)
        if status is not None
        else service.list_jobs()
    )

    return [
        JobResponse(
            **as_public_job(job)
        )
        for job in jobs
    ]


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
)
def get_job(
    job_id: str,
) -> JobResponse:

    job = service.repository.get(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' not found",
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

    except AuthorizationDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' not found",
        ) from exc

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

    except AuthorizationDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' not found",
        ) from exc

    except NoBlockProposalError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

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

    except AuthorizationDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except NoEligibleJobsError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except PossessionDataUnavailableError as exc:
        # Fail closed: nothing was solved and nothing was scheduled.
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except _CONFLICTS as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    return JobOptimizationResponse(**outcome)


@router.post(
    "/jobs/{job_id}/notify",
    response_model=JobActionResponse,
)
def notify_job(
    job_id: str,
    body: Optional[CommitBlockRequest] = Body(default=None),
    actor: Actor = Depends(request_actor),
) -> JobActionResponse:

    try:
        job = service.notify(
            job_id,
            actor=actor,
            expected_proposal_run_id=(
                body.expected_proposal_run_id if body is not None else None
            ),
        )

    except AuthorizationDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except _CONFLICTS as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

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

    except AuthorizationDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except _CONFLICTS as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

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

    except AuthorizationDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except _CONFLICTS as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

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

    except AuthorizationDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except _CONFLICTS as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    return JobActionResponse(
        job=JobResponse(
            **as_public_job(job)
        ),
        message="Proposal postponed",
    )


@router.post(
    "/jobs/{job_id}/complete",
    response_model=JobActionResponse,
)
def complete_job(
    job_id: str,
    actor: Actor = Depends(request_actor),
) -> JobActionResponse:

    try:
        job = service.complete(job_id, actor=actor)

    except AuthorizationDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except _CONFLICTS as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    return JobActionResponse(
        job=JobResponse(
            **as_public_job(job)
        ),
        message="Job completed",
    )
