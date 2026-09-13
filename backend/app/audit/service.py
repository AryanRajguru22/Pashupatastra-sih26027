"""Records an immutable audit trail around any solver execution.

This module contains no optimization logic and does not import
backend.app.optimizer.solver at all: the solver function to run is
passed in by the caller (record_run's `solve_fn` parameter) and called
exactly once, unmodified. That keeps the existing deterministic solver
configuration (single search worker, fixed random seed - see
backend/app/optimizer/solver.py) completely untouched, and lets a test
substitute a fake solve_fn to exercise the failure path without
needing OR-Tools to actually fail.

Every call to record_run produces exactly one OptimizationRunRecord,
successful or not:

  - solve_fn returns an OptimizationResult -> one record capturing the
    solver's own status (OPTIMAL/FEASIBLE/INFEASIBLE/NO_SOLUTION) and
    the full result.
  - solve_fn raises              -> one ERROR record capturing the
    exception message, and the exception is re-raised unchanged. The
    audit write must never replace or swallow the original error.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from contracts import OptimizationRequest, OptimizationResult

from backend.app.audit.models import (
    ERROR_STATUS,
    SYSTEM_ACTOR,
    OptimizationRunRecord,
)
from backend.app.audit.repository import AuditRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def serialize_request(request: OptimizationRequest) -> str:
    """Deterministic, safe JSON serialization of an OptimizationRequest.

    request.to_dict() already returns a plain dict of JSON-safe
    primitives (contracts/schemas.py dataclasses only ever nest str,
    int, float, bool, list and dict), so this never needs a custom
    encoder for a domain type. sort_keys=True is what makes the
    serialization deterministic: to_dict() itself is written out in a
    fixed field order already, but sort_keys removes any dependence on
    that incidental ordering, so two runs over an identical request
    always produce byte-identical request_json.
    """
    return json.dumps(request.to_dict(), sort_keys=True, default=str)


def serialize_result(result: OptimizationResult) -> str:
    return json.dumps(result.to_dict(), sort_keys=True, default=str)


def _serialize_provenance(
    provenance_snapshot: Optional[dict[str, Any]],
) -> Optional[str]:
    if provenance_snapshot is None:
        return None
    return json.dumps(provenance_snapshot, sort_keys=True, default=str)


class AuditService:

    def __init__(self, repository: AuditRepository | None = None):
        self.repository = repository or AuditRepository()

    def record_run(
        self,
        request: OptimizationRequest,
        solve_fn: Callable[[OptimizationRequest], OptimizationResult],
        *,
        trigger: str,
        actor: str = SYSTEM_ACTOR,
        provenance_snapshot: Optional[dict[str, Any]] = None,
    ) -> OptimizationResult:
        """Execute solve_fn(request) exactly once and audit the outcome.

        Returns the OptimizationResult on success. On failure, writes
        an ERROR audit record and then re-raises the original
        exception - callers see exactly the same exception they would
        have seen without auditing.
        """

        run_id = f"RUN-{uuid.uuid4().hex.upper()}"
        requested_at = _now()
        request_json = serialize_request(request)
        provenance_json = _serialize_provenance(provenance_snapshot)
        corridor_id = getattr(request, "corridor_id", None)

        try:
            result = solve_fn(request)
        except Exception as exc:
            self.repository.record(
                OptimizationRunRecord(
                    run_id=run_id,
                    actor=actor,
                    trigger=trigger,
                    corridor_id=corridor_id,
                    requested_at=requested_at,
                    completed_at=_now(),
                    solver_status=ERROR_STATUS,
                    solve_time_seconds=None,
                    request_json=request_json,
                    result_json=None,
                    provenance_snapshot_json=provenance_json,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            raise

        self.repository.record(
            OptimizationRunRecord(
                run_id=run_id,
                actor=actor,
                trigger=trigger,
                corridor_id=corridor_id,
                requested_at=requested_at,
                completed_at=_now(),
                solver_status=result.status,
                solve_time_seconds=result.solve_time_seconds,
                request_json=request_json,
                result_json=serialize_result(result),
                provenance_snapshot_json=provenance_json,
                error=None,
            )
        )

        return result
