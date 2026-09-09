"""Possession protection must never be lifted by an identifier mismatch.

The solver matches a block to its possession windows by exact track_id
string. Before this suite existed, a block whose track_id matched no
window was scheduled with NO possession restriction at all - so a typo,
a section-qualified id, or simply a request that covered only some of
its tracks silently removed train protection.

The safety rule these tests pin down:

    A request that declares ANY possession window is under possession
    control. Under possession control, work on a track with no window
    is REFUSED, never scheduled unprotected.

A request that declares no possession windows at all is not modelling
possessions, and keeps the long-standing unrestricted behavior that
several fixtures and re-optimization tests rely on.
"""

from __future__ import annotations

import json

import pytest

from backend.app.optimizer.solver import (
    solve,
    uncovered_possession_tracks,
)
from backend.app.simulation.simulator import simulate_disruption
from contracts import (
    BlockCandidate,
    DisruptionEvent,
    DisruptionType,
    OptimizationRequest,
    PossessionWindow,
)


HORIZON = 1440
SAFETY_REFUSAL = "refused for safety"


def make_window(
    track_id: str,
    start_minute: int,
    end_minute: int,
) -> PossessionWindow:
    return PossessionWindow(
        window_id=f"POS-{track_id}-{start_minute}",
        track_id=track_id,
        start_minute=start_minute,
        end_minute=end_minute,
    )


def make_request(
    candidate_track_id: str,
    windows: list[PossessionWindow],
    duration_minutes: int = 240,
) -> OptimizationRequest:
    return OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=HORIZON,
        tracks=[candidate_track_id],
        candidates=[
            BlockCandidate(
                block_id="B1",
                asset_id="A1",
                track_id=candidate_track_id,
                work_type="TRACK_RENEWAL",
                duration_minutes=duration_minutes,
                earliest_start_minute=0,
                latest_end_minute=HORIZON,
                priority_score=1.0,
                risk_score=1.0,
            )
        ],
        possession_windows=windows,
    )


def scheduled_block(result):
    return next(
        (
            block
            for block in result.scheduled_blocks
            if block.block_id == "B1"
        ),
        None,
    )


# ----------------------------------------------------------------------
# 1. Valid matching possession windows still work.
# ----------------------------------------------------------------------


def test_matching_window_large_enough_still_schedules():
    """The normal, correct case must keep working unchanged."""

    request = make_request(
        "UP-1",
        [make_window("UP-1", 0, 300)],
    )

    result = solve(request)
    block = scheduled_block(result)

    assert block is not None
    assert block.start_minute >= 0
    assert block.end_minute <= 300

    assert uncovered_possession_tracks(request) == {}


def test_matching_window_too_small_is_a_capacity_refusal_not_a_safety_one():
    """A covered track that simply has no room is a normal refusal.

    This must NOT be reported as a safety refusal - conflating the two
    would make the safety signal meaningless.
    """

    request = make_request(
        "UP-1",
        [make_window("UP-1", 0, 120)],
    )

    result = solve(request)

    assert scheduled_block(result) is None
    assert SAFETY_REFUSAL not in result.rejection_reasons["B1"]

    assert uncovered_possession_tracks(request) == {}


# ----------------------------------------------------------------------
# 2. Section-qualified identifiers still work where they are consistent.
# ----------------------------------------------------------------------


def test_section_qualified_ids_work_when_block_and_window_agree():
    """Section-qualified track ids are supported, as long as they match."""

    request = make_request(
        "UP-1:0-7",
        [make_window("UP-1:0-7", 0, 300)],
    )

    result = solve(request)
    block = scheduled_block(result)

    assert block is not None
    assert block.track_id == "UP-1:0-7"
    assert block.end_minute <= 300

    assert uncovered_possession_tracks(request) == {}


def test_two_sections_of_one_track_are_scheduled_independently():
    """Distinct sections are distinct possession scopes."""

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=HORIZON,
        tracks=["UP-1:0-7", "UP-1:7-32"],
        candidates=[
            BlockCandidate(
                block_id="B-SEC-1",
                asset_id="A1",
                track_id="UP-1:0-7",
                work_type="ROUTINE_INSPECTION",
                duration_minutes=60,
                earliest_start_minute=0,
                latest_end_minute=HORIZON,
                priority_score=1.0,
                risk_score=1.0,
            ),
            BlockCandidate(
                block_id="B-SEC-2",
                asset_id="A2",
                track_id="UP-1:7-32",
                work_type="ROUTINE_INSPECTION",
                duration_minutes=60,
                earliest_start_minute=0,
                latest_end_minute=HORIZON,
                priority_score=1.0,
                risk_score=1.0,
            ),
        ],
        possession_windows=[
            make_window("UP-1:0-7", 0, 120),
            make_window("UP-1:7-32", 0, 120),
        ],
    )

    result = solve(request)
    scheduled = {block.block_id for block in result.scheduled_blocks}

    assert scheduled == {"B-SEC-1", "B-SEC-2"}


# ----------------------------------------------------------------------
# 3. Mismatched / unknown identifiers cannot silently remove protection.
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_name, candidate_track_id, window_track_id",
    [
        ("section_qualified_window_plain_block", "UP-1", "UP-1:0-7"),
        ("plain_window_section_qualified_block", "UP-1:0-7", "UP-1"),
        ("typo_in_window_track_id", "UP-1", "UP1"),
        ("windows_cover_a_different_track", "UP-1", "DOWN-1"),
        ("case_mismatch", "UP-1", "up-1"),
        ("whitespace_mismatch", "UP-1", "UP-1 "),
    ],
)
def test_identifier_mismatch_refuses_instead_of_scheduling_unprotected(
    case_name: str,
    candidate_track_id: str,
    window_track_id: str,
):
    """The core safety regression.

    Every one of these previously scheduled the block for its full
    duration with no possession restriction whatsoever.
    """

    request = make_request(
        candidate_track_id,
        [make_window(window_track_id, 0, 120)],
    )

    result = solve(request)

    assert scheduled_block(result) is None, (
        f"{case_name}: block was scheduled despite no possession "
        f"window covering track '{candidate_track_id}'"
    )

    reason = result.rejection_reasons["B1"]

    assert SAFETY_REFUSAL in reason, (
        f"{case_name}: refusal must be reported as a safety refusal, "
        f"got: {reason}"
    )
    assert candidate_track_id in reason

    assert uncovered_possession_tracks(request) == {
        candidate_track_id: ["B1"]
    }


def test_partial_track_coverage_protects_the_uncovered_track():
    """A covered track proceeds; an uncovered one is refused.

    This is the realistic shape of the bug: a multi-track request where
    windows were only produced for some tracks.
    """

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=HORIZON,
        tracks=["UP-1", "DOWN-1"],
        candidates=[
            BlockCandidate(
                block_id="B-COVERED",
                asset_id="A1",
                track_id="UP-1",
                work_type="ROUTINE_INSPECTION",
                duration_minutes=60,
                earliest_start_minute=0,
                latest_end_minute=HORIZON,
                priority_score=1.0,
                risk_score=1.0,
            ),
            BlockCandidate(
                block_id="B-UNCOVERED",
                asset_id="A2",
                track_id="DOWN-1",
                work_type="ROUTINE_INSPECTION",
                duration_minutes=60,
                earliest_start_minute=0,
                latest_end_minute=HORIZON,
                priority_score=1.0,
                risk_score=1.0,
            ),
        ],
        possession_windows=[make_window("UP-1", 0, 120)],
    )

    result = solve(request)
    scheduled = {block.block_id for block in result.scheduled_blocks}

    assert "B-COVERED" in scheduled
    assert "B-UNCOVERED" not in scheduled

    assert SAFETY_REFUSAL in result.rejection_reasons["B-UNCOVERED"]

    assert uncovered_possession_tracks(request) == {
        "DOWN-1": ["B-UNCOVERED"]
    }


# ----------------------------------------------------------------------
# 4. Existing normal optimization remains functional.
# ----------------------------------------------------------------------


def test_request_without_possession_windows_stays_unrestricted():
    """No windows at all means possessions are not being modelled.

    Several re-optimization tests and callers rely on this; failing
    closed here would be wrong, because nothing was ever claimed to be
    protected.
    """

    request = make_request("UP-1", [])

    result = solve(request)

    assert scheduled_block(result) is not None
    assert uncovered_possession_tracks(request) == {}


@pytest.mark.parametrize(
    "fixture_name",
    [
        "corridor_a_blocks.json",
        "corridor_b_dense.json",
        "corridor_c_disrupted.json",
    ],
)
def test_shipped_fixtures_are_fully_covered_and_stay_inside_windows(
    fixture_name: str,
):
    """Every shipped scenario must be fully covered, and every block it
    schedules must lie completely inside a window on its own track.

    This is the post-solve safety assertion applied to real scenarios,
    generalized from the check that test_real_dataset_optimizer.py
    already performed inline for the NDLS-AGC dataset.
    """

    path = f"backend/app/data/fixtures/{fixture_name}"

    with open(path, encoding="utf-8") as handle:
        request = OptimizationRequest.from_dict(json.load(handle))

    assert uncovered_possession_tracks(request) == {}, (
        f"{fixture_name} has candidate tracks with no possession window"
    )

    result = solve(request)

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.scheduled_blocks

    for block in result.scheduled_blocks:
        containing = [
            window
            for window in request.possession_windows
            if window.track_id == block.track_id
            and window.start_minute <= block.start_minute
            and window.end_minute >= block.end_minute
        ]

        assert containing, (
            f"{fixture_name}: {block.block_id} scheduled "
            f"{block.start_minute}-{block.end_minute} on "
            f"{block.track_id}, outside every possession window"
        )


# ----------------------------------------------------------------------
# 5. Committed blocks must not be pinned onto an uncovered track.
# ----------------------------------------------------------------------
#
# The pinning that keeps a committed block at its prior placement posts
# presence == 1. The possession rule posts presence == 0 for a block on
# an uncovered track. If both applied to the same block the model would
# be unsatisfiable, and the whole request - including every unrelated
# block on properly covered tracks - would come back INFEASIBLE with the
# per-block safety reason replaced by a bare "no feasible solution".
#
# So a committed block on an uncovered track is NOT re-pinned: its prior
# placement was made against a possession that no longer exists, and it
# is refused individually like any other uncovered block.


def test_committed_block_on_uncovered_track_is_refused_not_infeasible():
    """Minimal form of the contradiction."""

    committed = BlockCandidate(
        block_id="B-COMMITTED",
        asset_id="A1",
        track_id="UP-1",
        work_type="TRACK_RENEWAL",
        duration_minutes=60,
        earliest_start_minute=0,
        latest_end_minute=HORIZON,
        priority_score=1.0,
        risk_score=1.0,
        is_committed=True,
        metadata={
            "committed_start_minute": 100,
            "committed_end_minute": 160,
        },
    )

    other = BlockCandidate(
        block_id="B-COVERED",
        asset_id="A2",
        track_id="DOWN-1",
        work_type="ROUTINE_INSPECTION",
        duration_minutes=60,
        earliest_start_minute=0,
        latest_end_minute=HORIZON,
        priority_score=1.0,
        risk_score=1.0,
    )

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=HORIZON,
        tracks=["UP-1", "DOWN-1"],
        candidates=[committed, other],
        possession_windows=[make_window("DOWN-1", 0, 300)],
        existing_committed_blocks=[committed],
    )

    result = solve(request)

    # The request must stay solvable.
    assert result.status in {"OPTIMAL", "FEASIBLE"}

    scheduled = {block.block_id for block in result.scheduled_blocks}

    # Fail-closed still holds for the committed block.
    assert "B-COMMITTED" not in scheduled
    assert SAFETY_REFUSAL in result.rejection_reasons["B-COMMITTED"]

    # And the unrelated covered block is not collateral damage.
    assert "B-COVERED" in scheduled


def test_curtailing_a_committed_track_keeps_the_rest_of_the_plan():
    """The production-reachable path, on a shipped fixture.

    corridor_c_disrupted carries committed blocks on both UP-1 and
    DOWN-1. Curtailing every UP-1 possession through the real recovery
    pipeline previously produced INFEASIBLE / 0 scheduled; before the
    fail-closed change it produced 7 blocks scheduled on the very track
    whose possession had just been withdrawn.
    """

    with open(
        "backend/app/data/fixtures/corridor_c_disrupted.json",
        encoding="utf-8",
    ) as handle:
        request = OptimizationRequest.from_dict(json.load(handle))

    committed_tracks = {
        block.track_id for block in request.existing_committed_blocks
    }

    assert {"UP-1", "DOWN-1"} <= committed_tracks, (
        "fixture no longer has committed blocks on both tracks; this "
        "test needs that shape to exercise the pinning interaction"
    )

    recovery = simulate_disruption(
        request,
        DisruptionEvent(
            disruption_id="DIS-CURTAIL-UP1",
            disruption_type=(
                DisruptionType.POSSESSION_CURTAILMENT.value
            ),
            corridor_id=request.corridor_id,
            track_id="UP-1",
            start_minute=0,
            end_minute=HORIZON,
            description="All UP-1 possessions withdrawn.",
        ),
    ).recovery_result

    # 1. The plan does not collapse.
    assert recovery.status in {"OPTIMAL", "FEASIBLE"}

    # 2. Nothing is scheduled on the now-unprotected track.
    on_uncovered = [
        block
        for block in recovery.scheduled_blocks
        if block.track_id == "UP-1"
    ]
    assert not on_uncovered, (
        f"{len(on_uncovered)} blocks scheduled on UP-1 after every "
        "UP-1 possession was withdrawn"
    )

    # 3. Work on the still-covered track survives.
    assert recovery.scheduled_blocks
    assert {
        block.track_id for block in recovery.scheduled_blocks
    } == {"DOWN-1"}

    # 4. Refusals carry the safety reason, not "no feasible solution".
    refused_up1 = [
        block.block_id
        for block in recovery.unscheduled_blocks
        if block.track_id == "UP-1"
    ]
    assert refused_up1

    for block_id in refused_up1:
        assert SAFETY_REFUSAL in recovery.rejection_reasons[block_id]


def test_committed_block_on_a_covered_track_is_still_pinned():
    """Option A must not weaken normal committed-block stability."""

    with open(
        "backend/app/data/fixtures/corridor_c_disrupted.json",
        encoding="utf-8",
    ) as handle:
        request = OptimizationRequest.from_dict(json.load(handle))

    result = solve(request)

    committed_by_id = {
        block.block_id: block
        for block in request.existing_committed_blocks
    }

    pinned = 0

    for block in result.scheduled_blocks:
        committed = committed_by_id.get(block.block_id)

        if committed is None:
            continue

        expected_start = int(
            committed.metadata.get(
                "committed_start_minute",
                committed.earliest_start_minute,
            )
        )
        expected_end = int(
            committed.metadata.get(
                "committed_end_minute",
                expected_start + committed.duration_minutes,
            )
        )

        assert block.start_minute == expected_start
        assert block.end_minute == expected_end
        assert block.is_committed

        pinned += 1

    assert pinned == len(committed_by_id)
