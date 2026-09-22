"""D-3: legacy POST /optimize is a stateless demo/what-if solver.

Approved decisions (see docs/D3_LEGACY_OPTIMIZE_AUTHORIZATION_ARCHITECTURE.md):

  D3-1: legacy POST /optimize must create ZERO persistent
        optimization_runs rows.
  D3-2: existing legacy optimization_runs rows remain readable as
        stored via GET /v1/optimization-runs/{id} - that route is
        untouched by this slice.
  D3-3: jobs.db is demo/dev data; legacy-run visibility is revisited
        later.

This file proves the stateless invariant end to end, that the v1
audited path (POST /v1/corridors/{id}/optimize-jobs) is completely
unaffected, and that optimize.py keeps its existing architectural
boundary of not depending on backend.app.audit or backend.app.jobs.

Every test uses a scratch database - never the repository-root
jobs.db, and never a *different* env var than the one
backend/tests/conftest.py already points PASHUPAT_JOBS_DB at for the
whole test session.
"""

from __future__ import annotations

import ast
import inspect

import pytest
from fastapi.testclient import TestClient

from backend.app.api.routers import optimize as optimize_router
from backend.app.audit.models import SYSTEM_ACTOR, OptimizationRunRecord
from backend.app.audit.repository import AuditRepository
from backend.app.identity.actor import ActorRole
from backend.app.jobs.optimization import JobOptimizationService
from contracts import BlockCandidate, OptimizationRequest, PossessionWindow

from backend.tests.slice10_1d_helpers import (
    CORRIDOR,
    ENGINEER_ID,
    IN_XY,
    S_XY,
    assignment,
    build_service,
    enforcing,
    report,
)


def feasible_request(corridor_id: str = "D3-TEST") -> OptimizationRequest:
    """One block, fully covered by a possession window: solves OPTIMAL."""

    block = BlockCandidate(
        block_id="BLK-D3",
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


@pytest.fixture()
def client() -> TestClient:
    """raise_server_exceptions=False so a solver crash is observed as
    the route's actual 500 response, the same thing a real client
    behind uvicorn sees - not re-raised into the test process."""

    from backend.app.api.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def audit_writes(monkeypatch) -> list:
    """Spies on every AuditRepository.record call, on ANY database, for
    the duration of the test - a path-independent guarantee that
    nothing on a request touched the audit subsystem, stronger than a
    row count on one particular file (which one process-default
    database happens to resolve to is irrelevant to what this proves).
    """

    writes: list = []
    monkeypatch.setattr(
        AuditRepository, "record", lambda self, run: writes.append(run)
    )
    return writes


# ------------------------------------------------------------------
# 1 & 2. Anonymous and actor-headered requests both succeed and write
#        nothing.
# ------------------------------------------------------------------


def test_anonymous_optimize_request_succeeds_and_writes_no_row(
    client, audit_writes
):
    response = client.post("/optimize", json=feasible_request().to_dict())

    assert response.status_code == 200
    assert response.json()["status"] in {"OPTIMAL", "FEASIBLE"}
    assert audit_writes == []


def test_optimize_with_actor_headers_succeeds_and_writes_no_row(
    client, audit_writes
):
    response = client.post(
        "/optimize",
        json=feasible_request().to_dict(),
        headers={"X-Actor-Id": "ENGINEER-900", "X-Actor-Role": "ENGINEER"},
    )

    assert response.status_code == 200
    assert response.json()["status"] in {"OPTIMAL", "FEASIBLE"}
    assert audit_writes == []


# ------------------------------------------------------------------
# 3 & 7. Solver exception: existing 500 behavior preserved, and no
#         ERROR audit row (nor any row at all) is left behind.
# ------------------------------------------------------------------


def test_solver_exception_returns_existing_500_and_writes_no_row(
    client, monkeypatch, audit_writes
):
    def exploding_solve(_request: OptimizationRequest):
        raise RuntimeError("simulated solver crash")

    monkeypatch.setattr(optimize_router, "solve", exploding_solve)

    response = client.post("/optimize", json=feasible_request().to_dict())

    assert response.status_code == 500
    assert audit_writes == []


def test_solver_exception_leaves_no_error_audit_row(
    client, monkeypatch, audit_writes
):
    def exploding_solve(_request: OptimizationRequest):
        raise RuntimeError("simulated solver crash")

    monkeypatch.setattr(optimize_router, "solve", exploding_solve)

    client.post("/optimize", json=feasible_request().to_dict())

    # Zero writes of any kind already subsumes "no ERROR row" - the
    # audit_service.record_run ERROR path this route used to go
    # through no longer exists on this route at all.
    assert audit_writes == []


# ------------------------------------------------------------------
# 4. Source boundary: optimize.py imports neither backend.app.audit
#    nor backend.app.jobs.
# ------------------------------------------------------------------


def test_optimize_router_imports_no_audit_or_jobs_modules():
    source = inspect.getsource(optimize_router)
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    def _forbidden(prefix: str) -> bool:
        return any(
            m == prefix or m.startswith(prefix + ".")
            for m in imported_modules
        )

    assert not _forbidden("backend.app.audit")
    assert not _forbidden("backend.app.jobs")


# ------------------------------------------------------------------
# 5. An existing legacy optimization_runs row remains readable under
#    the current UnenforcedPolicy, and is not modified.
# ------------------------------------------------------------------


def test_existing_legacy_run_remains_readable_via_v1_get(tmp_path, monkeypatch):
    import backend.app.jobs.router as jobs_router_module
    from backend.app.api.main import app

    service = build_service(tmp_path)  # authorization=None -> UnenforcedPolicy
    monkeypatch.setattr(jobs_router_module, "service", service)
    monkeypatch.setattr(
        jobs_router_module,
        "optimization_service",
        JobOptimizationService(service),
    )

    record = OptimizationRunRecord(
        run_id="RUN-LEGACY-D3",
        actor=SYSTEM_ACTOR,
        trigger="optimize",
        corridor_id=CORRIDOR,
        requested_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01+00:00",
        solver_status="OPTIMAL",
        solve_time_seconds=0.05,
        request_json='{"marker": "legacy-row"}',
        result_json='{"scheduled_blocks": []}',
        provenance_snapshot_json=None,
        error=None,
    )
    AuditRepository(service.repository.db_path).record(record)

    http = TestClient(app)
    response = http.get(f"/v1/optimization-runs/{record.run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == record.run_id
    assert body["solver_status"] == "OPTIMAL"
    assert body["trigger"] == "optimize"

    stored = AuditRepository(service.repository.db_path).get(record.run_id)
    assert stored.solver_status == "OPTIMAL"
    assert stored.request_json == record.request_json
    assert stored.result_json == record.result_json


# ------------------------------------------------------------------
# 6. v1 authorized optimization is completely unaffected: it still
#    creates an optimization_runs row, trigger stays "jobs_optimize",
#    and 10.1D.3 corridor-complete authorization is unchanged.
# ------------------------------------------------------------------


def test_v1_optimize_jobs_still_audited_with_corridor_complete_authorization(
    tmp_path, monkeypatch
):
    """Uses the default single-section deployment corridor (CORRIDOR_A),
    not the slice10_1d_helpers three-section one: the synthetic asset
    generator that HTTP optimize-jobs relies on cannot assign work
    across a multi-section corridor, which is an unrelated pre-existing
    limitation - see test_http_optimize_jobs_permits_corridor_complete_
    engineer in test_slice10_1d_3_corridor_authority.py, which sidesteps
    it the same way (asserting only non-403, never a stored run)."""

    import backend.app.jobs.router as jobs_router_module
    from backend.app.api.main import app
    from backend.app.jobs.models import JobCreateRequest
    from backend.app.jobs.repository import JobRepository
    from backend.app.jobs.service import JobService

    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))
    monkeypatch.setattr(jobs_router_module, "service", service)
    monkeypatch.setattr(
        jobs_router_module,
        "optimization_service",
        JobOptimizationService(service),
    )

    corridor_id = service.corridor.corridor_id
    (section_id,) = service.registry.section_ids()

    service.create_job(
        JobCreateRequest(
            track_id=service.corridor.tracks[0].track_id,
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="D-3 v1 regression job",
        )
    )

    # Corridor-complete: 10.1D.3 requires the union of the engineer's
    # own scopes to cover every section of the corridor - here, the
    # corridor's only section.
    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, section_id, corridor_id=corridor_id),
        topology=((corridor_id, (section_id,)),),
    )

    http = TestClient(app)
    response = http.post(
        f"/v1/corridors/{corridor_id}/optimize-jobs",
        headers={"X-Actor-Id": ENGINEER_ID, "X-Actor-Role": "ENGINEER"},
    )

    assert response.status_code == 200

    runs = AuditRepository(service.repository.db_path).list_all()
    assert len(runs) == 1
    assert runs[0].trigger == "jobs_optimize"
    assert runs[0].corridor_id == corridor_id
    assert runs[0].actor == ENGINEER_ID
    assert runs[0].provenance_snapshot_json is not None


def test_v1_optimize_jobs_still_denies_partial_scope_engineer(
    tmp_path, monkeypatch
):
    """10.1D.3 corridor-complete authorization is unchanged by D-3:
    partial coverage is still refused, before any run is recorded."""

    import backend.app.jobs.router as jobs_router_module
    from backend.app.api.main import app

    service = build_service(tmp_path)
    monkeypatch.setattr(jobs_router_module, "service", service)
    monkeypatch.setattr(
        jobs_router_module,
        "optimization_service",
        JobOptimizationService(service),
    )

    report(service, IN_XY)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY)
    )

    http = TestClient(app)
    response = http.post(
        f"/v1/corridors/{CORRIDOR}/optimize-jobs",
        headers={"X-Actor-Id": ENGINEER_ID, "X-Actor-Role": "ENGINEER"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "AUTHORIZATION_DENIED"
    assert AuditRepository(service.repository.db_path).list_all() == []
