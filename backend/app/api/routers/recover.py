"""Disruption recovery endpoint - thin HTTP wrapper around the existing
disruption/recovery simulation pipeline.

Contains no disruption or optimization logic of its own: applying the
disruption is delegated entirely to
backend.app.simulation.disruptions.apply_disruption, and re-optimization
to backend.app.optimizer.solver.solve, via
backend.app.simulation.simulator.simulate_disruption. Request/response
shapes are the existing contracts (contracts.RecoveryRequest,
contracts.RecoveryResponse), composed from the same OptimizationRequest,
DisruptionEvent, and OptimizationResult types /optimize already uses.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.app.simulation.simulator import simulate_disruption
from contracts import RecoveryRequest, RecoveryResponse

router = APIRouter()


@router.post("/recover", response_model=RecoveryResponse)
def recover(body: RecoveryRequest) -> RecoveryResponse:
    try:
        simulation = simulate_disruption(body.request, body.disruption)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RecoveryResponse(
        disruption=simulation.event,
        updated_request=simulation.updated_request,
        recovery_result=simulation.recovery_result,
    )
