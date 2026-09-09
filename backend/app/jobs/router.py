from __future__ import annotations

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
)

from backend.app.jobs.models import (
    JobActionResponse,
    JobCreateRequest,
    JobOptimizationResponse,
    JobResponse,
    JobStatus,
)

from backend.app.jobs.optimization import (
    JobOptimizationService,
    NoEligibleJobsError,
    PossessionDataUnavailableError,
)

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

# Shares the JobService above so both routers see the same repository
# and corridor. The single-flight lock lives on this instance.
optimization_service = JobOptimizationService(service)


@router.post(
    "/jobs",
    response_model=JobResponse,
    status_code=201,
)
def create_job(
    request: JobCreateRequest,
) -> JobResponse:

    try:
        job = service.create_job(request)

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


@router.post(
    "/corridors/{corridor_id}/optimize-jobs",
    response_model=JobOptimizationResponse,
    tags=["jobs"],
)
def optimize_corridor_jobs(
    corridor_id: str,
) -> JobOptimizationResponse:
    """Run the existing CP-SAT solver over this corridor's active jobs.

    Returns an honest batch outcome: scheduled and unscheduled work are
    reported separately, with the solver's own reasons, and the
    provenance of the possession data used is stated explicitly.
    """

    try:
        outcome = optimization_service.optimize_corridor(
            corridor_id
        )

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

    except TerminalJobError as exc:
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
) -> JobActionResponse:

    try:
        job = service.notify(job_id)

    except TerminalJobError as exc:
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
    "/jobs/{job_id}/complete",
    response_model=JobActionResponse,
)
def complete_job(
    job_id: str,
) -> JobActionResponse:

    try:
        job = service.complete(job_id)

    except TerminalJobError as exc:
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