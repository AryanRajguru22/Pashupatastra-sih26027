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
BlockCandidate - a single maintenance work request needing a track
possession window.

Draft v0.1 - see docs/contracts.md before relying on this in production
code. Needs a domain review pass from the railway/data owner, especially
the WorkType enum values and whether ResourceRequirement is needed for v1.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from contracts.common import TimeWindow, WorkType


class BlockCandidate(BaseModel):
    block_id: str
    asset_id: str
    corridor_id: str
    section_id: str
    track_id: str

    work_type: WorkType
    duration_minutes: int = Field(gt=0)
    earliest_start: datetime
    latest_finish: datetime
    preferred_windows: list[TimeWindow] = Field(default_factory=list)

    # Written by the AI/ML subsystem. These are objective-function inputs
    # only - the optimizer must never treat them as hard constraints.
    priority_score: float = Field(default=0.0, ge=0, le=1)
    risk_score: float = Field(default=0.0, ge=0, le=1)

    requires_full_block: bool = False
    min_gap_before_next_train_minutes: int = Field(default=0, ge=0)

    dependencies: list[str] = Field(default_factory=list)
    mutually_exclusive_with: list[str] = Field(default_factory=list)
