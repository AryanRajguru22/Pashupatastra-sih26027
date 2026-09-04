"""End-to-end disruption simulation and recovery."""

from __future__ import annotations

from dataclasses import dataclass

from contracts import (
    DisruptionEvent,
    OptimizationRequest,
    OptimizationResult,
)

from backend.app.optimizer.solver import solve
from backend.app.simulation.disruptions import apply_disruption


@dataclass(frozen=True)
class RecoverySimulation:
    """Result of applying a disruption and re-solving."""

    event: DisruptionEvent
    updated_request: OptimizationRequest
    recovery_result: OptimizationResult


def simulate_disruption(
    request: OptimizationRequest,
    event: DisruptionEvent,
) -> RecoverySimulation:
    """Apply a disruption and re-optimize the resulting request."""

    updated_request = apply_disruption(
        request,
        event,
    )

    recovery_result = solve(updated_request)

    return RecoverySimulation(
        event=event,
        updated_request=updated_request,
        recovery_result=recovery_result,
    )