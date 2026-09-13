"""Optimization endpoint - thin HTTP wrapper around the CP-SAT solver.

Contains no optimization logic of its own: requests/responses are the
existing contracts (contracts.OptimizationRequest,
contracts.OptimizationResult) and solving is delegated entirely to
backend.app.optimizer.solver.solve.

Every call is wrapped in AuditService.record_run so it produces an
immutable optimization_runs record - success, refusal/infeasibility,
or solver exception alike - without solve() itself being touched. See
backend/app/audit/service.py for what "wrapped" means: solve_fn is
called exactly once, and a solver exception is re-raised unchanged
after being recorded.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.audit.service import AuditService
from backend.app.optimizer.solver import solve
from contracts import OptimizationRequest, OptimizationResult

router = APIRouter()

_audit_service = AuditService()


@router.post("/optimize", response_model=OptimizationResult)
def optimize(request: OptimizationRequest) -> OptimizationResult:
    return _audit_service.record_run(
        request,
        solve,
        trigger="optimize",
    )
