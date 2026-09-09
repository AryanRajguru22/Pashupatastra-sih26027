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
OptimizationRequest - the input the CP-SAT engine solves.

Draft v0.1 - see docs/contracts.md.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from contracts.block_candidate import BlockCandidate
from contracts.common import (
    ConstraintsConfig,
    ObjectiveWeights,
    ScheduledBlock,
    TimeWindow,
    TrainService,
)


class OptimizationRequest(BaseModel):
    request_id: str
    corridor_id: str
    planning_horizon: TimeWindow

    block_candidates: list[BlockCandidate]
    train_timetable: list[TrainService] = Field(default_factory=list)

    # Blocks already locked in from a prior solve - needed so a
    # re-optimization after a disruption doesn't needlessly reshuffle
    # work that's already committed/underway.
    existing_committed_blocks: list[ScheduledBlock] = Field(default_factory=list)

    objective_weights: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    constraints_config: ConstraintsConfig = Field(default_factory=ConstraintsConfig)
