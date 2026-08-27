"""BlockCandidate - a single maintenance work request needing a track
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
