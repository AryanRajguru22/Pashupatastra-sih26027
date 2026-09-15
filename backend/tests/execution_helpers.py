"""Drive a job through the real Slice 5 execution lifecycle in tests.

There is no direct completion: a notified job is completed only by
starting execution with before-work evidence and completing it with
after-work evidence. Observation times sit inside the fixed optimization
horizon (DEFAULT_HORIZON_START), at the job's own committed placement.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from contracts import DEFAULT_HORIZON_START

from backend.app.identity.actor import ActorRole, human_actor
from backend.app.jobs.lifecycle import proposal_run_id_of


EXECUTION_WORKER = human_actor("WORKER-042", ActorRole.WORKER)

_HORIZON = datetime.fromisoformat(DEFAULT_HORIZON_START)


def observed_at(minute: float) -> str:
    return (_HORIZON + timedelta(minutes=minute)).isoformat()


def evidence(reference: str, minute: float, kind: str = "PHOTO") -> dict:
    return {
        "evidence_reference": reference,
        "evidence_kind": kind,
        "captured_at": observed_at(minute),
    }


def start_execution_body(job: dict) -> dict:
    """Request body (and service kwargs) starting execution of a notified job."""

    start_minute = job["schedule_start_minute"] + 1
    return {
        "expected_proposal_run_id": proposal_run_id_of(job),
        "actual_start_at": observed_at(start_minute),
        "before_work_evidence": [evidence("before/photo-1.jpg", start_minute - 1)],
    }


def complete_execution_body(job: dict, execution_id: str) -> dict:
    """Request body (and service kwargs) completing an execution with evidence."""

    end_minute = job["schedule_end_minute"] + 10
    return {
        "execution_id": execution_id,
        "actual_end_at": observed_at(end_minute),
        "after_work_evidence": [evidence("after/photo-1.jpg", end_minute - 1)],
    }


def start_execution(service, job_id: str, actor=EXECUTION_WORKER):
    job = service.repository.get(job_id)
    return service.start_execution(job_id, actor=actor, **start_execution_body(job))


def execute_to_completion(service, job_id: str, actor=EXECUTION_WORKER) -> dict:
    """notified -> in_progress -> completed. Returns the completed job."""

    job = service.repository.get(job_id)
    _, execution = service.start_execution(
        job_id, actor=actor, **start_execution_body(job)
    )
    completed, _ = service.complete_execution(
        job_id, actor=actor, **complete_execution_body(job, execution.execution_id)
    )
    return completed
