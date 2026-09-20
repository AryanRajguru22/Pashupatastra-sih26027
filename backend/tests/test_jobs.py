from __future__ import annotations

from pathlib import Path

from backend.app.jobs.models import (
    JobCreateRequest,
)

from backend.app.jobs.repository import (
    JobRepository,
)

from backend.app.jobs.optimization import (
    JobOptimizationService,
)

from backend.app.jobs.service import (
    JobService,
)

from backend.tests.execution_helpers import (
    EXECUTION_WORKER,
    complete_execution_body,
    execute_to_completion,
    start_execution,
)

# Slice 9: approving a proposal requires an identified human actor -
# accountability, not authentication (see backend.app.jobs.lifecycle.
# _require_identified_human). An in-process caller must now name one.
from backend.app.identity.actor import ActorRole as _ActorRole
from backend.app.identity.actor import human_actor as _human_actor

APPROVING_AUTHORITY = _human_actor("AUTHORITY-017", _ActorRole.AUTHORITY)



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
        service.notify(job_id, actor=APPROVING_AUTHORITY)
        assert False
    except ValueError:
        pass

    # reported -> scheduled (an optimization proposal)
    JobOptimizationService(service).optimize_corridor("CORRIDOR_A")

    scheduled = service.repository.get(
        job_id
    )

    assert (
        scheduled["status"]
        == "scheduled"
    )

    # scheduled -> notified
    notified = service.notify(job_id, actor=APPROVING_AUTHORITY)

    assert (
        notified["status"]
        == "notified"
    )

    # notified -> completed is not a transition
    assert not hasattr(service, "complete")

    # notified -> in_progress -> completed, with evidence
    _, execution = start_execution(service, job_id)

    assert (
        service.repository.get(job_id)["status"]
        == "in_progress"
    )

    completed, _ = service.complete_execution(
        job_id,
        actor=EXECUTION_WORKER,
        **complete_execution_body(notified, execution.execution_id),
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

    JobOptimizationService(service).optimize_corridor("CORRIDOR_A")

    service.notify(job_id, actor=APPROVING_AUTHORITY)
    execute_to_completion(service, job_id)

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