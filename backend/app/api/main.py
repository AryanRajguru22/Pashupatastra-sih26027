"""FastAPI application - Phase 1 backend API.

Thin HTTP wrapper around the existing CP-SAT optimizer. This module
contains no optimization logic of its own: requests/responses are
validated using the existing contracts (contracts.OptimizationRequest,
contracts.OptimizationResult) and solving is delegated entirely to
backend.app.optimizer.solver.solve.

Run the dev server from the repo root:
    python -m uvicorn backend.app.api.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.app.optimizer.solver import solve
from contracts import OptimizationRequest, OptimizationResult

app = FastAPI(title="Pashupatastra API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/optimize", response_model=OptimizationResult)
def optimize(request: OptimizationRequest) -> OptimizationResult:
    return solve(request)
