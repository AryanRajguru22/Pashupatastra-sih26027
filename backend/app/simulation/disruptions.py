"""Utilities for applying railway disruptions to an optimization request."""

from __future__ import annotations

import copy

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
    return OptimizationRequest.from_dict(
        request.to_dict()
    )


def apply_asset_breakdown(
    request: OptimizationRequest,
    event: DisruptionEvent,
) -> OptimizationRequest:
    """Remove candidates and committed blocks using the failed asset."""

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
    """Remove candidates and committed blocks on the unavailable track."""

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

    # Do not add a duplicate candidate.
    if event.new_candidate.block_id in existing_ids:
        return updated

    # Use deepcopy because the active contract does not provide
    # Pydantic's model_copy() method.
    emergency_candidate = copy.deepcopy(
        event.new_candidate
    )

    emergency_candidate.status = (
        BlockStatus.PLANNED.value
    )

    updated.candidates.append(
        emergency_candidate
    )

    return updated


def apply_possession_curtailment(
    request: OptimizationRequest,
    event: DisruptionEvent,
) -> OptimizationRequest:
    """Remove possession windows overlapping the disruption interval."""

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

        remaining_windows.append(
            window
        )

    # Fail closed. The solver treats an empty possession_windows list as
    # "this request does not model possessions", which lifts possession
    # protection entirely. A curtailment that removed the last window
    # would therefore silently turn protection OFF - the exact opposite
    # of what curtailing a possession means. Refuse instead; the
    # /recover router maps ValueError to HTTP 400.
    if request.possession_windows and not remaining_windows:
        raise ValueError(
            "Possession curtailment would remove every possession "
            "window from the request. Refusing: an empty possession "
            "window list disables possession protection rather than "
            "tightening it. Curtail a narrower interval or a single "
            "track."
        )

    updated.possession_windows = (
        remaining_windows
    )

    return updated


def apply_disruption(
    request: OptimizationRequest,
    event: DisruptionEvent,
) -> OptimizationRequest:
    """Apply a supported Phase 1 disruption."""

    # IMPORTANT:
    # The active contracts.schemas.DisruptionEvent uses
    # `disruption_type`, not `event_type`.

    disruption_type = event.disruption_type

    if (
        disruption_type
        == DisruptionType.ASSET_BREAKDOWN.value
    ):
        return apply_asset_breakdown(
            request,
            event,
        )

    if (
        disruption_type
        == DisruptionType.TRACK_UNAVAILABLE.value
    ):
        return apply_track_unavailable(
            request,
            event,
        )

    if (
        disruption_type
        == DisruptionType.EMERGENCY_WORK.value
    ):
        return apply_emergency_work(
            request,
            event,
        )

    if (
        disruption_type
        == DisruptionType.POSSESSION_CURTAILMENT.value
    ):
        return apply_possession_curtailment(
            request,
            event,
        )

    raise ValueError(
        f"Unsupported disruption type: {disruption_type}"
    )