"""Audit record model for optimization executions.

An OptimizationRunRecord is the immutable, append-only counterpart to
the last-write-wins `last_solver_status` / `last_refusal_reason`
columns on `maintenance_jobs`: those tell you the CURRENT outcome for
one job, overwritten on every optimization; this tells you EVERY
optimization that ever ran, and what it saw and returned.

Follows the same dataclass + to_dict()/from_dict() convention as
contracts/schemas.py rather than Pydantic, because this is an internal
persistence record with no HTTP surface of its own - nothing here is
a request/response body.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


# Documented placeholder actor. Authentication does not exist yet (see
# Sprint 3 recon, item P0-5); using this fixed, clearly-labelled value
# rather than inventing a user identity keeps every audit record honest
# about what is actually known versus assumed. Replace call sites with
# a real authenticated principal once auth exists - do not repurpose
# this constant's string value as a magic sentinel elsewhere.
SYSTEM_ACTOR = "system:unauthenticated-demo"

# Solver status recorded when solve() raised instead of returning an
# OptimizationResult. Distinct from every value in
# contracts.SolverStatus, so a query can never confuse "the solver
# concluded INFEASIBLE" with "the solver never returned at all".
ERROR_STATUS = "ERROR"


@dataclass(frozen=True)
class OptimizationRunRecord:
    """One immutable record of one optimization execution.

    request_json / result_json / provenance_snapshot_json are
    pre-serialized (see audit/service.py:serialize_*) rather than
    stored as nested structures, so the repository can persist them as
    opaque TEXT without needing to know the shape of
    OptimizationRequest/OptimizationResult - the same separation the
    jobs repository already keeps between its own SQL columns and
    `block_candidate_json`.
    """

    run_id: str
    actor: str
    trigger: str
    corridor_id: Optional[str]
    requested_at: str
    completed_at: str
    solver_status: str
    solve_time_seconds: Optional[float]
    request_json: str
    result_json: Optional[str]
    provenance_snapshot_json: Optional[str]
    error: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OptimizationRunRecord":
        return cls(
            run_id=data["run_id"],
            actor=data["actor"],
            trigger=data["trigger"],
            corridor_id=data.get("corridor_id"),
            requested_at=data["requested_at"],
            completed_at=data["completed_at"],
            solver_status=data["solver_status"],
            solve_time_seconds=(
                float(data["solve_time_seconds"])
                if data.get("solve_time_seconds") is not None
                else None
            ),
            request_json=data["request_json"],
            result_json=data.get("result_json"),
            provenance_snapshot_json=data.get("provenance_snapshot_json"),
            error=data.get("error"),
        )
