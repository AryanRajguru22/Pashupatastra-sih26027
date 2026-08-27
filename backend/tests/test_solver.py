"""Correctness tests for the CP-SAT block scheduling engine.

The optimizer is the highest-risk subsystem in the project, so these
tests assert the hard constraints directly rather than just checking
that a solve "runs" - a schedule that violates safety constraints is
a bug regardless of what the objective value says.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.optimizer.solver import solve
from contracts import OptimizationRequest, OptimizationStatus

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
    return OptimizationRequest.model_validate(raw)


def test_solve_finds_optimal_schedule(request_):
    result = solve(request_)
    assert result.status in (OptimizationStatus.OPTIMAL, OptimizationStatus.FEASIBLE)
    assert result.scheduled_blocks


def test_no_two_scheduled_blocks_overlap_on_same_track(request_):
    result = solve(request_)

    by_track: dict[str, list] = {}
    for sb in result.scheduled_blocks:
        by_track.setdefault(sb.track_id, []).append(sb)

    for blocks in by_track.values():
        blocks.sort(key=lambda b: b.start)
        for a, b in zip(blocks, blocks[1:]):
            assert a.end <= b.start


def test_dependencies_are_ordered_and_both_present(request_):
    result = solve(request_)
    by_id = {sb.block_id: sb for sb in result.scheduled_blocks}
    by_candidate = {b.block_id: b for b in request_.block_candidates}

    for block in request_.block_candidates:
        if block.block_id not in by_id:
            continue
        for dep_id in block.dependencies:
            assert dep_id in by_id, (
                f"{block.block_id} is scheduled but its dependency {dep_id} is not"
            )
            assert by_id[dep_id].end <= by_id[block.block_id].start


def test_mutually_exclusive_blocks_never_overlap(request_):
    result = solve(request_)
    by_id = {sb.block_id: sb for sb in result.scheduled_blocks}

    for block in request_.block_candidates:
        if block.block_id not in by_id:
            continue
        for other_id in block.mutually_exclusive_with:
            if other_id not in by_id:
                continue
            a, b = by_id[block.block_id], by_id[other_id]
            assert a.end <= b.start or b.end <= a.start


def test_no_block_scheduled_outside_its_window(request_):
    result = solve(request_)
    by_candidate = {b.block_id: b for b in request_.block_candidates}

    for sb in result.scheduled_blocks:
        candidate = by_candidate[sb.block_id]
        assert sb.start >= candidate.earliest_start
        assert sb.end <= candidate.latest_finish
