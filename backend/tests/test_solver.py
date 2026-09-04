"""Correctness tests for the CP-SAT block scheduling engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.optimizer.solver import solve
from contracts import (
    OptimizationRequest,
    SolverStatus,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "data"
    / "fixtures"
    / "corridor_a_blocks.json"
)


@pytest.fixture
def request_() -> OptimizationRequest:
    raw = json.loads(FIXTURE_PATH.read_text())

    return OptimizationRequest.from_dict(raw)


def test_solve_finds_optimal_schedule(request_):
    result = solve(request_)

    assert result.status in (
        SolverStatus.OPTIMAL.value,
        SolverStatus.FEASIBLE.value,
    )

    assert result.scheduled_blocks


def test_no_two_scheduled_blocks_overlap_on_same_track(
    request_,
):
    result = solve(request_)

    by_track: dict[str, list] = {}

    for block in result.scheduled_blocks:
        by_track.setdefault(
            block.track_id,
            [],
        ).append(block)

    for blocks in by_track.values():
        blocks.sort(
            key=lambda block: block.start_minute
        )

        for first, second in zip(
            blocks,
            blocks[1:],
        ):
            assert (
                first.end_minute
                + request_.min_headway_minutes
                <= second.start_minute
            )


def test_dependencies_are_ordered_and_both_present(
    request_,
):
    result = solve(request_)

    by_id = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    by_candidate = {
        block.block_id: block
        for block in request_.candidates
    }

    for block in request_.candidates:
        if block.block_id not in by_id:
            continue

        for dependency_id in block.dependencies:
            assert dependency_id in by_id, (
                f"{block.block_id} is scheduled but its "
                f"dependency {dependency_id} is not"
            )

            assert (
                by_id[dependency_id].end_minute
                <= by_id[block.block_id].start_minute
            )


def test_mutually_exclusive_blocks_never_overlap(
    request_,
):
    result = solve(request_)

    by_id = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    groups: dict[str, list[str]] = {}

    for candidate in request_.candidates:
        if candidate.mutual_exclusion_group:
            groups.setdefault(
                candidate.mutual_exclusion_group,
                [],
            ).append(candidate.block_id)

    for block_ids in groups.values():
        scheduled = [
            by_id[block_id]
            for block_id in block_ids
            if block_id in by_id
        ]

        scheduled.sort(
            key=lambda block: block.start_minute
        )

        for first, second in zip(
            scheduled,
            scheduled[1:],
        ):
            assert (
                first.end_minute <= second.start_minute
                or second.end_minute <= first.start_minute
            )


def test_no_block_scheduled_outside_its_window(
    request_,
):
    result = solve(request_)

    by_candidate = {
        block.block_id: block
        for block in request_.candidates
    }

    for scheduled in result.scheduled_blocks:
        candidate = by_candidate[scheduled.block_id]

        assert (
            scheduled.start_minute
            >= candidate.earliest_start_minute
        )

        assert (
            scheduled.end_minute
            <= candidate.latest_end_minute
        )


def test_result_partitions_all_candidates(request_):
    result = solve(request_)

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
        for block in request_.candidates
    }

    assert scheduled_ids.isdisjoint(
        unscheduled_ids
    )

    assert (
        scheduled_ids | unscheduled_ids
        == candidate_ids
    )


def test_result_totals_are_consistent(request_):
    result = solve(request_)

    scheduled_ids = {
        block.block_id
        for block in result.scheduled_blocks
    }

    expected_priority = round(
        sum(
            block.priority_score
            for block in request_.candidates
            if block.block_id in scheduled_ids
        ),
        3,
    )

    expected_risk = round(
        sum(
            block.risk_score
            for block in request_.candidates
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


def test_scheduled_blocks_use_valid_status(request_):
    result = solve(request_)

    valid_statuses = {
        "SCHEDULED",
        "COMMITTED",
    }

    for block in result.scheduled_blocks:
        assert block.status in valid_statuses


def test_solver_does_not_modify_request(request_):
    before = request_.to_dict()

    solve(request_)

    after = request_.to_dict()

    assert after == before