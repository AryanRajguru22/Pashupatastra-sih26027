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
Shared enums and supporting types used across the contract schemas.

These are draft v0.1 and expected to change after domain review
(see docs/contracts.md). Treat changes to this package like an API
version bump - sync with the team before editing.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class WorkType(str, Enum):
    RENEWAL = "RENEWAL"
    INSPECTION = "INSPECTION"
    REPAIR = "REPAIR"
    PREVENTIVE = "PREVENTIVE"


class OptimizationStatus(str, Enum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    TIMEOUT = "TIMEOUT"


class DisruptionType(str, Enum):
    ASSET_FAILURE = "ASSET_FAILURE"
    WEATHER = "WEATHER"
    EMERGENCY_BLOCK_REQUEST = "EMERGENCY_BLOCK_REQUEST"
    TRAIN_DELAY = "TRAIN_DELAY"
    BLOCK_OVERRUN = "BLOCK_OVERRUN"


class ReoptimizationScope(str, Enum):
    FULL_HORIZON = "FULL_HORIZON"
    ROLLING_WINDOW = "ROLLING_WINDOW"
    AFFECTED_SEGMENT_ONLY = "AFFECTED_SEGMENT_ONLY"


class TimeWindow(BaseModel):
    start: datetime
    end: datetime


class TrainService(BaseModel):
    """A scheduled train movement the optimizer must protect."""

    train_id: str
    corridor_id: str
    section_id: str
    track_id: str
    departure: datetime
    arrival: datetime


class ScheduledBlock(BaseModel):
    """A BlockCandidate that has been assigned a concrete time/track slot."""

    block_id: str
    track_id: str
    start: datetime
    end: datetime


class ObjectiveWeights(BaseModel):
    """Relative weights the optimizer uses when trading off objectives.

    These tune priorities among feasible schedules; they never permit
    violating a hard safety constraint.
    """

    risk_reduction_weight: float = Field(default=1.0, ge=0)
    asset_availability_weight: float = Field(default=1.0, ge=0)
    blocks_completed_weight: float = Field(default=1.0, ge=0)


class ConstraintsConfig(BaseModel):
    """Toggles/limits for hard constraints applied during solving."""

    max_concurrent_blocks_per_corridor: int = Field(default=1, ge=0)
    min_headway_minutes: int = Field(default=0, ge=0)


class ExplanationEntry(BaseModel):
    """Why a particular scheduling decision was made - the audit trail."""

    block_id: str
    reason: str
    binding_constraints: list[str] = Field(default_factory=list)
