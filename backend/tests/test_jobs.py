from __future__ import annotations

from pathlib import Path

from backend.app.jobs.models import (
    JobCreateRequest,
)

from backend.app.jobs.repository import (
    JobRepository,
)

from backend.app.jobs.service import (
    JobService,
)


def make_request() -> JobCreateRequest:

    return JobCreateRequest(
        track_id="UP-1",
        job_type="OHE_MAINTENANCE",
        distance_start=10000,
        distance_end=11000,
        workers_min=2,
        workers_max=4,
        description="Replace damaged OHE fittings",
    )


def test_create_job_scores_and_persists(
    tmp_path: Path,
):

    service = JobService(
        repository=JobRepository(
            tmp_path / "jobs.db"
        )
    )

    job = service.create_job(
        make_request()
    )

    assert job["status"] == "reported"

    assert 0 <= job["priority_score"] <= 1
    assert 0 <= job["risk_score"] <= 1

    candidate = job["block_candidate"]

    assert candidate["track_id"] == "UP-1"
    assert candidate["work_type"] == "OHE_MAINTENANCE"

    assert candidate["duration_minutes"] > 0


def test_job_lifecycle(
    tmp_path: Path,
):

    service = JobService(
        repository=JobRepository(
            tmp_path / "jobs.db"
        )
    )

    job = service.create_job(
        make_request()
    )

    job_id = job["job_id"]

    # reported -> notified is invalid
    try:
        service.notify(job_id)
        assert False
    except ValueError:
        pass

    # reported -> scheduled
    service.set_schedule(
        job_id,
        100,
        190,
    )

    scheduled = service.repository.get(
        job_id
    )

    assert (
        scheduled["status"]
        == "scheduled"
    )

    # scheduled -> notified
    notified = service.notify(
        job_id
    )

    assert (
        notified["status"]
        == "notified"
    )

    # notified -> completed
    completed = service.complete(
        job_id
    )

    assert (
        completed["status"]
        == "completed"
    )


def test_completed_job_is_excluded(
    tmp_path: Path,
):

    service = JobService(
        repository=JobRepository(
            tmp_path / "jobs.db"
        )
    )

    job = service.create_job(
        make_request()
    )

    job_id = job["job_id"]

    service.set_schedule(
        job_id,
        100,
        190,
    )

    service.notify(job_id)
    service.complete(job_id)

    candidates = (
        service.active_block_candidates()
    )

    assert all(
        candidate.block_id != job_id
        for candidate in candidates
    )


def test_invalid_request_validation():

    try:
        JobCreateRequest(
            track_id="UP-1",
            job_type="OHE_MAINTENANCE",
            distance_start=10000,
            distance_end=5000,
            workers_min=4,
            workers_max=2,
            description="Invalid",
        )

        assert False

    except ValueError:
        pass