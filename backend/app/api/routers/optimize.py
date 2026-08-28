"""Optimization endpoint - thin HTTP wrapper around the CP-SAT solver.

Contains no optimization logic of its own: requests/responses are the
existing contracts (contracts.OptimizationRequest,
contracts.OptimizationResult) and solving is delegated entirely to
backend.app.optimizer.solver.solve.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.optimizer.solver import solve
from contracts import OptimizationRequest, OptimizationResult

router = APIRouter()


@router.post("/optimize", response_model=OptimizationResult)
def optimize(request: OptimizationRequest) -> OptimizationResult:
    return solve(request)
