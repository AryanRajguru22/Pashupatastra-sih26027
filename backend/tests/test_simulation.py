from __future__ import annotations

import copy

import pytest

from contracts import (
    BlockCandidate,
    BlockStatus,
    DisruptionEvent,
    DisruptionType,
    OptimizationRequest,
    PossessionWindow,
)

from backend.app.simulation.disruptions import apply_disruption
from backend.app.simulation.simulator import simulate_disruption


def make_request() -> OptimizationRequest:
    """Create a small deterministic request for recovery simulation tests."""

    blocks = [
        BlockCandidate(
            block_id="BLOCK_UP_1",
            asset_id="ASSET_UP",
            track_id="UP",
            work_type="ROUTINE_INSPECTION",
            duration_minutes=60,
            earliest_start_minute=0,
            latest_end_minute=600,
            priority_score=0.8,
            risk_score=0.7,
        ),
        BlockCandidate(
            block_id="BLOCK_DOWN_1",
            asset_id="ASSET_DOWN",
            track_id="DOWN",
            work_type="EMERGENCY_REPAIR",
            duration_minutes=60,
            earliest_start_minute=0,
            latest_end_minute=600,
            priority_score=0.6,
            risk_score=0.5,
        ),
    ]

    return OptimizationRequest(
        corridor_id="CORRIDOR_A",
        horizon_minutes=600,
        tracks=["UP", "DOWN"],
        candidates=blocks,
        possession_windows=[],
        existing_committed_blocks=[],
        min_headway_minutes=10,
        train_timetable=[],
    )


def make_event(
    disruption_id: str,
    disruption_type: DisruptionType,
    *,
    track_id: str | None = None,
    affected_asset_id: str | None = None,
    new_candidate: BlockCandidate | None = None,
    start_minute: int = 0,
    end_minute: int = 600,
    description: str = "",
) -> DisruptionEvent:
    """Create a deterministic disruption event using the shared contract."""

    return DisruptionEvent(
        disruption_id=disruption_id,
        disruption_type=disruption_type.value,
        corridor_id="CORRIDOR_A",
        track_id=track_id,
        start_minute=start_minute,
        end_minute=end_minute,
        affected_asset_id=affected_asset_id,
        new_candidate=new_candidate,
        description=description,
    )


def test_asset_breakdown_removes_blocks_using_affected_asset():
    """Asset breakdown removes candidates belonging to the failed asset."""

    request = make_request()

    event = make_event(
        "DISRUPTION_001",
        DisruptionType.ASSET_BREAKDOWN,
        affected_asset_id="ASSET_UP",
        description="Asset ASSET_UP becomes unavailable.",
    )

    updated = apply_disruption(request, event)

    remaining_ids = {
        block.block_id
        for block in updated.candidates
    }

    assert "BLOCK_UP_1" not in remaining_ids
    assert "BLOCK_DOWN_1" in remaining_ids


def test_track_unavailable_removes_blocks_on_affected_track():
    """Track unavailability removes candidates on the affected track."""

    request = make_request()

    event = make_event(
        "DISRUPTION_002",
        DisruptionType.TRACK_UNAVAILABLE,
        track_id="UP",
        description="UP track becomes unavailable.",
    )

    updated = apply_disruption(request, event)

    remaining_ids = {
        block.block_id
        for block in updated.candidates
    }

    assert "BLOCK_UP_1" not in remaining_ids
    assert "BLOCK_DOWN_1" in remaining_ids


def test_invalidated_committed_block_is_removed():
    """An affected committed block is removed during recovery."""

    request = make_request()

    committed = copy.deepcopy(request.candidates[0])

    committed.is_committed = True
    committed.status = BlockStatus.COMMITTED.value

    request.existing_committed_blocks = [committed]

    event = make_event(
        "DISRUPTION_003",
        DisruptionType.ASSET_BREAKDOWN,
        affected_asset_id="ASSET_UP",
        description="Committed asset becomes unavailable.",
    )

    updated = apply_disruption(request, event)

    assert updated.existing_committed_blocks == []


def test_emergency_request_adds_new_candidate():
    """Emergency work adds a new candidate when one is supplied."""

    request = make_request()

    emergency = BlockCandidate(
        block_id="EMERGENCY_001",
        asset_id="ASSET_EMERGENCY",
        track_id="UP",
        work_type="EMERGENCY_REPAIR",
        duration_minutes=30,
        earliest_start_minute=100,
        latest_end_minute=200,
        priority_score=1.0,
        risk_score=1.0,
    )

    event = make_event(
        "DISRUPTION_004",
        DisruptionType.EMERGENCY_WORK,
        new_candidate=emergency,
        description="Emergency maintenance required.",
    )

    updated = apply_disruption(request, event)

    ids = {
        block.block_id
        for block in updated.candidates
    }

    assert "EMERGENCY_001" in ids


def test_emergency_request_without_candidate_does_not_change_request():
    """Emergency work without a candidate leaves candidates unchanged."""

    request = make_request()

    original_ids = {
        block.block_id
        for block in request.candidates
    }

    event = make_event(
        "DISRUPTION_005",
        DisruptionType.EMERGENCY_WORK,
        description="Emergency work requested without candidate.",
    )

    updated = apply_disruption(request, event)

    updated_ids = {
        block.block_id
        for block in updated.candidates
    }

    assert updated_ids == original_ids


def test_possession_curtailment_removes_overlapping_window():
    """Overlapping possession windows are removed only on the affected track."""

    request = make_request()

    request.possession_windows = [
        PossessionWindow(
            window_id="POS-001",
            track_id="UP",
            start_minute=100,
            end_minute=300,
        ),
        PossessionWindow(
            window_id="POS-002",
            track_id="DOWN",
            start_minute=100,
            end_minute=300,
        ),
    ]

    event = make_event(
        "DISRUPTION_006",
        DisruptionType.POSSESSION_CURTAILMENT,
        track_id="UP",
        start_minute=150,
        end_minute=200,
        description="UP possession window is curtailed.",
    )

    updated = apply_disruption(request, event)

    remaining_window_ids = {
        window.window_id
        for window in updated.possession_windows
    }

    assert "POS-001" not in remaining_window_ids
    assert "POS-002" in remaining_window_ids


def test_disruption_does_not_mutate_original_request():
    """Applying a disruption must leave the original request unchanged."""

    request = make_request()

    original_candidates = copy.deepcopy(request.candidates)
    original_committed = copy.deepcopy(
        request.existing_committed_blocks
    )
    original_windows = copy.deepcopy(
        request.possession_windows
    )

    event = make_event(
        "DISRUPTION_007",
        DisruptionType.ASSET_BREAKDOWN,
        affected_asset_id="ASSET_UP",
    )

    updated = apply_disruption(request, event)

    assert request.candidates == original_candidates
    assert (
        request.existing_committed_blocks
        == original_committed
    )
    assert request.possession_windows == original_windows

    assert len(updated.candidates) < len(request.candidates)


def test_full_simulation_returns_recovery_result():
    """Full disruption simulation applies the event and re-solves."""

    request = make_request()

    event = make_event(
        "DISRUPTION_008",
        DisruptionType.ASSET_BREAKDOWN,
        affected_asset_id="ASSET_UP",
        description="Asset ASSET_UP becomes unavailable.",
    )

    simulation = simulate_disruption(
        request,
        event,
    )

    assert simulation.event.disruption_id == "DISRUPTION_008"

    assert (
        simulation.updated_request.corridor_id
        == request.corridor_id
    )

    assert (
        simulation.recovery_result.corridor_id
        == request.corridor_id
    )

    assert all(
        block.block_id != "BLOCK_UP_1"
        for block in simulation.recovery_result.scheduled_blocks
    )


def test_unsupported_disruption_type_is_rejected():
    """Unsupported disruption types must raise ValueError."""

    request = make_request()

    event = DisruptionEvent(
        disruption_id="DISRUPTION_009",
        disruption_type="UNKNOWN_TYPE",
        corridor_id="CORRIDOR_A",
        description="Unsupported disruption.",
    )

    with pytest.raises(ValueError):
        apply_disruption(request, event)