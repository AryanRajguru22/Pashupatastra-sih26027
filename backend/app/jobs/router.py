from __future__ import annotations

from fastapi import (
    APIRouter,
    HTTPException,
)

from backend.app.jobs.models import (
    JobActionResponse,
    JobCreateRequest,
    JobResponse,
)

from backend.app.jobs.service import (
    JobService,
    as_public_job,
)


router = APIRouter(
    tags=["jobs"]
)

service = JobService()


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
def list_jobs() -> list[JobResponse]:

    return [
        JobResponse(
            **as_public_job(job)
        )
        for job in service.list_jobs()
    ]


@router.post(
    "/jobs/{job_id}/notify",
    response_model=JobActionResponse,
)
def notify_job(
    job_id: str,
) -> JobActionResponse:

    try:
        job = service.notify(job_id)

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