from datetime import datetime, timezone

import pytest

from contracts import (
    BlockCandidate,
    DisruptionEvent,
    DisruptionImpact,
    DisruptionType,
    OptimizationRequest,
    ScheduledBlock,
    TimeWindow,
    WorkType,
)

from backend.app.simulation.disruptions import apply_disruption
from backend.app.simulation.simulator import simulate_disruption


def make_request() -> OptimizationRequest:
    """Create a small deterministic request for simulation tests."""

    start = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)

    blocks = [
        BlockCandidate(
            block_id="BLOCK_UP_1",
            asset_id="ASSET_UP",
            corridor_id="CORRIDOR_A",
            section_id="SECTION_1",
            track_id="UP",
            work_type=WorkType.INSPECTION,
            duration_minutes=60,
            earliest_start=start,
            latest_finish=end,
            priority_score=0.8,
            risk_score=0.7,
        ),
        BlockCandidate(
            block_id="BLOCK_DOWN_1",
            asset_id="ASSET_DOWN",
            corridor_id="CORRIDOR_A",
            section_id="SECTION_1",
            track_id="DOWN",
            work_type=WorkType.REPAIR,
            duration_minutes=60,
            earliest_start=start,
            latest_finish=end,
            priority_score=0.6,
            risk_score=0.5,
        ),
    ]

    return OptimizationRequest(
        request_id="REQ_SIM_001",
        corridor_id="CORRIDOR_A",
        planning_horizon=TimeWindow(
            start=start,
            end=end,
        ),
        block_candidates=blocks,
    )


def test_asset_failure_removes_blocks_using_affected_asset():
    """An asset failure should remove blocks using that actual asset."""

    request = make_request()

    event = DisruptionEvent(
        event_id="DISRUPTION_001",
        event_type=DisruptionType.ASSET_FAILURE,
        affected_asset_id="ASSET_UP",
        timestamp=datetime(
            2026,
            8,
            29,
            9,
            0,
            tzinfo=timezone.utc,
        ),
        description="Asset ASSET_UP becomes unavailable.",
        impact=DisruptionImpact(
            unavailable_asset_ids=["ASSET_UP"],
            invalidated_block_ids=[],
            newly_required_block_ids=[],
        ),
    )

    updated = apply_disruption(request, event)

    remaining_ids = {
        block.block_id
        for block in updated.block_candidates
    }

    assert "BLOCK_UP_1" not in remaining_ids
    assert "BLOCK_DOWN_1" in remaining_ids


def test_invalidated_committed_block_is_removed():
    """An invalidated committed block should no longer remain committed."""

    request = make_request()

    request.existing_committed_blocks = [
        ScheduledBlock(
            block_id="BLOCK_UP_1",
            track_id="UP",
            start=datetime(
                2026,
                8,
                29,
                10,
                0,
                tzinfo=timezone.utc,
            ),
            end=datetime(
                2026,
                8,
                29,
                11,
                0,
                tzinfo=timezone.utc,
            ),
        )
    ]

    event = DisruptionEvent(
        event_id="DISRUPTION_002",
        event_type=DisruptionType.ASSET_FAILURE,
        affected_asset_id="ASSET_UP",
        timestamp=datetime(
            2026,
            8,
            29,
            9,
            0,
            tzinfo=timezone.utc,
        ),
        description="Asset ASSET_UP becomes unavailable.",
        impact=DisruptionImpact(
            unavailable_asset_ids=["ASSET_UP"],
            invalidated_block_ids=["BLOCK_UP_1"],
            newly_required_block_ids=[],
        ),
    )

    updated = apply_disruption(request, event)

    assert updated.existing_committed_blocks == []


def test_emergency_request_validates_existing_candidate():
    """Emergency requests currently validate that the candidate exists."""

    request = make_request()

    event = DisruptionEvent(
        event_id="DISRUPTION_003",
        event_type=DisruptionType.EMERGENCY_BLOCK_REQUEST,
        timestamp=datetime(
            2026,
            8,
            29,
            9,
            0,
            tzinfo=timezone.utc,
        ),
        description="Emergency block requested.",
        impact=DisruptionImpact(
            newly_required_block_ids=["BLOCK_DOWN_1"],
        ),
    )

    updated = apply_disruption(request, event)

    ids = {
        block.block_id
        for block in updated.block_candidates
    }

    assert "BLOCK_DOWN_1" in ids


def test_emergency_request_fails_for_unknown_block():
    """An unknown emergency block ID should be rejected."""

    request = make_request()

    event = DisruptionEvent(
        event_id="DISRUPTION_004",
        event_type=DisruptionType.EMERGENCY_BLOCK_REQUEST,
        timestamp=datetime(
            2026,
            8,
            29,
            9,
            0,
            tzinfo=timezone.utc,
        ),
        description="Unknown emergency block requested.",
        impact=DisruptionImpact(
            newly_required_block_ids=["DOES_NOT_EXIST"],
        ),
    )

    with pytest.raises(ValueError):
        apply_disruption(request, event)


def test_block_overrun_invalidates_block():
    """A block overrun should invalidate the affected block."""

    request = make_request()

    event = DisruptionEvent(
        event_id="DISRUPTION_005",
        event_type=DisruptionType.BLOCK_OVERRUN,
        affected_block_id="BLOCK_UP_1",
        timestamp=datetime(
            2026,
            8,
            29,
            11,
            0,
            tzinfo=timezone.utc,
        ),
        description="Maintenance block overran its planned window.",
        impact=DisruptionImpact(
            invalidated_block_ids=["BLOCK_UP_1"],
        ),
    )

    updated = apply_disruption(request, event)

    ids = {
        block.block_id
        for block in updated.block_candidates
    }

    assert "BLOCK_UP_1" not in ids


def test_disruption_does_not_mutate_original_request():
    """Applying a disruption must not modify the original request."""

    request = make_request()

    event = DisruptionEvent(
        event_id="DISRUPTION_006",
        event_type=DisruptionType.ASSET_FAILURE,
        affected_asset_id="ASSET_UP",
        timestamp=datetime(
            2026,
            8,
            29,
            9,
            0,
            tzinfo=timezone.utc,
        ),
        description="Asset ASSET_UP becomes unavailable.",
        impact=DisruptionImpact(
            unavailable_asset_ids=["ASSET_UP"],
        ),
    )

    original_count = len(request.block_candidates)

    updated = apply_disruption(request, event)

    assert len(request.block_candidates) == original_count
    assert len(updated.block_candidates) < original_count


def test_full_simulation_returns_recovery_result():
    """A disruption should produce a new optimization result."""

    request = make_request()

    event = DisruptionEvent(
        event_id="DISRUPTION_007",
        event_type=DisruptionType.ASSET_FAILURE,
        affected_asset_id="ASSET_UP",
        timestamp=datetime(
            2026,
            8,
            29,
            9,
            0,
            tzinfo=timezone.utc,
        ),
        description="Asset ASSET_UP becomes unavailable.",
        impact=DisruptionImpact(
            unavailable_asset_ids=["ASSET_UP"],
        ),
    )

    simulation = simulate_disruption(request, event)

    assert simulation.event.event_id == "DISRUPTION_007"
    assert simulation.recovery_result.request_id == request.request_id

    assert all(
        block.block_id != "BLOCK_UP_1"
        for block in simulation.recovery_result.scheduled_blocks
    )