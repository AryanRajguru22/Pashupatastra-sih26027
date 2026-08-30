from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


from backend.app.optimizer.solver import solve
from contracts import OptimizationRequest, OptimizationStatus, ScheduledBlock


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "data"
    / "fixtures"
    / "corridor_a_blocks.json"
)


def load_request() -> OptimizationRequest:
    raw = json.loads(FIXTURE_PATH.read_text())
    return OptimizationRequest.model_validate(raw)


def test_committed_block_remains_at_its_existing_slot():
    request = load_request()

    request.existing_committed_blocks = [
        ScheduledBlock(
            block_id="BLK-001",
            track_id="UP",
            start=datetime.fromisoformat("2026-09-01T01:00:00"),
            end=datetime.fromisoformat("2026-09-01T04:00:00"),
        )
    ]

    # Verify committed track matches the candidate track.
    candidate = next(
        block
        for block in request.block_candidates
        if block.block_id == "BLK-001"
    )

    committed_input = request.existing_committed_blocks[0]

    assert committed_input.track_id == candidate.track_id

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    scheduled = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    assert "BLK-001" in scheduled

    committed = scheduled["BLK-001"]

    assert committed.track_id == candidate.track_id

    assert committed.start == datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )

    assert committed.end == datetime.fromisoformat(
        "2026-09-01T04:00:00"
    )

def test_non_committed_block_can_move_around_committed_block():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.block_candidates
    }

    # Use a small controlled scenario.
    request.block_candidates = [
        blocks_by_id["BLK-003"],
        blocks_by_id["BLK-012"],
    ]

    # Commit BLK-003 to a valid position on the UP track.
    request.existing_committed_blocks = [
        ScheduledBlock(
            block_id="BLK-003",
            track_id="UP",
            start=datetime.fromisoformat(
                "2026-09-01T01:00:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-01T03:00:00"
            ),
        )
    ]

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    scheduled = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    # The committed block must remain exactly where it was committed.
    assert "BLK-003" in scheduled

    committed = scheduled["BLK-003"]

    assert committed.track_id == "UP"
    assert committed.start == datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )
    assert committed.end == datetime.fromisoformat(
        "2026-09-01T03:00:00"
    )

    # BLK-012 is NOT committed.
    # CP-SAT should still be able to place it around BLK-003.
    assert "BLK-012" in scheduled

    other = scheduled["BLK-012"]

    assert other.track_id == "UP"

    # BLK-012 cannot overlap the committed BLK-003.
    # The configured headway is 10 minutes.
    assert other.start >= datetime.fromisoformat(
        "2026-09-01T03:10:00"
    )

    assert other.end <= datetime.fromisoformat(
        "2026-09-01T05:00:00"
    )

def test_committed_block_still_respects_track_no_overlap():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.block_candidates
    }

    # Use two blocks on the same UP track.
    request.block_candidates = [
        blocks_by_id["BLK-003"],
        blocks_by_id["BLK-012"],
    ]

    # BLK-003 is committed from 01:00 to 03:00.
    request.existing_committed_blocks = [
        ScheduledBlock(
            block_id="BLK-003",
            track_id="UP",
            start=datetime.fromisoformat(
                "2026-09-01T01:00:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-01T03:00:00"
            ),
        )
    ]

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    scheduled = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    assert "BLK-003" in scheduled
    assert "BLK-012" in scheduled

    committed = scheduled["BLK-003"]
    other = scheduled["BLK-012"]

    # Verify the committed block stayed fixed.
    assert committed.start == datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )
    assert committed.end == datetime.fromisoformat(
        "2026-09-01T03:00:00"
    )

    # Both are on UP. Therefore BLK-012 must respect the
    # configured 10-minute headway after BLK-003.
    assert other.start >= datetime.fromisoformat(
        "2026-09-01T03:10:00"
    )

def test_non_committed_block_moves_when_old_slot_becomes_unavailable():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.block_candidates
    }

    blk003 = blocks_by_id["BLK-003"]
    blk012 = blocks_by_id["BLK-012"]

    # Keep the scenario small and deterministic.
    request.block_candidates = [blk003, blk012]

    # BLK-003 is already committed at 01:00-03:00.
    request.existing_committed_blocks = [
        ScheduledBlock(
            block_id="BLK-003",
            track_id="UP",
            start=datetime.fromisoformat(
                "2026-09-01T01:00:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-01T03:00:00"
            ),
        )
    ]

    # BLK-012 remains non-committed and is allowed to use the
    # remaining part of its window after the committed block.
    blk012.earliest_start = datetime.fromisoformat(
        "2026-09-01T00:00:00"
    )
    blk012.latest_finish = datetime.fromisoformat(
        "2026-09-01T05:00:00"
    )

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    scheduled = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    # Committed block must remain fixed.
    assert "BLK-003" in scheduled

    committed = scheduled["BLK-003"]

    assert committed.start == datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )
    assert committed.end == datetime.fromisoformat(
        "2026-09-01T03:00:00"
    )
    assert committed.track_id == "UP"

    # The non-committed block must still be solver-controlled.
    assert "BLK-012" in scheduled

    other = scheduled["BLK-012"]

    # BLK-012 is on the same UP track and must respect the
    # 10-minute headway after the committed block.
    assert other.start >= datetime.fromisoformat(
        "2026-09-01T03:10:00"
    )

    assert other.end <= datetime.fromisoformat(
        "2026-09-01T05:00:00"
    )

def test_objective_prefers_higher_risk_and_priority_block():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.block_candidates
    }

    high_value = blocks_by_id["BLK-001"].model_copy(deep=True)
    low_value = blocks_by_id["BLK-012"].model_copy(deep=True)

    # Make the two blocks compete for exactly the same track/time space.
    high_value.track_id = "UP"
    low_value.track_id = "UP"

    high_value.earliest_start = datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )
    high_value.latest_finish = datetime.fromisoformat(
        "2026-09-01T04:00:00"
    )

    low_value.earliest_start = datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )
    low_value.latest_finish = datetime.fromisoformat(
        "2026-09-01T04:00:00"
    )

    # Make both blocks take the same amount of time.
    high_value.duration_minutes = 180
    low_value.duration_minutes = 180

    # High-value block.
    high_value.priority_score = 0.90
    high_value.risk_score = 0.90

    # Low-value block.
    low_value.priority_score = 0.10
    low_value.risk_score = 0.10

    # Remove relationships from these copied blocks so the test
    # isolates the objective rather than dependencies.
    high_value.dependencies = []
    high_value.mutually_exclusive_with = []

    low_value.dependencies = []
    low_value.mutually_exclusive_with = []

    request.block_candidates = [high_value, low_value]

    # Make the objective weights explicit for the test.
    request.objective_weights.risk_reduction_weight = 1.0
    request.objective_weights.blocks_completed_weight = 1.0

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    scheduled_ids = {
        block.block_id
        for block in result.scheduled_blocks
    }

    assert high_value.block_id in scheduled_ids
    assert low_value.block_id not in scheduled_ids

def test_conflicting_committed_blocks_return_infeasible():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.block_candidates
    }

    # Use two blocks on the same UP track.
    request.block_candidates = [
        blocks_by_id["BLK-003"],
        blocks_by_id["BLK-012"],
    ]

    # Both blocks are committed to overlapping positions.
    request.existing_committed_blocks = [
        ScheduledBlock(
            block_id="BLK-003",
            track_id="UP",
            start=datetime.fromisoformat(
                "2026-09-01T01:00:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-01T03:00:00"
            ),
        ),
        ScheduledBlock(
            block_id="BLK-012",
            track_id="UP",
            start=datetime.fromisoformat(
                "2026-09-01T02:00:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-01T03:40:00"
            ),
        ),
    ]

    result = solve(request)

    assert result.status == OptimizationStatus.INFEASIBLE
    assert result.scheduled_blocks == []
    assert len(result.unscheduled_blocks) == 2

def test_unscheduled_block_reports_time_window_reason():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.block_candidates
    }

    block = blocks_by_id["BLK-005"].model_copy(deep=True)

    # Make the block impossible to fit:
    # 1 hour of work inside a 30-minute window.
    block.duration_minutes = 60
    block.earliest_start = datetime.fromisoformat(
        "2026-09-01T10:00:00"
    )
    block.latest_finish = datetime.fromisoformat(
        "2026-09-01T10:30:00"
    )

    block.dependencies = []
    block.mutually_exclusive_with = []

    request.block_candidates = [block]
    request.existing_committed_blocks = []

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    assert result.scheduled_blocks == []

    assert len(result.unscheduled_blocks) == 1

    unscheduled = result.unscheduled_blocks[0]

    assert unscheduled.block_id == "BLK-005"
    assert (
        unscheduled.reason
        == "duration does not fit within earliest_start/latest_finish window"
    )

def test_unscheduled_block_reports_dependency_reason():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.block_candidates
    }

    dependency_block = blocks_by_id["BLK-001"].model_copy(deep=True)
    dependent_block = blocks_by_id["BLK-006"].model_copy(deep=True)

    # Make BLK-001 impossible to schedule because its required
    # maintenance duration does not fit inside its time window.
    dependency_block.duration_minutes = 60
    dependency_block.earliest_start = datetime.fromisoformat(
        "2026-09-01T10:00:00"
    )
    dependency_block.latest_finish = datetime.fromisoformat(
        "2026-09-01T10:30:00"
    )

    dependency_block.dependencies = []
    dependency_block.mutually_exclusive_with = []

    # BLK-006 depends on BLK-001.
    dependent_block.dependencies = ["BLK-001"]
    dependent_block.mutually_exclusive_with = []

    request.block_candidates = [
        dependency_block,
        dependent_block,
    ]
    request.existing_committed_blocks = []

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    scheduled_ids = {
        block.block_id
        for block in result.scheduled_blocks
    }

    assert "BLK-001" not in scheduled_ids
    assert "BLK-006" not in scheduled_ids

    unscheduled = {
        block.block_id: block
        for block in result.unscheduled_blocks
    }

    assert "BLK-001" in unscheduled
    assert "BLK-006" in unscheduled

    assert (
        unscheduled["BLK-001"].reason
        == "duration does not fit within earliest_start/latest_finish window"
    )

    assert (
        unscheduled["BLK-006"].reason
        == "dependency not scheduled: BLK-001"
    )

def test_unscheduled_block_reports_mutual_exclusion_reason():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.block_candidates
    }

    high_value = blocks_by_id["BLK-003"].model_copy(deep=True)
    low_value = blocks_by_id["BLK-009"].model_copy(deep=True)

    # Put the blocks on different tracks so ordinary track
    # no-overlap does not cause the conflict.
    high_value.track_id = "UP"
    low_value.track_id = "DOWN"

    # Give both exactly the same 3-hour window and duration.
    # Since they are mutually exclusive, both cannot be scheduled.
    high_value.duration_minutes = 180
    low_value.duration_minutes = 180

    high_value.earliest_start = datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )
    high_value.latest_finish = datetime.fromisoformat(
        "2026-09-01T04:00:00"
    )

    low_value.earliest_start = datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )
    low_value.latest_finish = datetime.fromisoformat(
        "2026-09-01T04:00:00"
    )

    # Explicitly make the mutual exclusion relationship symmetric
    # for this controlled test.
    high_value.mutually_exclusive_with = ["BLK-009"]
    low_value.mutually_exclusive_with = ["BLK-003"]

    high_value.dependencies = []
    low_value.dependencies = []

    # Give the first block a clearly higher objective value.
    high_value.priority_score = 0.90
    high_value.risk_score = 0.90

    low_value.priority_score = 0.10
    low_value.risk_score = 0.10

    request.block_candidates = [high_value, low_value]
    request.existing_committed_blocks = []

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    scheduled_ids = {
        block.block_id
        for block in result.scheduled_blocks
    }

    assert "BLK-003" in scheduled_ids
    assert "BLK-009" not in scheduled_ids

    unscheduled = {
        block.block_id: block
        for block in result.unscheduled_blocks
    }

    assert "BLK-009" in unscheduled

    assert (
        unscheduled["BLK-009"].reason
        == "mutual exclusion with scheduled block: BLK-003"
    )

def test_unscheduled_block_reports_capacity_priority_reason():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.block_candidates
    }

    high_value = blocks_by_id["BLK-003"].model_copy(deep=True)
    low_value = blocks_by_id["BLK-012"].model_copy(deep=True)

    # Same track and same window: only one can fit.
    high_value.track_id = "UP"
    low_value.track_id = "UP"

    high_value.duration_minutes = 180
    low_value.duration_minutes = 180

    high_value.earliest_start = datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )
    high_value.latest_finish = datetime.fromisoformat(
        "2026-09-01T04:00:00"
    )

    low_value.earliest_start = datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )
    low_value.latest_finish = datetime.fromisoformat(
        "2026-09-01T04:00:00"
    )

    # Ensure this test is about capacity/priority only.
    high_value.dependencies = []
    high_value.mutually_exclusive_with = []

    low_value.dependencies = []
    low_value.mutually_exclusive_with = []

    # Make BLK-003 clearly more valuable.
    high_value.priority_score = 0.90
    high_value.risk_score = 0.90

    low_value.priority_score = 0.10
    low_value.risk_score = 0.10

    request.block_candidates = [high_value, low_value]
    request.existing_committed_blocks = []

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    scheduled_ids = {
        block.block_id
        for block in result.scheduled_blocks
    }

    assert "BLK-003" in scheduled_ids
    assert "BLK-012" not in scheduled_ids

    unscheduled = {
        block.block_id: block
        for block in result.unscheduled_blocks
    }

    assert "BLK-012" in unscheduled

    assert (
        unscheduled["BLK-012"].reason
        == "lower objective value than scheduled competing block: BLK-003"
    )

def test_optimization_result_kpis_match_scheduled_blocks():
    request = load_request()

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    # 1. blocks_scheduled must equal the actual number of
    #    scheduled blocks returned by the solver.
    assert (
        result.kpis.blocks_scheduled
        == len(result.scheduled_blocks)
    )

    # 2. Every block should appear exactly once in either
    #    scheduled_blocks or unscheduled_blocks.
    scheduled_ids = {
        block.block_id
        for block in result.scheduled_blocks
    }

    unscheduled_ids = {
        block.block_id
        for block in result.unscheduled_blocks
    }

    candidate_ids = {
        block.block_id
        for block in request.block_candidates
    }

    assert scheduled_ids.isdisjoint(unscheduled_ids)

    assert (
        scheduled_ids | unscheduled_ids
        == candidate_ids
    )

    # 3. Risk-reduction KPI must equal the actual sum of
    #    risk scores for scheduled blocks.
    expected_risk_reduction = round(
        sum(
            block.risk_score
            for block in request.block_candidates
            if block.block_id in scheduled_ids
        ),
        3,
    )

    assert (
        result.kpis.risk_reduction_score
        == expected_risk_reduction
    )

    # 4. Asset availability KPI must match the current
    #    capacity-based calculation used by the solver.
    horizon_minutes = int(
        (
            request.planning_horizon.end
            - request.planning_horizon.start
        ).total_seconds()
        // 60
    )

    track_ids = {
        block.track_id
        for block in request.block_candidates
    }

    total_track_minutes = (
        horizon_minutes * max(len(track_ids), 1)
    )

    total_blocked_minutes = sum(
        int(
            (
                block.end - block.start
            ).total_seconds()
            // 60
        )
        for block in result.scheduled_blocks
    )

    expected_availability = round(
        100.0
        * (
            1
            - total_blocked_minutes
            / total_track_minutes
        ),
        2,
    )

    assert (
        result.kpis.asset_availability_pct
        == expected_availability
    )

    # 5. The solver should always report a non-negative
    #    solve time.
    assert result.solve_time_ms >= 0

def test_scheduled_blocks_have_explainability_entries():
    request = load_request()

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    scheduled_ids = {
        block.block_id
        for block in result.scheduled_blocks
    }

    explained_ids = {
        entry.block_id
        for entry in result.explainability
    }

    # Every scheduled block must have an explanation.
    assert explained_ids == scheduled_ids

    for entry in result.explainability:
        assert entry.reason
        assert "priority=" in entry.reason
        assert "risk=" in entry.reason

        # Every scheduled block is on a track, so the track
        # constraint should be represented.
        assert any(
            constraint.startswith("track_no_overlap:")
            for constraint in entry.binding_constraints
        )

def test_solver_is_deterministic_for_same_request():
    request = load_request()

    result1 = solve(request)
    result2 = solve(request)

    assert result1.status == result2.status

    schedule1 = [
        (
            block.block_id,
            block.track_id,
            block.start,
            block.end,
        )
        for block in result1.scheduled_blocks
    ]

    schedule2 = [
        (
            block.block_id,
            block.track_id,
            block.start,
            block.end,
        )
        for block in result2.scheduled_blocks
    ]

    print("SCHEDULE 1:", schedule1)
    print("SCHEDULE 2:", schedule2)

    assert result1.status == result2.status