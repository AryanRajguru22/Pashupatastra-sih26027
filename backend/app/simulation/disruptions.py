"""Utilities for applying railway disruptions to an optimization request."""

from __future__ import annotations

from contracts import (
    BlockStatus,
    DisruptionEvent,
    DisruptionType,
    OptimizationRequest,
)


def _copy_request(
    request: OptimizationRequest,
) -> OptimizationRequest:
    """Return an independent copy of the optimization request."""
    return OptimizationRequest.from_dict(request.to_dict())


def apply_asset_breakdown(
    request: OptimizationRequest,
    event: DisruptionEvent,
) -> OptimizationRequest:
    """Remove candidates using the failed asset."""

    updated = _copy_request(request)

    if not event.affected_asset_id:
        return updated

    affected_asset = event.affected_asset_id

    updated.candidates = [
        block
        for block in updated.candidates
        if block.asset_id != affected_asset
    ]

    updated.existing_committed_blocks = [
        block
        for block in updated.existing_committed_blocks
        if block.asset_id != affected_asset
    ]

    return updated


def apply_track_unavailable(
    request: OptimizationRequest,
    event: DisruptionEvent,
) -> OptimizationRequest:
    """Remove candidates using the unavailable track."""

    updated = _copy_request(request)

    if not event.track_id:
        return updated

    affected_track = event.track_id

    updated.candidates = [
        block
        for block in updated.candidates
        if block.track_id != affected_track
    ]

    updated.existing_committed_blocks = [
        block
        for block in updated.existing_committed_blocks
        if block.track_id != affected_track
    ]

    return updated


def apply_emergency_work(
    request: OptimizationRequest,
    event: DisruptionEvent,
) -> OptimizationRequest:
    """Add a new emergency candidate when one is supplied."""

    updated = _copy_request(request)

    if event.new_candidate is None:
        return updated

    existing_ids = {
        block.block_id
        for block in updated.candidates
    }

    if event.new_candidate.block_id not in existing_ids:
        emergency_candidate = event.new_candidate

        emergency_candidate.status = BlockStatus.PLANNED.value

        updated.candidates.append(
            emergency_candidate
        )

    return updated


def apply_possession_curtailment(
    request: OptimizationRequest,
    event: DisruptionEvent,
) -> OptimizationRequest:
    """Reduce possession availability during a disruption interval.

    Windows overlapping the disruption are removed. This deliberately
    avoids inventing partial-window semantics not represented by the
    current shared contract.
    """

    updated = _copy_request(request)

    remaining_windows = []

    for window in updated.possession_windows:
        same_track = (
            event.track_id is None
            or window.track_id == event.track_id
        )

        overlaps = (
            window.start_minute < event.end_minute
            and event.start_minute < window.end_minute
        )

        if same_track and overlaps:
            continue

        remaining_windows.append(window)

    updated.possession_windows = remaining_windows

    return updated


def apply_disruption(
    request: OptimizationRequest,
    event: DisruptionEvent,
) -> OptimizationRequest:
    """Apply a supported Phase 1 disruption."""

    disruption_type = event.disruption_type

    if disruption_type == DisruptionType.ASSET_BREAKDOWN.value:
        return apply_asset_breakdown(
            request,
            event,
        )

    if disruption_type == DisruptionType.TRACK_UNAVAILABLE.value:
        return apply_track_unavailable(
            request,
            event,
        )

    if disruption_type == DisruptionType.EMERGENCY_WORK.value:
        return apply_emergency_work(
            request,
            event,
        )

    if disruption_type == DisruptionType.POSSESSION_CURTAILMENT.value:
        return apply_possession_curtailment(
            request,
            event,
        )

    raise ValueError(
        f"Unsupported disruption type: {disruption_type}"
    )