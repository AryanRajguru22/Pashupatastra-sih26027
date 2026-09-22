"""Optimization endpoint - thin HTTP wrapper around the CP-SAT solver.

Contains no optimization logic of its own: requests/responses are the
existing contracts (contracts.OptimizationRequest,
contracts.OptimizationResult) and solving is delegated entirely to
backend.app.optimizer.solver.solve.

Stateless (D-3): this legacy route is a demo/what-if solver and creates
no optimization_runs row and no audit trail. It imports neither the
audit subsystem nor the maintenance-jobs package, and must not gain
either dependency - that boundary is deliberate (see
backend/tests/test_job_lifecycle_accountability.py, which scans this
file's source for it). The audited, persistent path is
POST /v1/corridors/{corridor_id}/optimize-jobs.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.optimizer.solver import solve
from contracts import OptimizationRequest, OptimizationResult

router = APIRouter()


@router.post("/optimize", response_model=OptimizationResult)
def optimize(request: OptimizationRequest) -> OptimizationResult:
    return solve(request)
