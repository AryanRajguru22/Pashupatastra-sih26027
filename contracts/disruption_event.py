"""DisruptionEvent - something that invalidates part of a committed plan
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
