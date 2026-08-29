"""Utilities for applying railway disruptions to an optimization request.

This module does not modify the shared contract schemas or the optimizer.
It creates a new OptimizationRequest representing the situation after
a disruption.
"""

from __future__ import annotations

from contracts import (
    BlockCandidate,
    DisruptionEvent,
    DisruptionType,
    OptimizationRequest,
)


def _copy_request(request: OptimizationRequest) -> OptimizationRequest:
    """Return an independent copy of the optimization request."""
    return request.model_copy(deep=True)


def _is_affected_by_asset(
    block: BlockCandidate,
    event: DisruptionEvent,
) -> bool:
    """Return True when a block uses an asset affected by the disruption."""

    affected_assets = set(event.impact.unavailable_asset_ids)

    if event.affected_asset_id:
        affected_assets.add(event.affected_asset_id)

    return (
        block.asset_id in affected_assets
        or block.track_id in affected_assets
    )


def apply_asset_failure(
    request: OptimizationRequest,
    event: DisruptionEvent,
) -> OptimizationRequest:
    """Remove maintenance candidates that use an unavailable asset/track."""

    updated = _copy_request(request)

    unavailable_block_ids = {
        block.block_id
        for block in updated.block_candidates
        if _is_affected_by_asset(block, event)
    }

    unavailable_block_ids.update(event.impact.invalidated_block_ids)

    updated.block_candidates = [
        block
        for block in updated.block_candidates
        if block.block_id not in unavailable_block_ids
    ]

    updated.existing_committed_blocks = [
        block
        for block in updated.existing_committed_blocks
        if block.block_id not in unavailable_block_ids
    ]

    return updated


def apply_emergency_block_request(
    request: OptimizationRequest,
    event: DisruptionEvent,
) -> OptimizationRequest:
    """Ensure newly required emergency candidates participate in solving."""

    updated = _copy_request(request)

    existing_ids = {
        block.block_id
        for block in updated.block_candidates
    }

    missing_ids = [
        block_id
        for block_id in event.impact.newly_required_block_ids
        if block_id not in existing_ids
    ]

    if missing_ids:
        raise ValueError(
            "Emergency disruption references block candidates that are "
            f"not present in the request: {missing_ids}"
        )

    return updated


def apply_block_overrun(
    request: OptimizationRequest,
    event: DisruptionEvent,
) -> OptimizationRequest:
    """Invalidate blocks affected by a block overrun."""

    updated = _copy_request(request)

    invalidated_ids = set(event.impact.invalidated_block_ids)

    if event.affected_block_id:
        invalidated_ids.add(event.affected_block_id)

    updated.block_candidates = [
        block
        for block in updated.block_candidates
        if block.block_id not in invalidated_ids
    ]

    updated.existing_committed_blocks = [
        block
        for block in updated.existing_committed_blocks
        if block.block_id not in invalidated_ids
    ]

    return updated


def apply_disruption(
    request: OptimizationRequest,
    event: DisruptionEvent,
) -> OptimizationRequest:
    """Apply a supported disruption and return the resulting request."""

    if event.event_type == DisruptionType.ASSET_FAILURE:
        return apply_asset_failure(request, event)

    if event.event_type == DisruptionType.EMERGENCY_BLOCK_REQUEST:
        return apply_emergency_block_request(request, event)

    if event.event_type == DisruptionType.BLOCK_OVERRUN:
        return apply_block_overrun(request, event)

    if event.event_type in (
        DisruptionType.WEATHER,
        DisruptionType.TRAIN_DELAY,
    ):
        raise NotImplementedError(
            f"Disruption type {event.event_type.value} is not yet supported "
            "by the Phase 1 simulation layer."
        )

    raise ValueError(
        f"Unsupported disruption type: {event.event_type}"
    )