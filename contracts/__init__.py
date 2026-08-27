"""Shared data contracts for Pashupatastra (SIH26027).

Draft v0.1. This package is the single shared surface between all
subsystems (optimizer, ML, simulation, API, frontend). Changes here
should be treated like an API version bump - sync with the team before
editing. See docs/contracts.md for the human-readable description and
rationale.
"""

from contracts.block_candidate import BlockCandidate
from contracts.common import (
    ConstraintsConfig,
    DisruptionType,
    ExplanationEntry,
    ObjectiveWeights,
    OptimizationStatus,
    ReoptimizationScope,
    ScheduledBlock,
    TimeWindow,
    TrainService,
    WorkType,
)
from contracts.disruption_event import DisruptionEvent, DisruptionImpact
from contracts.optimization_request import OptimizationRequest
from contracts.optimization_result import (
    OptimizationKPIs,
    OptimizationResult,
    UnscheduledBlock,
)

__all__ = [
    "BlockCandidate",
    "ConstraintsConfig",
    "DisruptionEvent",
    "DisruptionImpact",
    "DisruptionType",
    "ExplanationEntry",
    "ObjectiveWeights",
    "OptimizationKPIs",
    "OptimizationRequest",
    "OptimizationResult",
    "OptimizationStatus",
    "ReoptimizationScope",
    "ScheduledBlock",
    "TimeWindow",
    "TrainService",
    "UnscheduledBlock",
    "WorkType",
]
