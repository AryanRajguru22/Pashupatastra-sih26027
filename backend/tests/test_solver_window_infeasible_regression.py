"""Regression tests for the window-infeasible skip in solver.solve().

Sprint 3 Slice 5 Step 2 exposed a solver defect: a block whose duration
cannot fit inside its own [earliest_start_minute, latest_end_minute)
window after horizon clamping (contracts.BlockCandidate + solver.
_clamp_window) is marked window_infeasible, gets presence forced to 0
and a placeholder start/end fixed at its own earliest_start_minute -
which can be arbitrarily far past horizon_minutes. Before the fix, the
no-overlap resource loop still posted an UNCONDITIONAL
`buffered_end == start + buffered_size` equality for that placeholder,
whose value can exceed the buffered_end IntVar's declared upper bound
(horizon_minutes + buffered_size). That made the whole CP-SAT model
INFEASIBLE - taking every unrelated block on every track down with it -
even though the offending block's presence was already fixed to 0 and
an AddNoOverlap over OPTIONAL intervals would never have needed it.

The fix (backend/app/optimizer/solver.py, track no-overlap loop) skips
window-infeasible blocks entirely when building buffered intervals,
exactly as the possession-window loop already did. These tests pin:

  - a window-infeasible block never turns the model INFEASIBLE, alone
    or combined with other blocks, regardless of how far past the
    horizon its earliest_start_minute lands
  - it is reported through rejection_reasons with the existing message
  - everything else the solver guarantees (headway, committed pinning,
    possession fail-closed, dependencies, mutual exclusion, real
    infeasibility, determinism) is unaffected
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.optimizer.solver import solve
from contracts import (
    BlockCandidate,
    OptimizationRequest,
    PossessionWindow,
    SolverStatus,
)


FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "data"
    / "fixtures"
)

FEASIBLE_STATUSES = (
    SolverStatus.OPTIMAL.value,
    SolverStatus.FEASIBLE.value,
)


def block(
    block_id: str,
    *,
    track_id: str = "UP-1",
    section_id: str | None = None,
    duration_minutes: int = 60,
    earliest_start_minute: int = 0,
    latest_end_minute: int = 1440,
    priority_score: float = 0.5,
    risk_score: float = 0.5,
    dependencies: list[str] | None = None,
    mutual_exclusion_group: str | None = None,
    is_committed: bool = False,
    metadata: dict | None = None,
) -> BlockCandidate:
    return BlockCandidate(
        block_id=block_id,
        asset_id=f"ASSET-{block_id}",
        track_id=track_id,
        work_type="BALLAST_TAMPING",
        duration_minutes=duration_minutes,
        section_id=section_id,
        earliest_start_minute=earliest_start_minute,
        latest_end_minute=latest_end_minute,
        priority_score=priority_score,
        risk_score=risk_score,
        dependencies=list(dependencies or []),
        mutual_exclusion_group=mutual_exclusion_group,
        is_committed=is_committed,
        metadata=dict(metadata or {}),
    )


def make_request(
    candidates: list[BlockCandidate],
    *,
    horizon_minutes: int = 2880,
    min_headway_minutes: int = 15,
    possession_windows: list[PossessionWindow] | None = None,
    existing_committed_blocks: list[BlockCandidate] | None = None,
    tracks: list[str] | None = None,
) -> OptimizationRequest:
    return OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=horizon_minutes,
        tracks=tracks or ["UP-1", "DOWN-1"],
        candidates=candidates,
        possession_windows=possession_windows or [],
        existing_committed_blocks=existing_committed_blocks or [],
        min_headway_minutes=min_headway_minutes,
    )


def load_fixture(name: str) -> OptimizationRequest:
    raw = json.loads((FIXTURES_DIR / name).read_text())
    return OptimizationRequest.from_dict(raw)


def schedule_signature(result) -> tuple:
    return tuple(
        sorted(
            (block.block_id, block.track_id, block.start_minute, block.end_minute)
            for block in result.scheduled_blocks
        )
    )


# ----------------------------------------------------------------------
# earliest_start_minute at and beyond the horizon
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "earliest_start_minute",
    [
        2880,  # earliest == horizon
        2881,  # earliest == horizon + 1
        2911,  # matches horizon_minutes + 31 used by the Step 2 not-before test
        10**9,  # very large earliest
    ],
    ids=["earliest-eq-horizon", "earliest-eq-horizon-plus-1", "earliest-2911", "earliest-very-large"],
)
def test_window_infeasible_block_alone_stays_feasible(earliest_start_minute):
    candidate = block(
        "B1",
        earliest_start_minute=earliest_start_minute,
        latest_end_minute=earliest_start_minute + 1000,
        duration_minutes=60,
    )

    result = solve(make_request([candidate], horizon_minutes=2880))

    assert result.status in FEASIBLE_STATUSES
    assert result.scheduled_blocks == []
    assert [b.block_id for b in result.unscheduled_blocks] == ["B1"]
    assert (
        "duration does not fit within"
        in result.rejection_reasons["B1"]
    )


def test_multiple_impossible_blocks_together_stay_feasible():
    b1 = block(
        "B1",
        earliest_start_minute=2900,
        latest_end_minute=5000,
        duration_minutes=60,
    )
    b2 = block(
        "B2",
        earliest_start_minute=3000,
        latest_end_minute=6000,
        duration_minutes=90,
    )

    result = solve(make_request([b1, b2], horizon_minutes=2880))

    assert result.status in FEASIBLE_STATUSES
    assert result.scheduled_blocks == []
    assert {b.block_id for b in result.unscheduled_blocks} == {"B1", "B2"}

    for block_id in ("B1", "B2"):
        assert (
            "duration does not fit within"
            in result.rejection_reasons[block_id]
        )


# ----------------------------------------------------------------------
# Interaction with committed blocks and genuine infeasibility
# ----------------------------------------------------------------------


def test_committed_block_still_scheduled_alongside_impossible_mutable_block():
    committed = block(
        "C1",
        earliest_start_minute=100,
        duration_minutes=60,
        is_committed=True,
    )
    impossible = block(
        "B1",
        earliest_start_minute=2900,
        latest_end_minute=5000,
        duration_minutes=60,
    )

    request = make_request(
        [committed, impossible],
        horizon_minutes=2880,
        existing_committed_blocks=[committed],
    )

    result = solve(request)

    assert result.status in FEASIBLE_STATUSES

    scheduled = {b.block_id: b for b in result.scheduled_blocks}
    assert "C1" in scheduled
    assert scheduled["C1"].start_minute == 100
    assert scheduled["C1"].end_minute == 160
    assert scheduled["C1"].status == "COMMITTED"

    assert "B1" not in scheduled
    assert (
        "duration does not fit within"
        in result.rejection_reasons["B1"]
    )


def test_genuine_committed_overlap_still_reports_infeasible():
    """The fix must not mask real infeasibility - only window-infeasible
    placeholders are skipped in the no-overlap loop."""

    c1 = block("C1", earliest_start_minute=100, duration_minutes=60, is_committed=True)
    c2 = block("C2", earliest_start_minute=120, duration_minutes=60, is_committed=True)

    request = make_request(
        [c1, c2],
        horizon_minutes=2880,
        min_headway_minutes=0,
        existing_committed_blocks=[c1, c2],
    )

    result = solve(request)

    assert result.status == SolverStatus.INFEASIBLE.value
    assert result.scheduled_blocks == []


# ----------------------------------------------------------------------
# Headway, possession fail-closed, dependencies unaffected
# ----------------------------------------------------------------------


def test_impossible_block_does_not_disturb_feasible_headway_placement():
    headway = 15

    a = block("A", earliest_start_minute=0, latest_end_minute=200, duration_minutes=60)
    b = block("B", earliest_start_minute=0, latest_end_minute=200, duration_minutes=60)
    impossible = block(
        "X",
        earliest_start_minute=2900,
        latest_end_minute=5000,
        duration_minutes=30,
    )

    request = make_request(
        [a, b, impossible],
        horizon_minutes=2880,
        min_headway_minutes=headway,
    )

    result = solve(request)

    assert result.status in FEASIBLE_STATUSES

    scheduled = {b.block_id: b for b in result.scheduled_blocks}
    assert {"A", "B"} <= scheduled.keys()
    assert "X" not in scheduled

    first, second = sorted(
        (scheduled["A"], scheduled["B"]),
        key=lambda s: s.start_minute,
    )
    assert first.end_minute + headway <= second.start_minute


def test_possession_fail_closed_remains_correct_alongside_impossible_block():
    covered = block("A", earliest_start_minute=0, latest_end_minute=200, duration_minutes=60, track_id="UP-1")
    uncovered = block("B", earliest_start_minute=0, latest_end_minute=200, duration_minutes=60, track_id="DOWN-1")
    impossible = block(
        "X",
        earliest_start_minute=2900,
        latest_end_minute=5000,
        duration_minutes=30,
        track_id="UP-1",
    )

    window = PossessionWindow(
        window_id="W1",
        track_id="UP-1",
        start_minute=0,
        end_minute=300,
    )

    request = make_request(
        [covered, uncovered, impossible],
        horizon_minutes=2880,
        possession_windows=[window],
    )

    result = solve(request)

    assert result.status in FEASIBLE_STATUSES

    scheduled_ids = {b.block_id for b in result.scheduled_blocks}
    assert "A" in scheduled_ids
    assert "B" not in scheduled_ids
    assert "refused for safety" in result.rejection_reasons["B"]

    assert "X" not in scheduled_ids
    assert (
        "duration does not fit within"
        in result.rejection_reasons["X"]
    )


def test_dependency_on_window_infeasible_block_is_not_scheduled():
    dep = block(
        "DEP",
        earliest_start_minute=2900,
        latest_end_minute=5000,
        duration_minutes=30,
    )
    dependent = block(
        "MAIN",
        earliest_start_minute=0,
        latest_end_minute=500,
        duration_minutes=30,
        dependencies=["DEP"],
    )

    result = solve(
        make_request([dep, dependent], horizon_minutes=2880)
    )

    assert result.status in FEASIBLE_STATUSES

    scheduled_ids = {b.block_id for b in result.scheduled_blocks}
    assert "DEP" not in scheduled_ids
    assert "MAIN" not in scheduled_ids

    assert (
        "duration does not fit within"
        in result.rejection_reasons["DEP"]
    )
    assert "dependency not scheduled: DEP" == result.rejection_reasons["MAIN"]


def test_normal_dependency_ordering_unaffected_by_unrelated_impossible_block():
    dep = block("DEP", earliest_start_minute=0, latest_end_minute=200, duration_minutes=30)
    dependent = block(
        "MAIN",
        earliest_start_minute=0,
        latest_end_minute=300,
        duration_minutes=30,
        dependencies=["DEP"],
    )
    impossible = block(
        "X",
        earliest_start_minute=2900,
        latest_end_minute=5000,
        duration_minutes=30,
        track_id="DOWN-1",
    )

    result = solve(
        make_request([dep, dependent, impossible], horizon_minutes=2880)
    )

    assert result.status in FEASIBLE_STATUSES

    scheduled = {b.block_id: b for b in result.scheduled_blocks}
    assert "DEP" in scheduled
    assert "MAIN" in scheduled
    assert scheduled["DEP"].end_minute <= scheduled["MAIN"].start_minute
    assert "X" not in scheduled


# ----------------------------------------------------------------------
# Normal feasible requests and determinism
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name",
    ["corridor_a_blocks.json", "corridor_b_dense.json", "corridor_c_disrupted.json"],
)
def test_normal_feasible_fixtures_still_solve_successfully(fixture_name):
    request = load_fixture(fixture_name)

    result = solve(request)

    assert result.status in FEASIBLE_STATUSES
    assert result.scheduled_blocks

    scheduled_ids = {b.block_id for b in result.scheduled_blocks}
    unscheduled_ids = {b.block_id for b in result.unscheduled_blocks}
    candidate_ids = {b.block_id for b in request.candidates}

    assert scheduled_ids.isdisjoint(unscheduled_ids)
    assert scheduled_ids | unscheduled_ids == candidate_ids


def test_repeated_solves_with_window_infeasible_block_are_deterministic():
    feasible = block("A", earliest_start_minute=0, latest_end_minute=200, duration_minutes=60)
    impossible = block(
        "X",
        earliest_start_minute=2900,
        latest_end_minute=5000,
        duration_minutes=30,
    )

    request = make_request([feasible, impossible], horizon_minutes=2880)

    signatures = set()
    reasons = set()
    statuses = set()

    for _ in range(8):
        result = solve(request)
        signatures.add(schedule_signature(result))
        reasons.add(tuple(sorted(result.rejection_reasons.items())))
        statuses.add(result.status)

    assert len(signatures) == 1
    assert len(reasons) == 1
    assert len(statuses) == 1
    assert statuses == {SolverStatus.OPTIMAL.value} or statuses <= set(FEASIBLE_STATUSES)
