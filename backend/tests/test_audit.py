"""Immutable optimization audit trail.

Every optimization execution - through the plain /optimize path, the
jobs pipeline, or a direct AuditService call - must produce exactly
one append-only optimization_runs record, whether the solver scheduled
everything, refused some of it, went INFEASIBLE, or raised. This file
covers the audit subsystem itself (backend/app/audit/) plus its two
integration points, and confirms the existing maintenance_jobs schema
and jobs workflow are untouched.

Every test uses an isolated temporary database - never the repository
jobs.db, and never the process-default PASHUPAT_JOBS_DB path.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.audit.models import (
    ERROR_STATUS,
    SYSTEM_ACTOR,
    OptimizationRunRecord,
)
from backend.app.audit.repository import AuditRepository
from backend.app.audit.service import (
    AuditService,
    serialize_request,
    serialize_result,
)
from backend.app.jobs.models import JobCreateRequest, JobStatus
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService
from backend.app.optimizer.solver import solve
from contracts import (
    BlockCandidate,
    OptimizationRequest,
    OptimizationResult,
    PossessionWindow,
)


# ------------------------------------------------------------------
# Request builders - minimal, self-contained OptimizationRequests that
# don't depend on the corridor generator or any fixture file.
# ------------------------------------------------------------------


def feasible_request(corridor_id: str = "AUDIT-TEST") -> OptimizationRequest:
    """One block, fully covered by a possession window: solves OPTIMAL."""

    block = BlockCandidate(
        block_id="BLK-FEASIBLE",
        asset_id="AST-1",
        track_id="UP-1",
        work_type="ROUTINE_INSPECTION",
        duration_minutes=60,
        earliest_start_minute=0,
        latest_end_minute=200,
        priority_score=0.5,
        risk_score=0.5,
    )

    window = PossessionWindow(
        window_id="POS-1",
        track_id="UP-1",
        start_minute=0,
        end_minute=200,
    )

    return OptimizationRequest(
        corridor_id=corridor_id,
        horizon_minutes=1440,
        tracks=["UP-1"],
        candidates=[block],
        possession_windows=[window],
    )


def possession_refused_request(
    corridor_id: str = "AUDIT-TEST",
) -> OptimizationRequest:
    """A block on a track no possession window covers.

    Solves to a solver status of OPTIMAL/FEASIBLE with the block
    refused for safety (Sprint 1 fail-closed behavior) - a partial,
    honestly-reported outcome rather than a scheduling success.
    """

    block = BlockCandidate(
        block_id="BLK-UNCOVERED",
        asset_id="AST-1",
        track_id="DOWN-1",
        work_type="ROUTINE_INSPECTION",
        duration_minutes=60,
        earliest_start_minute=0,
        latest_end_minute=200,
    )

    # Possession control is active (one window exists) but it covers a
    # different track, so DOWN-1 has no coverage at all.
    window = PossessionWindow(
        window_id="POS-1",
        track_id="UP-1",
        start_minute=0,
        end_minute=200,
    )

    return OptimizationRequest(
        corridor_id=corridor_id,
        horizon_minutes=1440,
        tracks=["UP-1", "DOWN-1"],
        candidates=[block],
        possession_windows=[window],
    )


def infeasible_request(corridor_id: str = "AUDIT-TEST") -> OptimizationRequest:
    """Two committed blocks pinned to overlap on the same track.

    Committed pinning forces presence=1 at fixed start/end for both;
    the track no-overlap constraint then has no feasible assignment,
    so the solver returns a genuine INFEASIBLE status.
    """

    committed_a = BlockCandidate(
        block_id="BLK-PINNED-A",
        asset_id="AST-1",
        track_id="UP-1",
        work_type="ROUTINE_INSPECTION",
        duration_minutes=60,
        earliest_start_minute=0,
        latest_end_minute=60,
        is_committed=True,
    )

    committed_b = BlockCandidate(
        block_id="BLK-PINNED-B",
        asset_id="AST-2",
        track_id="UP-1",
        work_type="ROUTINE_INSPECTION",
        duration_minutes=60,
        earliest_start_minute=0,
        latest_end_minute=60,
        is_committed=True,
        metadata={
            "committed_start_minute": 30,
            "committed_end_minute": 90,
        },
    )

    return OptimizationRequest(
        corridor_id=corridor_id,
        horizon_minutes=1440,
        tracks=["UP-1"],
        candidates=[committed_a, committed_b],
        existing_committed_blocks=[committed_a, committed_b],
    )


@pytest.fixture()
def repository(tmp_path: Path) -> AuditRepository:
    return AuditRepository(tmp_path / "audit.db")


@pytest.fixture()
def audit_service(repository: AuditRepository) -> AuditService:
    return AuditService(repository)


# ------------------------------------------------------------------
# 1. Successful optimization creates exactly one audit record
# ------------------------------------------------------------------


def test_successful_optimization_creates_exactly_one_audit_record(
    audit_service: AuditService,
    repository: AuditRepository,
):
    request = feasible_request()

    result = audit_service.record_run(request, solve, trigger="test")

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert len(result.scheduled_blocks) == 1

    runs = repository.list_all()
    assert len(runs) == 1

    run = runs[0]
    assert run.solver_status == result.status
    assert run.trigger == "test"
    assert run.corridor_id == "AUDIT-TEST"
    assert run.actor == SYSTEM_ACTOR
    assert run.error is None
    assert run.result_json is not None
    assert run.solve_time_seconds is not None
    assert run.solve_time_seconds >= 0


# ------------------------------------------------------------------
# 2. Infeasible / partial / refused optimization is audited
# ------------------------------------------------------------------


def test_possession_refused_outcome_is_audited(
    audit_service: AuditService,
    repository: AuditRepository,
):

    request = possession_refused_request()

    result = audit_service.record_run(request, solve, trigger="test")

    assert len(result.scheduled_blocks) == 0
    assert len(result.unscheduled_blocks) == 1
    assert "refused for safety" in result.rejection_reasons["BLK-UNCOVERED"]

    runs = repository.list_all()
    assert len(runs) == 1

    stored_result = json.loads(runs[0].result_json)
    assert stored_result["unscheduled_blocks"][0]["block_id"] == (
        "BLK-UNCOVERED"
    )
    assert (
        "refused for safety"
        in stored_result["rejection_reasons"]["BLK-UNCOVERED"]
    )


def test_infeasible_outcome_is_audited_with_its_own_status(
    audit_service: AuditService,
    repository: AuditRepository,
):

    request = infeasible_request()

    result = audit_service.record_run(request, solve, trigger="test")

    assert result.status == "INFEASIBLE"

    runs = repository.list_all()
    assert len(runs) == 1
    assert runs[0].solver_status == "INFEASIBLE"
    assert runs[0].result_json is not None


# ------------------------------------------------------------------
# 3. Solver failure is not silently lost
# ------------------------------------------------------------------


def test_solver_exception_is_audited_and_reraised(
    audit_service: AuditService,
    repository: AuditRepository,
):
    def exploding_solve(_request: OptimizationRequest) -> OptimizationResult:
        raise RuntimeError("simulated solver crash")

    request = feasible_request()

    with pytest.raises(RuntimeError, match="simulated solver crash"):
        audit_service.record_run(request, exploding_solve, trigger="test")

    runs = repository.list_all()
    assert len(runs) == 1

    run = runs[0]
    assert run.solver_status == ERROR_STATUS
    assert run.result_json is None
    assert run.error is not None
    assert "simulated solver crash" in run.error
    assert run.request_json is not None


# ------------------------------------------------------------------
# 4. Request/result serialization works
# ------------------------------------------------------------------


def test_request_serialization_is_deterministic_and_round_trips():
    request = feasible_request()

    first = serialize_request(request)
    second = serialize_request(request)

    assert first == second  # deterministic: same input, same bytes

    restored = OptimizationRequest.from_dict(json.loads(first))
    assert restored.to_dict() == request.to_dict()


def test_result_serialization_is_deterministic_and_round_trips():

    result = solve(feasible_request())

    first = serialize_result(result)
    second = serialize_result(result)

    assert first == second

    restored = OptimizationResult.from_dict(json.loads(first))
    assert restored.to_dict() == result.to_dict()


# ------------------------------------------------------------------
# 5. Audit records are append-only
# ------------------------------------------------------------------


def test_repository_exposes_no_mutation_methods():
    public_methods = {
        name
        for name in dir(AuditRepository)
        if not name.startswith("_")
    }

    assert "update" not in public_methods
    assert "delete" not in public_methods


def test_duplicate_run_id_is_rejected_not_overwritten(
    repository: AuditRepository,
):
    record = OptimizationRunRecord(
        run_id="RUN-DUPLICATE",
        actor=SYSTEM_ACTOR,
        trigger="test",
        corridor_id="C1",
        requested_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01+00:00",
        solver_status="OPTIMAL",
        solve_time_seconds=0.1,
        request_json="{}",
        result_json="{}",
        provenance_snapshot_json=None,
        error=None,
    )

    repository.record(record)

    with pytest.raises(sqlite3.IntegrityError):
        repository.record(record)

    # The original row must be exactly what was first written.
    assert len(repository.list_all()) == 1


def test_direct_sql_update_is_rejected_by_trigger(
    repository: AuditRepository,
):
    record = OptimizationRunRecord(
        run_id="RUN-IMMUTABLE",
        actor=SYSTEM_ACTOR,
        trigger="test",
        corridor_id="C1",
        requested_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01+00:00",
        solver_status="OPTIMAL",
        solve_time_seconds=0.1,
        request_json="{}",
        result_json="{}",
        provenance_snapshot_json=None,
        error=None,
    )
    repository.record(record)

    conn = sqlite3.connect(repository.db_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                "UPDATE optimization_runs SET solver_status = 'HACKED' "
                "WHERE run_id = 'RUN-IMMUTABLE'"
            )
    finally:
        conn.close()

    assert repository.get("RUN-IMMUTABLE").solver_status == "OPTIMAL"


def test_direct_sql_delete_is_rejected_by_trigger(
    repository: AuditRepository,
):
    record = OptimizationRunRecord(
        run_id="RUN-UNDELETABLE",
        actor=SYSTEM_ACTOR,
        trigger="test",
        corridor_id="C1",
        requested_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01+00:00",
        solver_status="OPTIMAL",
        solve_time_seconds=0.1,
        request_json="{}",
        result_json="{}",
        provenance_snapshot_json=None,
        error=None,
    )
    repository.record(record)

    conn = sqlite3.connect(repository.db_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                "DELETE FROM optimization_runs WHERE run_id = 'RUN-UNDELETABLE'"
            )
    finally:
        conn.close()

    assert repository.get("RUN-UNDELETABLE") is not None


# ------------------------------------------------------------------
# 6. A second optimization creates a separate run
# ------------------------------------------------------------------


def test_second_optimization_creates_a_separate_run(
    audit_service: AuditService,
    repository: AuditRepository,
):

    audit_service.record_run(feasible_request(), solve, trigger="test")
    audit_service.record_run(feasible_request(), solve, trigger="test")

    runs = repository.list_all()
    assert len(runs) == 2
    assert runs[0].run_id != runs[1].run_id


# ------------------------------------------------------------------
# 7 & 8. maintenance_jobs schema untouched; jobs workflow integrated
# ------------------------------------------------------------------


def test_maintenance_jobs_schema_is_unchanged_by_audit_initialization(
    tmp_path: Path,
):
    db_path = tmp_path / "shared.db"

    job_repository = JobRepository(db_path)

    with sqlite3.connect(db_path) as conn:
        before = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(maintenance_jobs)"
            )
        }

    service = JobService(repository=job_repository)
    job = service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="pre-audit job",
        )
    )

    # Constructing the audit repository against the SAME db file must
    # not add, remove or rename a single maintenance_jobs column, and
    # must not touch the row already in it.
    AuditRepository(db_path)

    with sqlite3.connect(db_path) as conn:
        after = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(maintenance_jobs)"
            )
        }
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert after == before
    assert "maintenance_jobs" in tables
    assert "optimization_runs" in tables

    unchanged = job_repository.get(job["job_id"])
    assert unchanged["job_id"] == job["job_id"]
    assert unchanged["status"] == JobStatus.REPORTED.value


def test_jobs_pipeline_optimize_corridor_is_audited(tmp_path: Path):
    """optimize_corridor's existing behavior and response shape are
    unchanged; it now also produces exactly one audit record in the
    SAME database file as the jobs it optimized - not a shared default.
    """

    db_path = tmp_path / "jobs.db"
    service = JobService(repository=JobRepository(db_path))
    optimizer = JobOptimizationService(service)

    service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="audited job",
        )
    )

    outcome = optimizer.optimize_corridor("CORRIDOR_A")

    # Existing response shape, unchanged.
    assert outcome["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert "possession_source" in outcome

    audit_repository = AuditRepository(db_path)
    runs = audit_repository.list_by_corridor("CORRIDOR_A")

    assert len(runs) == 1
    assert runs[0].trigger == "jobs_optimize"
    assert runs[0].solver_status == outcome["solver_status"]
    assert runs[0].provenance_snapshot_json is not None

    provenance = json.loads(runs[0].provenance_snapshot_json)
    assert provenance["possession_source"] == outcome["possession_source"]


def test_jobs_pipeline_audit_survives_a_second_optimize_call(
    tmp_path: Path,
):
    db_path = tmp_path / "jobs.db"
    service = JobService(repository=JobRepository(db_path))
    optimizer = JobOptimizationService(service)

    service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="first job",
        )
    )
    optimizer.optimize_corridor("CORRIDOR_A")

    service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="ROUTINE_INSPECTION",
            distance_start=5000.0,
            distance_end=5200.0,
            workers_min=1,
            workers_max=2,
            description="second job",
        )
    )
    optimizer.optimize_corridor("CORRIDOR_A")

    runs = AuditRepository(db_path).list_by_corridor("CORRIDOR_A")
    assert len(runs) == 2
    assert runs[0].run_id != runs[1].run_id


# ------------------------------------------------------------------
# HTTP-level integration: POST /optimize is audited end to end
# ------------------------------------------------------------------


def test_optimize_endpoint_is_audited(tmp_path: Path, monkeypatch):
    from backend.app.api.routers import optimize as optimize_router

    isolated = AuditRepository(tmp_path / "http_audit.db")
    monkeypatch.setattr(
        optimize_router,
        "_audit_service",
        AuditService(isolated),
    )

    from backend.app.api.main import app

    client = TestClient(app)

    response = client.post(
        "/optimize",
        json=feasible_request().to_dict(),
    )

    assert response.status_code == 200

    runs = isolated.list_all()
    assert len(runs) == 1
    assert runs[0].trigger == "optimize"
    assert runs[0].solver_status == response.json()["status"]
