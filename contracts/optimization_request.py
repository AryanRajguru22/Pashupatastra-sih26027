"""OptimizationRequest - the input the CP-SAT engine solves.

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
