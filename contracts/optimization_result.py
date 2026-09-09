"""DORMANT - NOT the canonical runtime contract.

Sprint 1 contract audit (verified by import graph, not by comment):
this module is imported by nothing except the other modules in this
same dormant set. No API route, no solver, no test and no frontend
type depends on it.

The CANONICAL contract used by POST /optimize, POST /recover, the
CP-SAT solver and frontend/src/types/contracts.ts is
contracts/schemas.py, re-exported through contracts/__init__.py.
Those are dataclasses using integer minutes from midnight; the models
here are Pydantic models using real datetimes. The two are NOT
interchangeable, and `from contracts import X` always resolves to the
schemas.py dataclass, never to the model defined here.

Kept deliberately rather than deleted: this set carries the v0.2 design
intent that the canonical contract still lacks - section_id on a block,
ObjectiveWeights, ConstraintsConfig, ExplanationEntry.binding_constraints
and OptimizationKPIs. Migrate-or-delete is an explicit decision that has
not been taken yet. Do not import from here until it has been.


Original module note
--------------------
OptimizationResult - the output of a CP-SAT solve.

Draft v0.1 - see docs/contracts.md.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from contracts.common import (
    ExplanationEntry,
    OptimizationStatus,
    ScheduledBlock,
)


class UnscheduledBlock(BaseModel):
    block_id: str
    reason: str


class OptimizationKPIs(BaseModel):
    asset_availability_pct: float = Field(ge=0, le=100)
    trains_affected: int = Field(ge=0)
    blocks_scheduled: int = Field(ge=0)
    risk_reduction_score: float = Field(ge=0)


class OptimizationResult(BaseModel):
    request_id: str
    status: OptimizationStatus

    scheduled_blocks: list[ScheduledBlock] = Field(default_factory=list)
    unscheduled_blocks: list[UnscheduledBlock] = Field(default_factory=list)

    kpis: OptimizationKPIs
    # Per scheduled block: why it landed where it did / which constraints
    # were binding. This is the audit trail surfaced in the command center.
    explainability: list[ExplanationEntry] = Field(default_factory=list)

    solve_time_ms: int = Field(ge=0)
    generated_at: datetime
