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
DisruptionEvent - something that invalidates part of a committed plan
and (usually) triggers a re-optimization.

Draft v0.1 - see docs/contracts.md.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from contracts.common import DisruptionType, ReoptimizationScope


class DisruptionImpact(BaseModel):
    """What the disruption actually changes about the world."""

    unavailable_asset_ids: list[str] = []
    invalidated_block_ids: list[str] = []
    newly_required_block_ids: list[str] = []


class DisruptionEvent(BaseModel):
    event_id: str
    event_type: DisruptionType

    affected_asset_id: str | None = None
    affected_block_id: str | None = None
    affected_corridor_id: str | None = None

    timestamp: datetime
    description: str

    impact: DisruptionImpact

    triggers_reoptimization: bool = True
    reoptimization_scope: ReoptimizationScope = ReoptimizationScope.AFFECTED_SEGMENT_ONLY
