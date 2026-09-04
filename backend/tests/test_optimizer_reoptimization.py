from __future__ import annotations

import copy
import json
from pathlib import Path

from backend.app.optimizer.solver import solve
from contracts import (
    BlockCandidate,
    OptimizationRequest,
    ScheduledBlock,
    SolverStatus,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "data"
    / "fixtures"
    / "corridor_a_blocks.json"
)


def load_request() -> OptimizationRequest:
    raw = json.loads(FIXTURE_PATH.read_text())
    return OptimizationRequest.from_dict(raw)


def make_committed_block(
    candidate: BlockCandidate,
    start_minute: int,
) -> BlockCandidate:
    """Create a committed candidate pinned to an exact slot."""
    committed = copy.deepcopy(candidate)

    committed.is_committed = True
    committed.earliest_start_minute = start_minute
    committed.latest_end_minute = (
        start_minute + committed.duration_minutes
    )

    committed.metadata = dict(committed.metadata)
    committed.metadata["committed_start_minute"] = start_minute
    committed.metadata["committed_end_minute"] = (
        start_minute + committed.duration_minutes
    )

    return committed


def prepare_controlled_pair(request: OptimizationRequest):
    """Return two independent candidates for focused scheduling tests."""
    blocks_by_id = {
        block.block_id: block
        for block in request.candidates
    }

    first = copy.deepcopy(blocks_by_id["BLK-003"])
    second = copy.deepcopy(blocks_by_id["BLK-012"])

    request.candidates = [first, second]
    request.possession_windows = []
    request.min_headway_minutes = 10

    for block in request.candidates:
        block.track_id = "UP"
        block.duration_minutes = 120
        block.earliest_start_minute = 0
        block.latest_end_minute = 320
        block.dependencies = []
        block.mutual_exclusion_group = None
        block.is_committed = False

    request.horizon_minutes = 1440
    request.existing_committed_blocks = []

    return first, second


def assert_good_status(result):
    assert result.status in (
        SolverStatus.OPTIMAL.value,
        SolverStatus.FEASIBLE.value,
    )


def test_committed_block_remains_at_its_existing_slot():
    request = load_request()

    candidate = next(
        block
        for block in request.candidates
        if block.block_id == "BLK-001"
    )

    committed_input = make_committed_block(
        candidate,
        start_minute=60,
    )

    request.candidates = [
        copy.deepcopy(candidate),
    ]

    request.possession_windows = []
    request.existing_committed_blocks = [committed_input]

    result = solve(request)

    assert_good_status(result)

    scheduled = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    assert "BLK-001" in scheduled

    committed = scheduled["BLK-001"]

    assert committed.track_id == candidate.track_id
    assert committed.start_minute == 60
    assert committed.end_minute == (
        60 + candidate.duration_minutes
    )
    assert committed.is_committed is True
    assert committed.status == "COMMITTED"


def test_non_committed_block_can_move_around_committed_block():
    request = load_request()

    first, second = prepare_controlled_pair(request)

    committed = make_committed_block(
        first,
        start_minute=60
    )

    request.existing_committed_blocks = [committed]

    result = solve(request)

    assert_good_status(result)

    scheduled = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    assert "BLK-003" in scheduled
    assert "BLK-012" in scheduled

    committed_result = scheduled["BLK-003"]
    other = scheduled["BLK-012"]

    assert committed_result.start_minute == 60
    assert committed_result.end_minute == (
    committed_result.start_minute
        + first.duration_minutes
    )

    # 120-minute committed block + 10-minute headway.
    assert (
        other.start_minute
        >= committed_result.end_minute + 10
    )
    assert other.end_minute <= 310 or other.start_minute <= 60 - 10


def test_committed_block_still_respects_track_no_overlap():
    request = load_request()

    first, second = prepare_controlled_pair(request)

    committed = make_committed_block(
        first,
        start_minute=60,
    )

    request.existing_committed_blocks = [committed]

    result = solve(request)

    assert_good_status(result)

    scheduled = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    assert "BLK-003" in scheduled
    assert "BLK-012" in scheduled

    committed_result = scheduled["BLK-003"]
    other = scheduled["BLK-012"]

    assert committed_result.start_minute == 60
    assert committed_result.end_minute == (
    committed_result.start_minute
       + first.duration_minutes
    )

    # Same track => the second block must respect headway.
    assert (
        other.start_minute >= committed_result.end_minute + 10
        or other.end_minute <= committed_result.start_minute - 10
    )


def test_non_committed_block_moves_when_old_slot_becomes_unavailable():
    request = load_request()

    first, second = prepare_controlled_pair(request)

    # Give both blocks the same old preferred window.
    first.earliest_start_minute = 0
    first.latest_end_minute = 320

    second.earliest_start_minute = 0
    second.latest_end_minute = 320

    committed = make_committed_block(
        first,
        start_minute=60,
    )

    request.existing_committed_blocks = [committed]

    result = solve(request)

    assert_good_status(result)

    scheduled = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    assert "BLK-003" in scheduled
    assert "BLK-012" in scheduled

    committed_result = scheduled["BLK-003"]
    other = scheduled["BLK-012"]

    assert committed_result.start_minute == 60
    assert committed_result.end_minute == (
    committed_result.start_minute
       + first.duration_minutes
    )

    assert (
        other.start_minute >= 190
        or other.end_minute <= 50
    )


def test_objective_prefers_higher_risk_and_priority_block():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.candidates
    }

    high_value = copy.deepcopy(blocks_by_id["BLK-001"])
    low_value = copy.deepcopy(blocks_by_id["BLK-012"])

    request.possession_windows = []
    request.horizon_minutes = 1440
    request.min_headway_minutes = 10

    high_value.block_id = "HIGH"
    low_value.block_id = "LOW"

    high_value.track_id = "UP"
    low_value.track_id = "UP"

    for block in (high_value, low_value):
        block.duration_minutes = 180
        block.earliest_start_minute = 60
        block.latest_end_minute = 240
        block.dependencies = []
        block.mutual_exclusion_group = None
        block.is_committed = False

    high_value.priority_score = 0.90
    high_value.risk_score = 0.90

    low_value.priority_score = 0.10
    low_value.risk_score = 0.10

    request.candidates = [high_value, low_value]
    request.existing_committed_blocks = []

    result = solve(request)

    assert_good_status(result)

    scheduled_ids = {
        block.block_id
        for block in result.scheduled_blocks
    }

    assert "HIGH" in scheduled_ids
    assert "LOW" not in scheduled_ids


def test_conflicting_committed_blocks_return_infeasible():
    request = load_request()

    first, second = prepare_controlled_pair(request)

    first.block_id = "COMMITTED-A"
    second.block_id = "COMMITTED-B"

    request.candidates = [first, second]

    committed_a = make_committed_block(
        first,
        start_minute=60,
    )

    committed_b = make_committed_block(
        second,
        start_minute=120,
    )

    request.existing_committed_blocks = [
        committed_a,
        committed_b,
    ]

    result = solve(request)

    assert result.status == SolverStatus.INFEASIBLE.value
    assert result.scheduled_blocks == []
    assert len(result.unscheduled_blocks) == 2


def test_unscheduled_block_reports_time_window_reason():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.candidates
    }

    block = copy.deepcopy(blocks_by_id["BLK-005"])

    request.possession_windows = []
    request.existing_committed_blocks = []
    request.horizon_minutes = 1440

    block.block_id = "WINDOW-TEST"
    block.duration_minutes = 60
    block.earliest_start_minute = 600
    block.latest_end_minute = 630
    block.dependencies = []
    block.mutual_exclusion_group = None
    block.is_committed = False

    request.candidates = [block]

    result = solve(request)

    assert_good_status(result)

    assert result.scheduled_blocks == []
    assert len(result.unscheduled_blocks) == 1

    assert (
        result.rejection_reasons["WINDOW-TEST"]
        == (
            "duration does not fit within "
            "earliest_start_minute/latest_end_minute window"
        )
    )


def test_unscheduled_block_reports_dependency_reason():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.candidates
    }

    dependency_block = copy.deepcopy(
        blocks_by_id["BLK-001"]
    )
    dependent_block = copy.deepcopy(
        blocks_by_id["BLK-006"]
    )

    request.possession_windows = []
    request.existing_committed_blocks = []

    dependency_block.block_id = "DEP"
    dependency_block.duration_minutes = 60
    dependency_block.earliest_start_minute = 600
    dependency_block.latest_end_minute = 630
    dependency_block.dependencies = []
    dependency_block.mutual_exclusion_group = None

    dependent_block.block_id = "DEPENDENT"
    dependent_block.duration_minutes = 30
    dependent_block.earliest_start_minute = 600
    dependent_block.latest_end_minute = 700
    dependent_block.dependencies = ["DEP"]
    dependent_block.mutual_exclusion_group = None

    request.candidates = [
        dependency_block,
        dependent_block,
    ]

    result = solve(request)

    assert_good_status(result)

    scheduled_ids = {
        block.block_id
        for block in result.scheduled_blocks
    }

    assert "DEP" not in scheduled_ids
    assert "DEPENDENT" not in scheduled_ids

    assert (
        result.rejection_reasons["DEP"]
        == (
            "duration does not fit within "
            "earliest_start_minute/latest_end_minute window"
        )
    )

    assert (
        result.rejection_reasons["DEPENDENT"]
        == "dependency not scheduled: DEP"
    )


def test_unscheduled_block_reports_mutual_exclusion_reason():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.candidates
    }

    high_value = copy.deepcopy(
        blocks_by_id["BLK-003"]
    )
    low_value = copy.deepcopy(
        blocks_by_id["BLK-009"]
    )

    request.possession_windows = []
    request.existing_committed_blocks = []
    request.horizon_minutes = 1440
    request.min_headway_minutes = 10

    high_value.block_id = "HIGH-MUTEX"
    low_value.block_id = "LOW-MUTEX"

    high_value.track_id = "UP"
    low_value.track_id = "DOWN"

    for block in (high_value, low_value):
        block.duration_minutes = 180
        block.earliest_start_minute = 60
        block.latest_end_minute = 240
        block.dependencies = []
        block.is_committed = False
        block.mutual_exclusion_group = "CREW-A"

    high_value.priority_score = 0.90
    high_value.risk_score = 0.90

    low_value.priority_score = 0.10
    low_value.risk_score = 0.10

    request.candidates = [
        high_value,
        low_value,
    ]

    result = solve(request)

    assert_good_status(result)

    scheduled_ids = {
        block.block_id
        for block in result.scheduled_blocks
    }

    assert "HIGH-MUTEX" in scheduled_ids
    assert "LOW-MUTEX" not in scheduled_ids

    assert (
        result.rejection_reasons["LOW-MUTEX"]
        == (
            "mutual exclusion group conflict with "
            "scheduled block: HIGH-MUTEX"
        )
    )


def test_unscheduled_block_reports_capacity_priority_reason():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.candidates
    }

    high_value = copy.deepcopy(
        blocks_by_id["BLK-003"]
    )
    low_value = copy.deepcopy(
        blocks_by_id["BLK-012"]
    )

    request.possession_windows = []
    request.existing_committed_blocks = []
    request.horizon_minutes = 1440
    request.min_headway_minutes = 10

    high_value.block_id = "HIGH-CAPACITY"
    low_value.block_id = "LOW-CAPACITY"

    for block in (high_value, low_value):
        block.track_id = "UP"
        block.duration_minutes = 180
        block.earliest_start_minute = 60
        block.latest_end_minute = 240
        block.dependencies = []
        block.mutual_exclusion_group = None
        block.is_committed = False

    high_value.priority_score = 0.90
    high_value.risk_score = 0.90

    low_value.priority_score = 0.10
    low_value.risk_score = 0.10

    request.candidates = [
        high_value,
        low_value,
    ]

    result = solve(request)

    assert_good_status(result)

    scheduled_ids = {
        block.block_id
        for block in result.scheduled_blocks
    }

    assert "HIGH-CAPACITY" in scheduled_ids
    assert "LOW-CAPACITY" not in scheduled_ids

    assert (
        result.rejection_reasons["LOW-CAPACITY"]
        == (
            "lower objective value than scheduled "
            "competing block: HIGH-CAPACITY"
        )
    )


def test_optimization_result_totals_match_scheduled_blocks():
    request = load_request()

    result = solve(request)

    assert_good_status(result)

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
        for block in request.candidates
    }

    assert scheduled_ids.isdisjoint(unscheduled_ids)

    assert (
        scheduled_ids | unscheduled_ids
        == candidate_ids
    )

    expected_priority = round(
        sum(
            block.priority_score
            for block in request.candidates
            if block.block_id in scheduled_ids
        ),
        3,
    )

    expected_risk = round(
        sum(
            block.risk_score
            for block in request.candidates
            if block.block_id in scheduled_ids
        ),
        3,
    )

    assert (
        result.total_priority_scheduled
        == expected_priority
    )

    assert (
        result.total_risk_mitigated
        == expected_risk
    )

    assert result.solve_time_seconds >= 0


def test_scheduled_blocks_have_useful_explanation_fields():
    request = load_request()

    result = solve(request)

    assert_good_status(result)

    candidate_by_id = {
        block.block_id: block
        for block in request.candidates
    }

    for scheduled in result.scheduled_blocks:
        candidate = candidate_by_id[scheduled.block_id]

        assert scheduled.track_id == candidate.track_id
        assert scheduled.end_minute > scheduled.start_minute
        assert scheduled.work_type == candidate.work_type
        assert scheduled.priority_score == candidate.priority_score
        assert scheduled.risk_score == candidate.risk_score


def test_solver_is_deterministic_for_same_request():
    request = load_request()

    result1 = solve(request)
    result2 = solve(request)

    assert result1.status == result2.status

    schedule1 = [
        (
            block.block_id,
            block.track_id,
            block.start_minute,
            block.end_minute,
        )
        for block in result1.scheduled_blocks
    ]

    schedule2 = [
        (
            block.block_id,
            block.track_id,
            block.start_minute,
            block.end_minute,
        )
        for block in result2.scheduled_blocks
    ]

    assert schedule1 == schedule2