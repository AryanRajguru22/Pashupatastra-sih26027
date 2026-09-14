"""Slice 1: accountable maintenance job lifecycle.

Pins, against the real service, repository, solver and HTTP API:

  EVENT HISTORY    every real transition records who did what, when, to
                   which job, why, and the state before and after; system
                   decisions are recorded under SYSTEM actors.
  APPEND-ONLY      history cannot be changed through the API or SQL.
  STALE PROPOSALS  an optimization attempt that does not reaffirm an
                   uncommitted proposal withdraws it (Step 16 P0).
  STATE INTEGRITY  committed work stays COMMITTED and pinned; completed
                   work stays terminal (Step 16 defects).
  CONCURRENCY      optimize and commit cannot interleave in-process, and
                   fail closed across processes.
  LINKAGE          job events and proposals reference optimization_runs.
  HASH READINESS   events serialize canonically and losslessly.

Every test uses an isolated temporary database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.jobs.optimization as optimization_module
from backend.app.audit.repository import AuditRepository
from backend.app.data.corridor_dataset import load_corridor_dataset
from backend.app.identity.actor import (
    OPTIMIZER,
    SYSTEM,
    ActorRole,
    human_actor,
)
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.jobs.events import (
    JobEvent,
    JobEventType,
    event_timestamp,
    make_event,
)
from backend.app.jobs.history import JobHistoryRepository
from backend.app.jobs.lifecycle import (
    CommittedJobError,
    CommittedStateIntegrityError,
    ConcurrentJobModificationError,
    InvalidTransitionError,
    StaleProposalError,
    TerminalJobError,
    plan_commit,
)
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import (
    JobOptimizationService,
    PossessionDataUnavailableError,
)
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService


WORKER = human_actor("WORKER-042", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-003", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-017", ActorRole.AUTHORITY)

E = JobEventType


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "jobs.db"


@pytest.fixture()
def service(db_path: Path) -> JobService:
    return JobService(repository=JobRepository(db_path))


@pytest.fixture()
def optimizer(service: JobService) -> JobOptimizationService:
    return JobOptimizationService(service)


def report(
    service: JobService,
    actor=WORKER,
    track_id: str = "UP-1",
    job_type: str = "BALLAST_TAMPING",
    distance_start: float = 1000.0,
) -> dict:
    return service.create_job(
        JobCreateRequest(
            track_id=track_id,
            job_type=job_type,
            distance_start=distance_start,
            distance_end=distance_start + 400,
            workers_min=2,
            workers_max=4,
            description="Rail head crack observed during foot patrol",
        ),
        actor=actor,
    )


def history(service: JobService, job_id: str) -> list[JobEvent]:
    return [item.event for item in service.history.list_for_job(job_id)]


def event_types(service: JobService, job_id: str) -> list[str]:
    return [event.event_type.value for event in history(service, job_id)]


def last_event(service: JobService, job_id: str) -> JobEvent:
    return history(service, job_id)[-1]


def uncover_track(monkeypatch, service: JobService, track_id: str) -> None:
    """Remove every possession window on one track for later runs.

    Other tracks keep their windows, so possession control stays active
    and the solver refuses the uncovered track's blocks for safety -
    a real solver refusal, not a simulated one.
    """

    original = service.possession_inputs

    def without_track(*args, **kwargs):
        inputs = original(*args, **kwargs)
        return replace(
            inputs,
            windows=[w for w in inputs.windows if w.track_id != track_id],
        )

    monkeypatch.setattr(service, "possession_inputs", without_track)


def count_events(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]


def schedule_and_commit(service, optimizer, **kwargs) -> tuple[dict, str]:
    job = report(service, **kwargs)
    run_id = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)[
        "optimization_run_id"
    ]
    service.notify(job["job_id"], actor=AUTHORITY)
    return service.repository.get(job["job_id"]), run_id


# ----------------------------------------------------------------------
# Acceptance scenario (Part S)
# ----------------------------------------------------------------------


def test_acceptance_worker_report_through_system_proposal(service, optimizer):
    job = report(service, actor=WORKER)

    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=SYSTEM)
    run_id = outcome["optimization_run_id"]

    events = history(service, job["job_id"])

    assert [(e.actor.actor_id, e.event_type) for e in events] == [
        ("WORKER-042", E.JOB_CREATED),
        ("SYSTEM:SCORER", E.JOB_SCORED),
        ("SYSTEM", E.OPTIMIZATION_REQUESTED),
        ("SYSTEM:OPTIMIZER", E.OPTIMIZATION_COMPLETED),
        ("SYSTEM:OPTIMIZER", E.BLOCK_PROPOSED),
    ]

    assert [e.optimization_run_id for e in events] == [
        None,
        None,
        run_id,
        run_id,
        run_id,
    ]

    proposed = events[-1]
    assert proposed.before_state.status == "reported"
    assert proposed.after_state.status == "scheduled"
    assert proposed.after_state.proposal_run_id == run_id


# ----------------------------------------------------------------------
# 8-16. Event history
# ----------------------------------------------------------------------


def test_job_creation_records_worker_then_system_scoring(service):
    job = report(service, actor=WORKER)

    created, scored = history(service, job["job_id"])

    assert created.event_type is E.JOB_CREATED
    assert created.actor == WORKER
    assert created.actor.kind.value == "HUMAN"
    assert created.before_state is None
    assert created.after_state.status == "reported"
    assert created.metadata["track_id"] == "UP-1"
    assert created.metadata["section_id"] == job["block_candidate"]["section_id"]
    assert created.metadata["workers_min"] == 2
    assert created.occurred_at == job["created_at"]

    # Scoring happened inside the worker's request, but it is an
    # automated decision and must not be attributed to the worker.
    assert scored.event_type is E.JOB_SCORED
    assert scored.actor.actor_id == "SYSTEM:SCORER"
    assert scored.actor.kind.value == "SYSTEM"
    assert scored.metadata["priority_score"] == job["priority_score"]
    assert scored.metadata["risk_score"] == job["risk_score"]
    assert scored.metadata["scoring_explanation"]


def test_rejected_report_writes_no_job_and_no_history(service, db_path):
    with pytest.raises(ValueError):
        service.create_job(
            JobCreateRequest(
                track_id="NOPE",
                job_type="BALLAST_TAMPING",
                distance_start=1000.0,
                distance_end=1400.0,
                workers_min=2,
                workers_max=4,
                description="unknown track",
            ),
            actor=WORKER,
        )

    assert service.list_jobs() == []
    assert count_events(db_path) == 0


def test_optimization_records_requester_and_system_decisions(service, optimizer):
    job = report(service)

    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    requested, completed, proposed = history(service, job["job_id"])[-3:]

    assert requested.event_type is E.OPTIMIZATION_REQUESTED
    assert requested.actor == ENGINEER
    assert requested.metadata["corridor_id"] == "CORRIDOR_A"

    assert completed.event_type is E.OPTIMIZATION_COMPLETED
    assert completed.actor == OPTIMIZER
    assert completed.metadata["solver_status"] == outcome["solver_status"]
    assert completed.metadata["considered"] == 1

    assert proposed.event_type is E.BLOCK_PROPOSED
    assert proposed.actor == OPTIMIZER
    placement = outcome["scheduled"][0]
    assert proposed.metadata["start_minute"] == placement["start_minute"]
    assert proposed.metadata["end_minute"] == placement["end_minute"]
    assert proposed.metadata["section_id"] == job["block_candidate"]["section_id"]
    assert proposed.metadata["horizon_start"]


def test_refused_reported_job_records_the_solver_reason(
    service, optimizer, monkeypatch
):
    job = report(service)
    uncover_track(monkeypatch, service, "UP-1")

    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    refused = last_event(service, job["job_id"])
    assert refused.event_type is E.OPTIMIZATION_REFUSED
    assert refused.actor == OPTIMIZER
    assert "refused for safety" in refused.reason
    assert refused.before_state == refused.after_state

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "reported"
    assert stored["last_refusal_reason"] == refused.reason


def test_reoptimization_records_a_reproposal_linked_to_the_previous_run(
    service, optimizer
):
    job = report(service)

    first = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    second = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    reproposed = last_event(service, job["job_id"])

    assert reproposed.event_type is E.BLOCK_REPROPOSED
    assert reproposed.optimization_run_id == second["optimization_run_id"]
    assert (
        reproposed.metadata["previous_proposal_run_id"]
        == first["optimization_run_id"]
    )
    assert isinstance(reproposed.metadata["placement_changed"], bool)
    assert reproposed.before_state.proposal_run_id == first["optimization_run_id"]
    assert reproposed.after_state.proposal_run_id == second["optimization_run_id"]


def test_commit_records_the_human_who_committed(service, optimizer):
    job, run_id = schedule_and_commit(service, optimizer)

    committed = last_event(service, job["job_id"])

    assert committed.event_type is E.BLOCK_COMMITTED
    assert committed.actor == AUTHORITY
    assert committed.optimization_run_id == run_id
    assert committed.before_state.status == "scheduled"
    assert committed.before_state.is_committed is False
    assert committed.after_state.status == "notified"
    assert committed.after_state.block_status == "COMMITTED"
    assert committed.after_state.is_committed is True


def test_completion_records_job_completed_with_the_actual_actor(
    service, optimizer
):
    job, _ = schedule_and_commit(service, optimizer)

    service.complete(job["job_id"], actor=WORKER)

    completed = last_event(service, job["job_id"])

    assert completed.event_type is E.JOB_COMPLETED
    assert completed.actor == WORKER
    assert completed.before_state.status == "notified"
    assert completed.after_state.status == "completed"


def test_history_is_chronological_and_complete_for_the_lifecycle(
    service, optimizer
):
    job, _ = schedule_and_commit(service, optimizer)
    service.complete(job["job_id"], actor=WORKER)

    stored = service.history.list_for_job(job["job_id"])

    assert [item.event.event_type for item in stored] == [
        E.JOB_CREATED,
        E.JOB_SCORED,
        E.OPTIMIZATION_REQUESTED,
        E.OPTIMIZATION_COMPLETED,
        E.BLOCK_PROPOSED,
        E.BLOCK_COMMITTED,
        E.JOB_COMPLETED,
    ]

    sequences = [item.sequence for item in stored]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)

    timestamps = [item.event.occurred_at for item in stored]
    assert timestamps == sorted(timestamps)

    # Each transition starts from the state the previous one produced.
    transitions = [item.event for item in stored if item.event.before_state]
    for earlier, later in zip(transitions, transitions[1:]):
        assert later.before_state == earlier.after_state


def test_history_of_one_job_contains_only_that_job(service, optimizer):
    first = report(service)
    second = report(service, track_id="DOWN-1", distance_start=2000.0)

    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    assert {e.job_id for e in history(service, first["job_id"])} == {first["job_id"]}
    assert {e.job_id for e in history(service, second["job_id"])} == {second["job_id"]}


def test_history_of_a_missing_job_is_a_key_error(service):
    with pytest.raises(KeyError):
        service.job_history("JOB-DOES-NOT-EXIST", actor=AUTHORITY)


def test_invalid_transition_attempt_is_recorded_without_changing_state(service):
    job = report(service)
    before = service.repository.get(job["job_id"])

    with pytest.raises(InvalidTransitionError):
        service.notify(job["job_id"], actor=AUTHORITY)

    rejected = last_event(service, job["job_id"])

    assert rejected.event_type is E.TRANSITION_REJECTED
    assert rejected.actor == AUTHORITY
    assert rejected.metadata["attempted_transition"] == "notify"
    assert "cannot be notified from status 'reported'" in rejected.reason
    assert rejected.before_state == rejected.after_state

    after = service.repository.get(job["job_id"])
    assert after["status"] == before["status"]
    assert after["updated_at"] == before["updated_at"]


def test_omitted_actor_is_recorded_as_unidentified_not_invented(service):
    job = service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="ROUTINE_INSPECTION",
            distance_start=5000.0,
            distance_end=5200.0,
            workers_min=1,
            workers_max=2,
            description="in-process caller with no actor",
        )
    )

    created = history(service, job["job_id"])[0]
    assert created.actor.actor_id == "UNIDENTIFIED"
    assert created.actor.assurance.value == "NONE"


# ----------------------------------------------------------------------
# 17-18. Append-only: no API or SQL path changes history
# ----------------------------------------------------------------------


def test_history_table_rejects_update_and_delete_at_the_sql_layer(
    service, db_path
):
    report(service)

    with closing(sqlite3.connect(db_path)) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(
                "UPDATE job_events SET event_type = 'BLOCK_COMMITTED'"
            )

        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM job_events")

    assert count_events(db_path) == 2


def test_duplicate_event_id_is_rejected_not_overwritten(service):
    job = report(service)
    existing = history(service, job["job_id"])[0]

    duplicate = replace(existing, reason="rewritten")

    with pytest.raises(sqlite3.IntegrityError):
        service.repository.append_events([duplicate])

    assert history(service, job["job_id"])[0].reason is None


def test_history_repository_exposes_no_write_methods():
    public = {name for name in dir(JobHistoryRepository) if not name.startswith("_")}

    for forbidden in ("update", "delete", "append", "append_events", "set", "replace", "insert"):
        assert forbidden not in public


def test_history_mutation_is_rolled_back_with_a_failed_transition(
    service, optimizer, db_path, monkeypatch
):
    """Events are written in the same transaction as the state change."""

    job = report(service)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    before_count = count_events(db_path)

    import backend.app.jobs.repository as repository_module

    def exploding_append(conn, events):
        raise RuntimeError("simulated history write failure")

    monkeypatch.setattr(repository_module, "append_events", exploding_append)

    with pytest.raises(RuntimeError, match="simulated history write failure"):
        service.repository.mutate_jobs(
            [job["job_id"]],
            plan_commit(job["job_id"], actor=AUTHORITY, at=event_timestamp()),
        )

    assert service.repository.get(job["job_id"])["status"] == "scheduled"
    assert count_events(db_path) == before_count


# ----------------------------------------------------------------------
# 19-21. Stale proposals
# ----------------------------------------------------------------------


def test_solver_refusal_withdraws_the_stale_uncommitted_proposal(
    service, optimizer, monkeypatch
):
    job = report(service)
    first = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    scheduled = service.repository.get(job["job_id"])
    assert scheduled["status"] == "scheduled"

    uncover_track(monkeypatch, service, "UP-1")
    second = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    stored = service.repository.get(job["job_id"])

    assert stored["status"] == "reported"
    assert stored["schedule_start_minute"] is None
    assert stored["schedule_end_minute"] is None
    assert stored["block_candidate"]["status"] == "PLANNED"
    assert "committed_start_minute" not in stored["block_candidate"]["metadata"]
    assert "proposal_run_id" not in stored["block_candidate"]["metadata"]
    assert "refused for safety" in stored["last_refusal_reason"]

    invalidated = last_event(service, job["job_id"])
    assert invalidated.event_type is E.PROPOSAL_INVALIDATED
    assert invalidated.actor == OPTIMIZER
    assert invalidated.optimization_run_id == second["optimization_run_id"]
    assert "refused for safety" in invalidated.reason
    assert invalidated.before_state.status == "scheduled"
    assert invalidated.after_state.status == "reported"
    assert (
        invalidated.metadata["withdrawn_proposal_run_id"]
        == first["optimization_run_id"]
    )
    assert (
        invalidated.metadata["withdrawn_start_minute"]
        == scheduled["schedule_start_minute"]
    )


def test_solver_exception_withdraws_the_stale_proposal_and_is_linked_to_its_run(
    service, optimizer, db_path, monkeypatch
):
    job = report(service)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    def exploding_solve(request):
        raise RuntimeError("simulated solver crash")

    monkeypatch.setattr(optimization_module, "solve", exploding_solve)

    with pytest.raises(RuntimeError, match="simulated solver crash"):
        optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "reported"
    assert stored["schedule_start_minute"] is None
    assert stored["last_solver_status"] == "ERROR"

    requested, failed, invalidated = history(service, job["job_id"])[-3:]

    assert requested.event_type is E.OPTIMIZATION_REQUESTED
    assert failed.event_type is E.OPTIMIZATION_FAILED
    assert invalidated.event_type is E.PROPOSAL_INVALIDATED
    assert "simulated solver crash" in failed.reason

    run = AuditRepository(db_path).get(failed.optimization_run_id)
    assert run is not None
    assert run.solver_status == "ERROR"
    assert invalidated.optimization_run_id == run.run_id


def test_refusal_to_solve_withdraws_the_stale_proposal_without_a_run(
    service, optimizer, db_path, monkeypatch
):
    job = report(service)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    runs_before = len(AuditRepository(db_path).list_all())

    original = service.possession_inputs
    monkeypatch.setattr(
        service,
        "possession_inputs",
        lambda *a, **k: replace(original(*a, **k), windows=[]),
    )

    with pytest.raises(PossessionDataUnavailableError):
        optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "reported"
    assert stored["schedule_start_minute"] is None
    assert stored["last_solver_status"] == "NOT_RUN"

    failed, invalidated = history(service, job["job_id"])[-2:]
    assert failed.event_type is E.OPTIMIZATION_FAILED
    assert invalidated.event_type is E.PROPOSAL_INVALIDATED
    assert failed.optimization_run_id is None
    assert "PossessionDataUnavailableError" in failed.reason

    # No solver ran, so no optimization run record was invented.
    assert len(AuditRepository(db_path).list_all()) == runs_before


def test_withdrawn_proposal_cannot_be_committed(service, optimizer, monkeypatch):
    job = report(service)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    uncover_track(monkeypatch, service, "UP-1")
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    with pytest.raises(InvalidTransitionError):
        service.notify(job["job_id"], actor=AUTHORITY)

    assert service.repository.get(job["job_id"])["status"] == "reported"
    assert last_event(service, job["job_id"]).event_type is E.TRANSITION_REJECTED


def test_commit_pinned_to_a_superseded_proposal_is_refused(service, optimizer):
    job = report(service)
    first = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    second = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    with pytest.raises(StaleProposalError):
        service.notify(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=first["optimization_run_id"],
        )

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "scheduled"
    assert (
        stored["block_candidate"]["metadata"]["proposal_run_id"]
        == second["optimization_run_id"]
    )
    rejected = last_event(service, job["job_id"])
    assert rejected.event_type is E.TRANSITION_REJECTED
    assert rejected.metadata["error_type"] == "StaleProposalError"

    committed = service.notify(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=second["optimization_run_id"],
    )
    assert committed["status"] == "notified"


def test_committed_work_survives_a_refusing_reoptimization(
    service, optimizer, monkeypatch
):
    job, _ = schedule_and_commit(service, optimizer)

    uncover_track(monkeypatch, service, "UP-1")
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "notified"
    assert stored["schedule_start_minute"] == job["schedule_start_minute"]
    assert stored["schedule_end_minute"] == job["schedule_end_minute"]
    assert stored["block_candidate"]["status"] == "COMMITTED"
    assert stored["block_candidate"]["is_committed"] is True

    conflict = last_event(service, job["job_id"])
    assert conflict.event_type is E.COMMITTED_BLOCK_CONFLICT
    assert conflict.metadata["commitment_preserved"] is True
    assert "refused for safety" in conflict.reason
    assert conflict.before_state.status == conflict.after_state.status == "notified"


def test_committed_work_survives_a_failed_reoptimization(
    service, optimizer, monkeypatch
):
    job, _ = schedule_and_commit(service, optimizer)

    def exploding_solve(request):
        raise RuntimeError("simulated solver crash")

    monkeypatch.setattr(optimization_module, "solve", exploding_solve)

    with pytest.raises(RuntimeError):
        optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "notified"
    assert stored["schedule_start_minute"] == job["schedule_start_minute"]
    assert stored["block_candidate"]["status"] == "COMMITTED"

    failed = last_event(service, job["job_id"])
    assert failed.event_type is E.OPTIMIZATION_FAILED
    assert failed.metadata["commitment_preserved"] is True
    assert E.PROPOSAL_INVALIDATED.value not in event_types(service, job["job_id"])


# ----------------------------------------------------------------------
# 22-24. State integrity
# ----------------------------------------------------------------------


def test_committed_block_does_not_regress_to_scheduled_on_reoptimization(
    service, optimizer
):
    """Step 16 defect: re-optimization rewrote COMMITTED to SCHEDULED."""

    job, _ = schedule_and_commit(service, optimizer)

    report(service, track_id="DOWN-1", distance_start=2000.0)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "notified"
    assert stored["block_candidate"]["status"] == "COMMITTED"
    assert stored["block_candidate"]["is_committed"] is True

    preserved = last_event(service, job["job_id"])
    assert preserved.event_type is E.COMMITTED_BLOCK_PRESERVED
    assert preserved.after_state.block_status == "COMMITTED"

    candidates, committed = service.classify_for_optimization()
    assert job["job_id"] in {block.block_id for block in committed}


def corrupt_row(db_path, job_id: str, *, block=None, columns=None) -> None:
    """Write an inconsistent row directly, bypassing every repository guard."""

    with closing(sqlite3.connect(db_path)) as conn, conn:
        stored = json.loads(
            conn.execute(
                "SELECT block_candidate_json FROM maintenance_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
        )
        stored.update(block or {})
        conn.execute(
            "UPDATE maintenance_jobs SET block_candidate_json = ? WHERE job_id = ?",
            (json.dumps(stored), job_id),
        )
        for column, value in (columns or {}).items():
            conn.execute(
                f"UPDATE maintenance_jobs SET {column} = ? WHERE job_id = ?",
                (value, job_id),
            )


def raw_row(db_path, job_id: str) -> tuple:
    with closing(sqlite3.connect(db_path)) as conn:
        return tuple(
            conn.execute(
                "SELECT * FROM maintenance_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        )


def count_runs(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM optimization_runs").fetchone()[0]


def test_inconsistent_committed_state_is_detected_and_fails_closed_not_repaired(
    service, optimizer, db_path
):
    """A notified job whose stored block reads SCHEDULED.

    The old re-optimization bug produced exactly this row; so could any
    future writer bug. It is never rewritten into valid-looking state:
    optimization refuses the corridor before solving, completion is
    refused, the row stays byte-identical, and each refusal is recorded
    under the human who attempted it with the integrity error named.
    """

    job, _ = schedule_and_commit(service, optimizer)
    job_id = job["job_id"]
    other = report(service, track_id="DOWN-1", distance_start=2000.0)

    corrupt_row(db_path, job_id, block={"status": "SCHEDULED"})

    row_before = raw_row(db_path, job_id)
    other_before = raw_row(db_path, other["job_id"])
    runs_before = count_runs(db_path)
    types_before = event_types(service, job_id)
    other_types_before = event_types(service, other["job_id"])

    with pytest.raises(CommittedStateIntegrityError) as optimize_error:
        optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    assert optimize_error.value.job_ids == (job_id,)
    assert "reconciliation" in str(optimize_error.value)

    with pytest.raises(CommittedStateIntegrityError):
        service.complete(job_id, actor=WORKER)

    # Nothing was solved, written, withdrawn or repaired.
    assert count_runs(db_path) == runs_before
    assert raw_row(db_path, job_id) == row_before
    assert raw_row(db_path, other["job_id"]) == other_before
    assert event_types(service, other["job_id"]) == other_types_before

    # Surfaced, not concealed: two refusals, no legitimizing event.
    added = history(service, job_id)[len(types_before):]
    assert [e.event_type for e in added] == [E.TRANSITION_REJECTED] * 2
    assert [e.metadata["attempted_transition"] for e in added] == [
        "optimize",
        "complete",
    ]
    assert [e.actor for e in added] == [ENGINEER, WORKER]

    for event in added:
        assert event.metadata["error_type"] == "CommittedStateIntegrityError"
        assert event.before_state == event.after_state
        assert event.after_state.status == "notified"
        assert event.after_state.block_status == "SCHEDULED"

    for forbidden in (E.COMMITTED_BLOCK_PRESERVED, E.JOB_COMPLETED):
        assert types_before.count(forbidden.value) == event_types(
            service, job_id
        ).count(forbidden.value)


@pytest.mark.parametrize(
    "corruption",
    [
        {"block": {"status": "SCHEDULED"}},
        {"block": {"status": "PLANNED", "is_committed": False}},
        {"block": {"is_committed": False}},
        {"columns": {"schedule_start_minute": None}},
        {"columns": {"schedule_end_minute": 0}},
    ],
    ids=["block-scheduled", "block-planned", "not-is-committed", "no-start", "bad-window"],
)
def test_every_kind_of_inconsistent_committed_row_blocks_every_write_path(
    service, optimizer, db_path, corruption
):
    job, _ = schedule_and_commit(service, optimizer)
    job_id = job["job_id"]
    corrupt_row(db_path, job_id, **corruption)
    row_before = raw_row(db_path, job_id)

    attempts = [
        lambda: service.complete(job_id, actor=WORKER),
        lambda: optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER),
        lambda: service.repository.update_status(job_id, "completed"),
        lambda: service.repository.apply_optimization_outcome(
            scheduled=[], refused=[{"job_id": job_id, "reason": "x"}],
            updated_at="now", solver_status="INFEASIBLE",
        ),
    ]

    for attempt in attempts:
        with pytest.raises(CommittedStateIntegrityError):
            attempt()

    assert raw_row(db_path, job_id) == row_before


def test_no_write_path_can_produce_an_inconsistent_committed_job(
    service, optimizer, db_path
):
    """Detection is backed by prevention: a buggy writer is refused."""

    job = report(service)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    job_id = job["job_id"]
    row_before = raw_row(db_path, job_id)
    events_before = count_events(db_path)

    # Compatibility primitive: notified without mirroring the block.
    with pytest.raises(CommittedJobError):
        service.repository.update_status(job_id, "notified")

    # Service write path: a plan that commits but forgets the block.
    def buggy_commit(rows):
        from backend.app.jobs.lifecycle import JobMutation

        mutation = replace(JobMutation.unchanged(rows[job_id]), status="notified")
        return [mutation], [
            make_event(job_id, E.BLOCK_COMMITTED, AUTHORITY, occurred_at=event_timestamp())
        ]

    with pytest.raises(CommittedJobError):
        service.repository.mutate_jobs([job_id], buggy_commit)

    assert raw_row(db_path, job_id) == row_before
    assert count_events(db_path) == events_before


def test_committed_state_corrupted_during_solve_rolls_back_the_batch(
    service, optimizer, db_path, monkeypatch
):
    """A writer outside this process corrupts a row after the snapshot."""

    job, _ = schedule_and_commit(service, optimizer)
    other = report(service, track_id="DOWN-1", distance_start=2000.0)
    real_solve = optimization_module.solve

    def solve_then_corrupt(request):
        result = real_solve(request)
        corrupt_row(db_path, job["job_id"], block={"status": "SCHEDULED"})
        return result

    monkeypatch.setattr(optimization_module, "solve", solve_then_corrupt)

    with pytest.raises(CommittedStateIntegrityError):
        optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    assert service.repository.get(other["job_id"])["status"] == "reported"
    assert event_types(service, other["job_id"]) == ["JOB_CREATED", "JOB_SCORED"]

    rejected = last_event(service, job["job_id"])
    assert rejected.event_type is E.TRANSITION_REJECTED
    assert rejected.metadata["error_type"] == "CommittedStateIntegrityError"


def test_committed_placement_cannot_be_reassigned_through_any_write_path(
    service, optimizer
):
    job, _ = schedule_and_commit(service, optimizer)
    job_id = job["job_id"]

    with pytest.raises(CommittedJobError):
        service.set_schedule(job_id, 600, 720, actor=ENGINEER)

    assert last_event(service, job_id).event_type is E.TRANSITION_REJECTED

    with pytest.raises(CommittedJobError):
        service.repository.update_schedule(job_id, 600, 720, updated_at="now")

    with pytest.raises(CommittedJobError):
        service.repository.update_status(job_id, "scheduled")

    with pytest.raises(CommittedJobError):
        service.repository.update_status(
            job_id, "notified", block_status="SCHEDULED"
        )

    with pytest.raises(CommittedJobError):
        service.repository.apply_optimization_outcome(
            scheduled=[{"job_id": job_id, "start_minute": 600, "end_minute": 720}],
            refused=[],
            updated_at="now",
            solver_status="OPTIMAL",
        )

    stored = service.repository.get(job_id)
    assert stored["status"] == "notified"
    assert stored["schedule_start_minute"] == job["schedule_start_minute"]
    assert stored["block_candidate"]["status"] == "COMMITTED"


def test_completed_job_remains_terminal(service, optimizer, db_path):
    job, _ = schedule_and_commit(service, optimizer)
    service.complete(job["job_id"], actor=WORKER)

    for attempt in (
        lambda: service.complete(job["job_id"], actor=WORKER),
        lambda: service.notify(job["job_id"], actor=AUTHORITY),
    ):
        with pytest.raises(InvalidTransitionError):
            attempt()

    with pytest.raises(TerminalJobError):
        service.set_schedule(job["job_id"], 100, 160, actor=ENGINEER)

    with pytest.raises(TerminalJobError):
        service.repository.update_status(job["job_id"], "notified")

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "completed"

    rejected = [
        e for e in history(service, job["job_id"])
        if e.event_type is E.TRANSITION_REJECTED
    ]
    assert len(rejected) == 3
    assert all(e.after_state.status == "completed" for e in rejected)


def test_completed_job_never_reenters_optimization_or_gains_system_events(
    service, optimizer
):
    job, _ = schedule_and_commit(service, optimizer)
    service.complete(job["job_id"], actor=WORKER)
    before = event_types(service, job["job_id"])

    report(service, track_id="DOWN-1", distance_start=2000.0)
    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    touched = {s["job_id"] for s in outcome["scheduled"]}
    touched |= {u["job_id"] for u in outcome["unscheduled"]}
    assert job["job_id"] not in touched
    assert event_types(service, job["job_id"]) == before


# ----------------------------------------------------------------------
# 25-26. Concurrency
# ----------------------------------------------------------------------


def test_commit_waits_for_a_running_optimization_and_cannot_commit_stale_data(
    service, optimizer, monkeypatch
):
    """Optimize and commit share JobService.lifecycle_lock.

    The commit is started while the solver is mid-run. It must not
    complete until the optimization has written its new proposal, and a
    commit pinned to the superseded proposal must then be refused.
    """

    job = report(service)
    first = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    solving = threading.Event()
    release = threading.Event()
    real_solve = optimization_module.solve

    def gated_solve(request):
        solving.set()
        assert release.wait(10), "test gate never released"
        return real_solve(request)

    monkeypatch.setattr(optimization_module, "solve", gated_solve)

    outcomes: list[dict] = []
    errors: list[BaseException] = []
    commit_finished = threading.Event()

    def run_optimization():
        outcomes.append(optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER))

    def run_commit():
        try:
            service.notify(
                job["job_id"],
                actor=AUTHORITY,
                expected_proposal_run_id=first["optimization_run_id"],
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            commit_finished.set()

    optimizing = threading.Thread(target=run_optimization)
    optimizing.start()
    assert solving.wait(10)

    committing = threading.Thread(target=run_commit)
    committing.start()

    assert not commit_finished.wait(0.5), (
        "the commit ran while optimization held the lifecycle lock"
    )

    release.set()
    optimizing.join(10)
    committing.join(10)

    assert len(outcomes) == 1
    assert len(errors) == 1 and isinstance(errors[0], StaleProposalError)

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "scheduled"
    assert (
        stored["block_candidate"]["metadata"]["proposal_run_id"]
        == outcomes[0]["optimization_run_id"]
    )

    tail = event_types(service, job["job_id"])[-2:]
    assert tail == [E.BLOCK_REPROPOSED.value, E.TRANSITION_REJECTED.value]


def test_optimization_refuses_to_apply_over_a_commit_it_did_not_see(
    db_path, monkeypatch
):
    """Cross-process guard: two services on one database share no lock.

    A commit lands (through a second JobService, standing in for a
    second process) while the first service's solver is running. The
    first service must refuse its whole outcome rather than overwrite a
    committed job's placement with a plan computed before the commit.
    """

    first_service = JobService(repository=JobRepository(db_path))
    other_process = JobService(repository=JobRepository(db_path))
    optimizer = JobOptimizationService(first_service)

    job = report(first_service)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    scheduled = first_service.repository.get(job["job_id"])

    real_solve = optimization_module.solve

    def solve_while_another_process_commits(request):
        other_process.notify(job["job_id"], actor=AUTHORITY)
        return real_solve(request)

    monkeypatch.setattr(
        optimization_module, "solve", solve_while_another_process_commits
    )

    with pytest.raises(ConcurrentJobModificationError):
        optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    stored = first_service.repository.get(job["job_id"])
    assert stored["status"] == "notified"
    assert stored["schedule_start_minute"] == scheduled["schedule_start_minute"]
    assert stored["block_candidate"]["status"] == "COMMITTED"

    assert event_types(first_service, job["job_id"])[-1] == E.BLOCK_COMMITTED.value


def test_concurrent_commits_of_one_proposal_succeed_exactly_once(
    service, optimizer
):
    job = report(service)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    successes: list[dict] = []
    failures: list[BaseException] = []
    start = threading.Barrier(6)

    def commit(n: int):
        start.wait()
        try:
            successes.append(
                service.notify(
                    job["job_id"],
                    actor=human_actor(f"AUTHORITY-{n:03d}", ActorRole.AUTHORITY),
                )
            )
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)

    threads = [threading.Thread(target=commit, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert len(successes) == 1
    assert len(failures) == 5
    assert all(isinstance(exc, InvalidTransitionError) for exc in failures)

    types = event_types(service, job["job_id"])
    assert types.count(E.BLOCK_COMMITTED.value) == 1
    assert types.count(E.TRANSITION_REJECTED.value) == 5


def test_concurrent_optimizations_and_completion_preserve_invariants(
    service, optimizer
):
    committed, _ = schedule_and_commit(service, optimizer)
    for i in range(3):
        report(service, distance_start=4000.0 + 500 * i)

    errors: list[BaseException] = []

    def optimize():
        try:
            optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def complete():
        try:
            service.complete(committed["job_id"], actor=WORKER)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=optimize) for _ in range(3)]
    threads.insert(1, threading.Thread(target=complete))

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert not errors, errors

    for job in service.repository.list_all():
        if job["status"] == "scheduled":
            assert job["schedule_start_minute"] is not None
            assert job["block_candidate"]["status"] == "SCHEDULED"
        elif job["status"] == "reported":
            assert job["schedule_start_minute"] is None
        elif job["status"] == "completed":
            assert job["job_id"] == committed["job_id"]
        else:
            pytest.fail(f"unexpected status {job['status']!r}")

    assert service.repository.get(committed["job_id"])["status"] == "completed"


# ----------------------------------------------------------------------
# 27. Job <-> optimization run linkage
# ----------------------------------------------------------------------


def test_job_links_to_the_optimization_run_that_proposed_it(
    service, optimizer, db_path
):
    job = report(service)
    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    run_id = outcome["optimization_run_id"]

    stored = service.repository.get(job["job_id"])
    assert stored["block_candidate"]["metadata"]["proposal_run_id"] == run_id

    run = AuditRepository(db_path).get(run_id)
    assert run is not None
    assert run.actor == ENGINEER.actor_id

    request = json.loads(run.request_json)
    assert job["job_id"] in {c["block_id"] for c in request["candidates"]}

    result = json.loads(run.result_json)
    assert job["job_id"] in {b["block_id"] for b in result["scheduled_blocks"]}

    linked = service.history.list_for_run(run_id)
    assert {item.event.job_id for item in linked} == {job["job_id"]}
    assert {item.event.event_type for item in linked} == {
        E.OPTIMIZATION_REQUESTED,
        E.OPTIMIZATION_COMPLETED,
        E.BLOCK_PROPOSED,
    }


def test_directly_assigned_schedule_claims_no_optimization_run(service):
    job = report(service)

    stored = service.set_schedule(job["job_id"], 100, 190, actor=ENGINEER)

    assert stored["status"] == "scheduled"
    assert "proposal_run_id" not in stored["block_candidate"]["metadata"]

    assigned = last_event(service, job["job_id"])
    assert assigned.event_type is E.SCHEDULE_ASSIGNED
    assert assigned.actor == ENGINEER
    assert assigned.optimization_run_id is None
    assert assigned.metadata["source"] == "direct_assignment"


# ----------------------------------------------------------------------
# Authorization seam
# ----------------------------------------------------------------------


class DenyPolicy:
    enforcing = True

    def __init__(self, *denied: JobAction):
        self.denied = set(denied)

    def authorize(self, actor, action):
        if action in self.denied:
            raise AuthorizationDenied(actor, action, "denied by test policy")


def test_denied_report_creates_nothing(db_path):
    service = JobService(
        repository=JobRepository(db_path),
        authorization=DenyPolicy(JobAction.REPORT_JOB),
    )

    with pytest.raises(AuthorizationDenied):
        report(service)

    assert service.list_jobs() == []
    assert count_events(db_path) == 0


def test_denied_commit_changes_no_state_and_records_nothing(db_path):
    service = JobService(
        repository=JobRepository(db_path),
        authorization=DenyPolicy(JobAction.COMMIT_BLOCK),
    )
    optimizer = JobOptimizationService(service)

    job = report(service)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    before = count_events(db_path)

    with pytest.raises(AuthorizationDenied):
        service.notify(job["job_id"], actor=WORKER)

    assert service.repository.get(job["job_id"])["status"] == "scheduled"
    assert count_events(db_path) == before


def test_denied_optimization_request_runs_nothing(db_path):
    service = JobService(
        repository=JobRepository(db_path),
        authorization=DenyPolicy(JobAction.REQUEST_OPTIMIZATION),
    )
    job = report(service)
    before = count_events(db_path)

    with pytest.raises(AuthorizationDenied):
        JobOptimizationService(service).optimize_corridor(
            "CORRIDOR_A", actor=WORKER
        )

    assert service.repository.get(job["job_id"])["status"] == "reported"
    assert count_events(db_path) == before
    assert AuditRepository(db_path).list_all() == []


# ----------------------------------------------------------------------
# Hash-chain readiness (no chain is implemented)
# ----------------------------------------------------------------------


def test_stored_events_round_trip_to_identical_canonical_bytes(service, optimizer):
    """Storage must be lossless, or a future chain could not be verified."""

    job, _ = schedule_and_commit(service, optimizer)

    for event in history(service, job["job_id"]):
        rebuilt = JobEvent.from_dict(json.loads(event.canonical_bytes()))
        assert rebuilt.canonical_bytes() == event.canonical_bytes()


def test_canonical_bytes_are_deterministic_and_order_independent():
    event = make_event(
        "JOB-1",
        E.BLOCK_COMMITTED,
        AUTHORITY,
        occurred_at="2026-09-13T14:08:42.000000+00:00",
        reason="Operational requirement",
        metadata={"b": 2, "a": 1},
    )
    same = replace(event, metadata={"a": 1, "b": 2})

    assert event.canonical_bytes() == same.canonical_bytes()
    assert b" " not in event.canonical_bytes().replace(b"Operational requirement", b"")


def test_any_change_to_a_recorded_decision_changes_its_digest():
    original = make_event(
        "JOB-104",
        E.TRANSITION_REJECTED,
        AUTHORITY,
        occurred_at="2026-09-13T14:08:42.000000+00:00",
        reason="Operational requirement",
    )

    def digest(event: JobEvent) -> str:
        return hashlib.sha256(event.canonical_bytes()).hexdigest()

    tampered = [
        replace(original, event_type=E.BLOCK_COMMITTED),
        replace(original, reason="Approved"),
        replace(original, actor=human_actor("AUTHORITY-018", ActorRole.AUTHORITY)),
        replace(original, occurred_at="2026-09-13T14:08:43.000000+00:00"),
    ]

    assert len({digest(original), *map(digest, tampered)}) == 5


def test_event_rejects_non_canonical_timestamps_and_metadata():
    with pytest.raises(ValueError):
        make_event("JOB-1", E.JOB_CREATED, WORKER, occurred_at="2026-09-13T14:08:42+00:00")

    with pytest.raises(ValueError):
        make_event("JOB-1", E.JOB_CREATED, WORKER, metadata={"x": float("nan")})

    with pytest.raises(ValueError):
        make_event("JOB-1", E.JOB_CREATED, WORKER, metadata={"x": object()})


def test_stored_actor_kind_contradicting_role_is_rejected(service):
    job = report(service)
    record = history(service, job["job_id"])[0].to_dict()
    record["actor"]["kind"] = "SYSTEM"

    with pytest.raises(ValueError, match="contradicts role"):
        JobEvent.from_dict(record)


# ----------------------------------------------------------------------
# 28-30. Regression on the canonical synthetic dataset
# ----------------------------------------------------------------------


def test_canonical_timetable_corridor_records_section_aware_history(db_path):
    service = JobService(
        repository=JobRepository(db_path),
        dataset=load_corridor_dataset(),
    )
    optimizer = JobOptimizationService(service)

    job = service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="ROUTINE_INSPECTION",
            distance_start=1_000.0,
            distance_end=1_400.0,
            workers_min=1,
            workers_max=2,
            description="NDLS-NZM inspection",
        ),
        actor=WORKER,
    )

    outcome = optimizer.optimize_corridor("CORR-NDLS-AGC", actor=ENGINEER)

    assert outcome["possession_derivation"] == "CANONICAL_TIMETABLE_DERIVED"
    assert outcome["provenance"]["effective"] == "SYNTHETIC"

    proposed = last_event(service, job["job_id"])
    assert proposed.event_type is E.BLOCK_PROPOSED
    assert proposed.metadata["section_id"] == "NDLS-NZM"

    service.notify(job["job_id"], actor=AUTHORITY)
    pinned = service.repository.get(job["job_id"])

    optimizer.optimize_corridor("CORR-NDLS-AGC", actor=ENGINEER)

    after = service.repository.get(job["job_id"])
    assert after["status"] == "notified"
    assert after["block_candidate"]["status"] == "COMMITTED"
    assert after["schedule_start_minute"] == pinned["schedule_start_minute"]
    assert last_event(service, job["job_id"]).event_type is E.COMMITTED_BLOCK_PRESERVED


def test_section_resolution_failure_still_writes_no_history(db_path):
    service = JobService(
        repository=JobRepository(db_path),
        dataset=load_corridor_dataset(),
    )

    with pytest.raises(ValueError):
        service.create_job(
            JobCreateRequest(
                track_id="UP-1",
                job_type="ROUTINE_INSPECTION",
                distance_start=999_000.0,
                distance_end=999_400.0,
                workers_min=1,
                workers_max=2,
                description="outside the corridor",
            ),
            actor=WORKER,
        )

    assert count_events(db_path) == 0


# ----------------------------------------------------------------------
# HTTP: job history API
# ----------------------------------------------------------------------


@pytest.fixture()
def client():
    from backend.app.api.main import app
    from backend.app.jobs.router import service as router_service

    def wipe_jobs():
        # Only maintenance_jobs is cleared between tests. job_events is
        # append-only and cannot be deleted; job ids are unique, so
        # history from earlier tests never appears under a new job.
        with closing(sqlite3.connect(router_service.repository.db_path)) as conn, conn:
            conn.execute("DELETE FROM maintenance_jobs")

    wipe_jobs()
    yield TestClient(app)
    wipe_jobs()


WORKER_HEADERS = {"X-Actor-Id": "WORKER-042", "X-Actor-Role": "WORKER"}
ENGINEER_HEADERS = {"X-Actor-Id": "ENGINEER-003", "X-Actor-Role": "ENGINEER"}
AUTHORITY_HEADERS = {"X-Actor-Id": "AUTHORITY-017", "X-Actor-Role": "AUTHORITY"}


def _create(client) -> dict:
    response = client.post(
        "/jobs",
        json={
            "track_id": "UP-1",
            "job_type": "BALLAST_TAMPING",
            "distance_start": 1000.0,
            "distance_end": 1400.0,
            "workers_min": 2,
            "workers_max": 4,
            "description": "Field-reported defect",
        },
        headers=WORKER_HEADERS,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_history_endpoint_shows_who_did_what_and_the_resulting_state(client):
    job = _create(client)

    optimized = client.post(
        "/corridors/CORRIDOR_A/optimize-jobs", headers=ENGINEER_HEADERS
    )
    assert optimized.status_code == 200, optimized.text
    run_id = optimized.json()["optimization_run_id"]

    committed = client.post(
        f"/jobs/{job['job_id']}/notify",
        json={"expected_proposal_run_id": run_id},
        headers=AUTHORITY_HEADERS,
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["job"]["proposal_run_id"] == run_id

    completed = client.post(
        f"/jobs/{job['job_id']}/complete", headers=WORKER_HEADERS
    )
    assert completed.status_code == 200

    response = client.get(f"/jobs/{job['job_id']}/history")
    assert response.status_code == 200
    body = response.json()

    assert body["job_id"] == job["job_id"]

    summary = [
        (e["actor"]["actor_id"], e["actor"]["kind"], e["event_type"], e["after_state"]["status"])
        for e in body["events"]
    ]

    assert summary == [
        ("WORKER-042", "HUMAN", "JOB_CREATED", "reported"),
        ("SYSTEM:SCORER", "SYSTEM", "JOB_SCORED", "reported"),
        ("ENGINEER-003", "HUMAN", "OPTIMIZATION_REQUESTED", "reported"),
        ("SYSTEM:OPTIMIZER", "SYSTEM", "OPTIMIZATION_COMPLETED", "reported"),
        ("SYSTEM:OPTIMIZER", "SYSTEM", "BLOCK_PROPOSED", "scheduled"),
        ("AUTHORITY-017", "HUMAN", "BLOCK_COMMITTED", "notified"),
        ("WORKER-042", "HUMAN", "JOB_COMPLETED", "completed"),
    ]

    first = body["events"][0]
    assert first["before_state"] is None
    assert first["entity_type"] == "maintenance_job"
    assert first["actor"]["assurance"] == "DECLARED_UNVERIFIED"

    sequences = [e["sequence"] for e in body["events"]]
    assert sequences == sorted(sequences)

    serialized = json.dumps(body).lower()
    for forbidden in ("password", "token", "secret", "credential"):
        assert forbidden not in serialized


def test_history_endpoint_for_missing_job_is_404(client):
    assert client.get("/jobs/JOB-DOES-NOT-EXIST/history").status_code == 404


@pytest.mark.parametrize("method", ["put", "patch", "delete", "post"])
def test_history_endpoint_offers_no_modification(client, method):
    job = _create(client)

    response = getattr(client, method)(f"/jobs/{job['job_id']}/history")

    assert response.status_code == 405
    assert len(client.get(f"/jobs/{job['job_id']}/history").json()["events"]) == 2


def _http_operations() -> set[tuple[str, str]]:
    """Every (method, path) the app serves, including included routers.

    Newer FastAPI wraps app.include_router() in an object with no
    path/methods of its own, so iterating app.routes alone silently finds
    nothing; recurse into the original router instead.
    """

    from backend.app.api.main import app

    operations = set()

    def walk(routes):
        for route in routes:
            nested = getattr(route, "original_router", None)

            if nested is not None:
                walk(nested.routes)
                continue

            for method in getattr(route, "methods", None) or set():
                operations.add((method, route.path))

    walk(app.routes)

    # Cross-check against the served OpenAPI document so the walk above
    # cannot quietly miss routes again.
    documented = {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item
    }
    assert documented <= operations, documented - operations

    return operations


def test_no_route_can_modify_or_delete_history():
    operations = _http_operations()
    assert ("GET", "/jobs/{job_id}/history") in operations

    for method, path in operations:
        if "history" in path:
            assert method in {"GET", "HEAD"}, (method, path)

        assert method != "DELETE", (method, path)


def test_stale_commit_over_http_is_409_and_recorded(client):
    job = _create(client)

    first = client.post(
        "/corridors/CORRIDOR_A/optimize-jobs", headers=ENGINEER_HEADERS
    ).json()["optimization_run_id"]
    client.post("/corridors/CORRIDOR_A/optimize-jobs", headers=ENGINEER_HEADERS)

    response = client.post(
        f"/jobs/{job['job_id']}/notify",
        json={"expected_proposal_run_id": first},
        headers=AUTHORITY_HEADERS,
    )

    assert response.status_code == 409

    last = client.get(f"/jobs/{job['job_id']}/history").json()["events"][-1]
    assert last["event_type"] == "TRANSITION_REJECTED"
    assert last["actor"]["actor_id"] == "AUTHORITY-017"
    assert client.get(f"/jobs/{job['job_id']}").json()["status"] == "scheduled"


def test_commit_body_rejects_unknown_fields(client):
    job = _create(client)

    response = client.post(
        f"/jobs/{job['job_id']}/notify",
        json={"approve": True},
        headers=AUTHORITY_HEADERS,
    )

    assert response.status_code == 422


def test_inconsistent_committed_state_over_http_is_409_and_recorded(client):
    from backend.app.jobs.router import service as router_service

    job = _create(client)
    run_id = client.post(
        "/corridors/CORRIDOR_A/optimize-jobs", headers=ENGINEER_HEADERS
    ).json()["optimization_run_id"]
    client.post(
        f"/jobs/{job['job_id']}/notify",
        json={"expected_proposal_run_id": run_id},
        headers=AUTHORITY_HEADERS,
    )

    corrupt_row(
        router_service.repository.db_path,
        job["job_id"],
        block={"status": "SCHEDULED"},
    )

    for path, headers in (
        ("/corridors/CORRIDOR_A/optimize-jobs", ENGINEER_HEADERS),
        (f"/jobs/{job['job_id']}/complete", WORKER_HEADERS),
    ):
        response = client.post(path, headers=headers)
        assert response.status_code == 409, response.text
        assert "inconsistent" in response.json()["detail"]

    stored = client.get(f"/jobs/{job['job_id']}").json()
    assert stored["status"] == "notified"
    assert stored["block_candidate"]["status"] == "SCHEDULED"

    last_two = client.get(f"/jobs/{job['job_id']}/history").json()["events"][-2:]
    assert [e["metadata"]["error_type"] for e in last_two] == [
        "CommittedStateIntegrityError"
    ] * 2


# ----------------------------------------------------------------------
# No production mutation path bypasses job history
# ----------------------------------------------------------------------


APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# Pre-Slice-1 repository writes that record no lifecycle history.
HISTORY_LESS_WRITES = frozenset(
    {"update_status", "update_schedule", "record_refusal", "apply_optimization_outcome"}
)


def _history_bypasses(source: str) -> list[str]:
    import ast

    found = []

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue

        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)

        if name in HISTORY_LESS_WRITES:
            found.append(f"calls {name}() at line {node.lineno}")

        for keyword in node.keywords:
            if keyword.arg == "record_history" and not (
                isinstance(keyword.value, ast.Constant) and keyword.value.value is True
            ):
                found.append(f"passes record_history= at line {node.lineno}")

    return found


def test_production_code_never_calls_history_less_repository_writes():
    """The compatibility primitives stay test-only.

    Scans every module under backend/app. repository.py is the one
    exemption: it defines the primitives, and apply_optimization_outcome
    is the only record_history=False caller by design.

    Boundary: the scan catches direct calls (repo.update_status(...)),
    which is what accidental use looks like. Deliberate indirection
    (getattr(repo, "update_status"), a stored bound method) is not
    detected; that would be a code-review matter, not an accident.
    """

    offenders = {}

    for path in APP_ROOT.rglob("*.py"):
        if path == APP_ROOT / "jobs" / "repository.py":
            continue

        found = _history_bypasses(path.read_text(encoding="utf-8"))

        if found:
            offenders[str(path.relative_to(APP_ROOT))] = found

    assert offenders == {}


def test_history_bypass_scanner_detects_what_it_guards_against():
    """Prove the guard above can fail, so a passing scan means something."""

    repository_source = (APP_ROOT / "jobs" / "repository.py").read_text(encoding="utf-8")
    assert any("record_history" in f for f in _history_bypasses(repository_source))

    assert _history_bypasses("repo.update_status('J', 'notified')")
    assert _history_bypasses("self.repository.apply_optimization_outcome([], [], 'x', 'y')")
    assert _history_bypasses("plan_optimization_outcome(ids, record_history=flag)")
    assert not _history_bypasses("repo.mutate_jobs(ids, plan)")


def test_every_state_changing_route_is_a_reviewed_history_recording_path():
    """New mutating routes must be added here deliberately, after review.

    Job routes reach state only through JobService / JobOptimizationService
    methods that write events in the same transaction. /optimize and
    /recover never touch maintenance jobs.
    """

    mutating = {
        (method, path)
        for method, path in _http_operations()
        if method not in {"GET", "HEAD", "OPTIONS"}
    }

    assert mutating == {
        ("POST", "/jobs"),
        ("POST", "/corridors/{corridor_id}/optimize-jobs"),
        ("POST", "/jobs/{job_id}/notify"),
        ("POST", "/jobs/{job_id}/proposal/approve"),
        ("POST", "/jobs/{job_id}/proposal/reject"),
        ("POST", "/jobs/{job_id}/proposal/postpone"),
        ("POST", "/jobs/{job_id}/complete"),
        ("POST", "/optimize"),
        ("POST", "/recover"),
    }

    for module in ("optimize", "recover"):
        source = (APP_ROOT / "api" / "routers" / f"{module}.py").read_text(encoding="utf-8")
        assert "backend.app.jobs" not in source


def test_every_job_mutation_over_http_records_its_event(client):
    """Each state-changing job request adds the event describing it."""

    job = _create(client)
    job_id = job["job_id"]

    def events():
        return client.get(f"/jobs/{job_id}/history").json()["events"]

    assert [e["event_type"] for e in events()] == ["JOB_CREATED", "JOB_SCORED"]

    steps = (
        ("/corridors/CORRIDOR_A/optimize-jobs", ENGINEER_HEADERS, "BLOCK_PROPOSED", "scheduled"),
        (f"/jobs/{job_id}/notify", AUTHORITY_HEADERS, "BLOCK_COMMITTED", "notified"),
        (f"/jobs/{job_id}/complete", WORKER_HEADERS, "JOB_COMPLETED", "completed"),
    )

    for path, headers, event_type, status in steps:
        before = len(events())
        assert client.post(path, headers=headers).status_code == 200
        added = events()[before:]

        assert added, path
        assert added[-1]["event_type"] == event_type
        assert added[-1]["after_state"]["status"] == status
        assert client.get(f"/jobs/{job_id}").json()["status"] == status


# ----------------------------------------------------------------------
# SYSTEM cannot be impersonated; declared identity stays unverified
# ----------------------------------------------------------------------


_REPORT_BODY = {
    "track_id": "UP-1",
    "job_type": "BALLAST_TAMPING",
    "distance_start": 1000.0,
    "distance_end": 1400.0,
    "workers_min": 2,
    "workers_max": 4,
    "description": "Impersonation check",
}


_PROPOSAL_REVIEW_BODY = {
    "expected_proposal_run_id": "RUN-DOES-NOT-EXIST",
    "reason": "Impersonation check",
    "selected_date": "2026-09-10",
}


def _proposal_review_body(path: str) -> dict | None:
    """A syntactically valid body for whichever route `path` is.

    Every proposal-review route requires a non-empty body; sending none
    (or an empty {}) would fail with 422 before the actor check ever
    runs, which would test body validation instead of the actor
    rejection these tests exist to prove. The run id is bogus - these
    requests must never reach a point where that matters.
    """

    if path.endswith("/proposal/approve"):
        return {"expected_proposal_run_id": _PROPOSAL_REVIEW_BODY["expected_proposal_run_id"]}

    if path.endswith("/proposal/reject") or path.endswith("/proposal/postpone"):
        return dict(_PROPOSAL_REVIEW_BODY)

    return None


@pytest.mark.parametrize("role", ["SYSTEM", "system", " System "])
def test_no_state_changing_route_accepts_a_system_role(client, role):
    from backend.app.jobs.router import service as router_service

    db = router_service.repository.db_path
    job = _create(client)
    job_id = job["job_id"]
    events_before = count_events(db)
    row_before = raw_row(db, job_id)
    headers = {"X-Actor-Id": "SYSTEM:OPTIMIZER", "X-Actor-Role": role}

    for path in (
        "/jobs",
        "/corridors/CORRIDOR_A/optimize-jobs",
        f"/jobs/{job_id}/notify",
        f"/jobs/{job_id}/proposal/approve",
        f"/jobs/{job_id}/proposal/reject",
        f"/jobs/{job_id}/proposal/postpone",
        f"/jobs/{job_id}/complete",
    ):
        body = _REPORT_BODY if path == "/jobs" else _proposal_review_body(path)
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 403, (path, response.text)

    assert count_events(db) == events_before
    assert raw_row(db, job_id) == row_before
    assert len(client.get("/jobs").json()) == 1


def test_system_looking_id_with_a_human_role_is_rejected_on_every_route(client):
    job = _create(client)
    headers = {"X-Actor-Id": "system:optimizer", "X-Actor-Role": "AUTHORITY"}

    for path in (
        "/corridors/CORRIDOR_A/optimize-jobs",
        f"/jobs/{job['job_id']}/notify",
        f"/jobs/{job['job_id']}/proposal/approve",
        f"/jobs/{job['job_id']}/proposal/reject",
        f"/jobs/{job['job_id']}/proposal/postpone",
        f"/jobs/{job['job_id']}/complete",
    ):
        body = _proposal_review_body(path)
        assert client.post(path, json=body, headers=headers).status_code == 400, path

    assert client.get(f"/jobs/{job['job_id']}").json()["status"] == "reported"


def test_caller_cannot_raise_its_own_identity_assurance(client):
    """No header can turn a declared identity into a verified or system one."""

    response = client.post(
        "/jobs",
        json=_REPORT_BODY,
        headers={
            **AUTHORITY_HEADERS,
            "X-Actor-Assurance": "SYSTEM_INTERNAL",
            "X-Actor-Kind": "SYSTEM",
            "Authorization": "Bearer forged",
        },
    )
    assert response.status_code == 201, response.text

    actor = client.get(f"/jobs/{response.json()['job_id']}/history").json()[
        "events"
    ][0]["actor"]

    assert actor == {
        "actor_id": "AUTHORITY-017",
        "role": "AUTHORITY",
        "kind": "HUMAN",
        "assurance": "DECLARED_UNVERIFIED",
    }


def test_system_actors_are_only_ever_recorded_for_automated_decisions(service, optimizer):
    """Human and automated actions stay distinguishable in every history."""

    job, _ = schedule_and_commit(service, optimizer)
    service.complete(job["job_id"], actor=WORKER)

    automated = {
        E.JOB_SCORED,
        E.OPTIMIZATION_COMPLETED,
        E.OPTIMIZATION_FAILED,
        E.BLOCK_PROPOSED,
        E.BLOCK_REPROPOSED,
        E.OPTIMIZATION_REFUSED,
        E.PROPOSAL_INVALIDATED,
        E.COMMITTED_BLOCK_PRESERVED,
        E.COMMITTED_BLOCK_CONFLICT,
    }

    for event in history(service, job["job_id"]):
        assert event.actor.is_system is (event.event_type in automated), event

        if event.actor.is_system:
            assert event.actor.assurance.value == "SYSTEM_INTERNAL"
        else:
            assert event.actor.assurance.value in {"DECLARED_UNVERIFIED", "NONE"}
