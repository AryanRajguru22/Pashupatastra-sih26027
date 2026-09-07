from backend.app.data.train_adapter import generate_possession_windows_from_trains
from backend.app.optimizer.solver import solve
from contracts.schemas import BlockCandidate, OptimizationRequest


def test_train_derived_possession_windows_constrain_optimizer():
    trains = [
        {
            "train_no": "12001",
            "track_id": "UP-1",
            "scheduled_start_minute": 100,
            "scheduled_end_minute": 160,
        },
        {
            "train_no": "12002",
            "track_id": "UP-1",
            "scheduled_start_minute": 300,
            "scheduled_end_minute": 340,
        },
    ]

    windows = generate_possession_windows_from_trains(
        trains,
        horizon_minutes=480,
        safety_buffer_minutes=10,
        minimum_window_minutes=30,
    )

    candidates = [
        BlockCandidate(
            block_id="MAINT-001",
            asset_id="AST-001",
            track_id="UP-1",
            work_type="TRACK_RENEWAL",
            duration_minutes=60,
            earliest_start_minute=180,
            latest_end_minute=280,
            priority_score=1.0,
            risk_score=1.0,
        ),
        BlockCandidate(
            block_id="MAINT-002",
            asset_id="AST-002",
            track_id="UP-1",
            work_type="TRACK_RENEWAL",
            duration_minutes=60,
            earliest_start_minute=100,
            latest_end_minute=160,
            priority_score=0.5,
            risk_score=0.5,
        ),
    ]

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=480,
        tracks=["UP-1"],
        candidates=candidates,
        possession_windows=windows,
        min_headway_minutes=15,
    )

    result = solve(request)

    scheduled_ids = {block.block_id for block in result.scheduled_blocks}

    # MAINT-001 can fit inside the 170-290 train-free gap.
    assert "MAINT-001" in scheduled_ids

    # MAINT-002 conflicts with the first train's buffered occupation.
    assert "MAINT-002" not in scheduled_ids

    scheduled = next(
        block for block in result.scheduled_blocks
        if block.block_id == "MAINT-001"
    )

    assert scheduled.start_minute >= 170
    assert scheduled.end_minute <= 290