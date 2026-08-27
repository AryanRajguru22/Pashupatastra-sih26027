"""OptimizationResult - the output of a CP-SAT solve.

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
