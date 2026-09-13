"""Sprint 3 Step 4: absolute monotonic horizon + midnight-crossing safety.

Before this step, every solver-facing time value was documented as
"minutes from midnight" with an implicit 0..1440 single calendar day.
An overnight possession window such as 23:30 -> 04:30 next day has no
valid minutes-from-midnight representation (its end is numerically
SMALLER than its start), so any caller trying to model one was forced
into an ambiguous choice: wrap end_minute back to 270 (which collides
with, and is indistinguishable from, an ordinary 04:30 AM window) or
have it silently discarded/truncated by horizon clamping.

The fix is architectural, not a special case: OptimizationRequest now
carries horizon_start (an explicit-offset ISO-8601 anchor) and every
minute value elsewhere in the contract is simply an integer offset from
that anchor, free to exceed 1440. 23:30 -> 04:30 next day is
start_minute=1410, end_minute=1710 - monotonically increasing, no
wraparound, nothing special to discard.

This suite pins:
  1. An overnight window (1410 -> 1710) is not discarded and a
     compatible block schedules inside it.
  2. A block outside that overnight window is not accepted merely
     because the window happens to cross midnight.
  3. A multi-day horizon (> 1440 minutes) solves correctly with no
     artificial 1440-minute clipping.
  4. The old wraparound encoding (end_minute < start_minute, e.g. the
     1410 -> 270 shape this step explicitly forbids) stays invalid
     under fail-closed semantics rather than being reinterpreted.
  5. horizon_start itself is validated: explicit UTC offset required.
"""

from __future__ import annotations

import pytest

from datetime import datetime, timedelta

from backend.app.optimizer.solver import solve, uncovered_possession_tracks
from contracts import (
    DEFAULT_HORIZON_START,
    BlockCandidate,
    OptimizationRequest,
    PossessionWindow,
)


def make_window(
    track_id: str,
    start_minute: int,
    end_minute: int,
    window_id: str | None = None,
) -> PossessionWindow:
    return PossessionWindow(
        window_id=window_id or f"POS-{track_id}-{start_minute}",
        track_id=track_id,
        start_minute=start_minute,
        end_minute=end_minute,
    )


def make_block(
    block_id: str,
    track_id: str,
    duration_minutes: int,
    earliest_start_minute: int,
    latest_end_minute: int,
) -> BlockCandidate:
    return BlockCandidate(
        block_id=block_id,
        asset_id=f"A-{block_id}",
        track_id=track_id,
        work_type="TRACK_RENEWAL",
        duration_minutes=duration_minutes,
        earliest_start_minute=earliest_start_minute,
        latest_end_minute=latest_end_minute,
        priority_score=1.0,
        risk_score=1.0,
    )


def scheduled(result, block_id: str):
    return next(
        (b for b in result.scheduled_blocks if b.block_id == block_id),
        None,
    )


# ----------------------------------------------------------------------
# 1. The overnight window itself: 23:30 -> 04:30 next day.
# ----------------------------------------------------------------------

# 23:30 == 23*60 + 30 == 1410. 04:30 next day == 24*60 + 4*60 + 30 ==
# 1710. This is the literal example from the Sprint 3 Step 4 brief.
OVERNIGHT_START = 1410
OVERNIGHT_END = 1710


def test_overnight_window_values_are_not_wrapped_to_minutes_from_midnight():
    """23:30 -> 04:30 next day must be 1410 -> 1710, never 1410 -> 270."""

    window = make_window("UP-1", OVERNIGHT_START, OVERNIGHT_END)

    assert window.start_minute == 1410
    assert window.end_minute == 1710
    assert window.end_minute > window.start_minute


def test_compatible_block_schedules_inside_overnight_possession():
    """A block that fits inside the overnight window must not be discarded."""

    horizon_minutes = OVERNIGHT_END + 60  # headroom past the window

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=horizon_minutes,
        tracks=["UP-1"],
        candidates=[
            make_block(
                "B-OVERNIGHT",
                "UP-1",
                duration_minutes=120,
                earliest_start_minute=OVERNIGHT_START,
                latest_end_minute=OVERNIGHT_END,
            )
        ],
        possession_windows=[
            make_window("UP-1", OVERNIGHT_START, OVERNIGHT_END)
        ],
    )

    result = solve(request)
    block = scheduled(result, "B-OVERNIGHT")

    assert block is not None, (
        f"expected block inside overnight window to schedule; "
        f"rejection reason: {result.rejection_reasons.get('B-OVERNIGHT')}"
    )
    assert block.start_minute >= OVERNIGHT_START
    assert block.end_minute <= OVERNIGHT_END

    assert uncovered_possession_tracks(request) == {}


def test_block_outside_overnight_window_is_not_accepted():
    """A window crossing midnight must not accidentally cover unrelated
    daytime work on the same track - the inverse safety condition."""

    horizon_minutes = OVERNIGHT_END + 60

    daytime_block = make_block(
        "B-DAYTIME",
        "UP-1",
        duration_minutes=120,
        earliest_start_minute=600,   # 10:00
        latest_end_minute=900,       # 15:00
    )

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=horizon_minutes,
        tracks=["UP-1"],
        candidates=[daytime_block],
        possession_windows=[
            make_window("UP-1", OVERNIGHT_START, OVERNIGHT_END)
        ],
    )

    result = solve(request)

    # The window's track_id matches, so this is a normal containment
    # exclusion (the block's own earliest/latest range never overlaps
    # the overnight window), not a "no window for this track" safety
    # refusal - exactly parallel to
    # test_matching_window_too_small_is_a_capacity_refusal_not_a_safety_one
    # in test_possession_fail_closed.py. The safety property under test
    # is simply that the block does NOT get scheduled.
    assert scheduled(result, "B-DAYTIME") is None


# ----------------------------------------------------------------------
# 2. Multi-day horizon (> 1440 minutes).
# ----------------------------------------------------------------------


def test_48_hour_horizon_schedules_both_days_without_clipping():
    """horizon_minutes > 1440 must work end-to-end with no 1440 clamp."""

    horizon_minutes = 2880  # 48 hours

    day1_window = make_window("UP-1", 30, 300, window_id="POS-UP-1-D1")
    day2_window = make_window("UP-1", 1470, 1740, window_id="POS-UP-1-D2")

    day1_block = make_block(
        "B-DAY1", "UP-1", duration_minutes=120,
        earliest_start_minute=30, latest_end_minute=300,
    )
    day2_block = make_block(
        "B-DAY2", "UP-1", duration_minutes=120,
        earliest_start_minute=1470, latest_end_minute=1740,
    )

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=horizon_minutes,
        tracks=["UP-1"],
        candidates=[day1_block, day2_block],
        possession_windows=[day1_window, day2_window],
    )

    result = solve(request)

    b1 = scheduled(result, "B-DAY1")
    b2 = scheduled(result, "B-DAY2")

    assert b1 is not None, result.rejection_reasons.get("B-DAY1")
    assert b2 is not None, result.rejection_reasons.get("B-DAY2")

    # Day-1 block keeps its day-1 relative placement.
    assert 30 <= b1.start_minute and b1.end_minute <= 300

    # Day-2 block is NOT clipped down into day 1 - it keeps its true
    # horizon-relative placement past minute 1440.
    assert b2.start_minute >= 1440
    assert 1470 <= b2.start_minute and b2.end_minute <= 1740


def test_multi_day_horizon_preserves_committed_block_pinning_past_1440():
    """A committed block pinned past minute 1440 must stay pinned exactly."""

    horizon_minutes = 2880

    committed_start = 1500
    committed_end = 1560

    committed_block = make_block(
        "B-COMMITTED", "UP-1", duration_minutes=60,
        earliest_start_minute=committed_start,
        latest_end_minute=committed_end,
    )
    committed_block.is_committed = True
    committed_block.metadata = {
        "committed_start_minute": committed_start,
        "committed_end_minute": committed_end,
    }

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=horizon_minutes,
        tracks=["UP-1"],
        candidates=[committed_block],
        possession_windows=[
            make_window("UP-1", committed_start, committed_end)
        ],
        existing_committed_blocks=[committed_block],
    )

    result = solve(request)
    block = scheduled(result, "B-COMMITTED")

    assert block is not None
    assert block.start_minute == committed_start
    assert block.end_minute == committed_end
    assert block.is_committed is True


# ----------------------------------------------------------------------
# 3. The forbidden old encoding: end_minute < start_minute must stay
#    invalid, never be reinterpreted as a wraparound.
# ----------------------------------------------------------------------


def test_reversed_window_end_before_start_is_not_treated_as_wraparound():
    """A window written as 1410 -> 270 (the old wraparound shape) must
    remain an impossible/zero-eligible window, not get reinterpreted as
    the equivalent of 1410 -> 1710. Silently "fixing" it here would
    reintroduce exactly the ambiguity this step removes.

    Uses the SAME block for both the reversed window and a control
    window, so the only variable is the window shape - proving refusal
    is actually caused by the reversal, not by some other property of
    the block/request.
    """

    horizon_minutes = OVERNIGHT_END + 60

    def make_request(window: PossessionWindow) -> OptimizationRequest:
        block = make_block(
            "B-REVERSED", "UP-1", duration_minutes=60,
            earliest_start_minute=OVERNIGHT_START,
            latest_end_minute=horizon_minutes,
        )
        return OptimizationRequest(
            corridor_id="TEST_CORRIDOR",
            horizon_minutes=horizon_minutes,
            tracks=["UP-1"],
            candidates=[block],
            possession_windows=[window],
        )

    # The old, now-forbidden encoding: end < start. Must be refused.
    reversed_result = solve(
        make_request(make_window("UP-1", 1410, 270))
    )
    assert scheduled(reversed_result, "B-REVERSED") is None

    # Sensitivity check: the identical block DOES schedule once the
    # window is expressed the correct, monotonic way (1410 -> 1710).
    # This proves the reversed case above is refused because it is
    # reversed, not for some unrelated reason.
    control_result = solve(
        make_request(make_window("UP-1", 1410, 1710))
    )
    assert scheduled(control_result, "B-REVERSED") is not None


def test_zero_duration_window_start_equals_end_stays_invalid():
    """start_minute == end_minute is still a zero-duration invalid window.

    Uses a block with a real (non-zero) duration and a matching-shaped
    time range, so the window guard - not the block's own duration - is
    what determines the outcome. A widened control window proves the
    same block schedules once the window has real duration, confirming
    the zero-duration case is refused because of the window, not
    because the block is unschedulable for some other reason.
    """

    horizon_minutes = OVERNIGHT_END + 60

    def make_request(window: PossessionWindow) -> OptimizationRequest:
        block = make_block(
            "B-ZERO", "UP-1", duration_minutes=60,
            earliest_start_minute=OVERNIGHT_START,
            latest_end_minute=OVERNIGHT_START + 60,
        )
        return OptimizationRequest(
            corridor_id="TEST_CORRIDOR",
            horizon_minutes=horizon_minutes,
            tracks=["UP-1"],
            candidates=[block],
            possession_windows=[window],
        )

    zero_result = solve(
        make_request(make_window("UP-1", OVERNIGHT_START, OVERNIGHT_START))
    )
    assert scheduled(zero_result, "B-ZERO") is None

    # Sensitivity check: the identical block DOES schedule once the
    # window has real (non-zero) duration.
    control_result = solve(
        make_request(make_window("UP-1", OVERNIGHT_START, OVERNIGHT_START + 60))
    )
    assert scheduled(control_result, "B-ZERO") is not None


# ----------------------------------------------------------------------
# 4. horizon_start validation.
# ----------------------------------------------------------------------


def test_horizon_start_defaults_to_an_explicit_offset_value():
    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=1440,
        tracks=["UP-1"],
        candidates=[],
    )

    assert request.horizon_start == DEFAULT_HORIZON_START

    # The railway domain modelled here uses Asia/Kolkata (+05:30)
    # semantics - checked as a property of the default, not just its
    # literal string value.
    assert datetime.fromisoformat(
        DEFAULT_HORIZON_START
    ).utcoffset() == timedelta(hours=5, minutes=30)


def test_horizon_start_accepts_explicit_offset():
    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_start="2026-09-10T00:00:00+05:30",
        horizon_minutes=1440,
        tracks=["UP-1"],
        candidates=[],
    )

    assert request.horizon_start == "2026-09-10T00:00:00+05:30"


def test_horizon_start_rejects_naive_datetime():
    with pytest.raises(ValueError, match="explicit UTC offset"):
        OptimizationRequest(
            corridor_id="TEST_CORRIDOR",
            horizon_start="2026-09-10T00:00:00",
            horizon_minutes=1440,
            tracks=["UP-1"],
            candidates=[],
        )


def test_horizon_start_rejects_garbage():
    with pytest.raises(ValueError, match="ISO-8601"):
        OptimizationRequest(
            corridor_id="TEST_CORRIDOR",
            horizon_start="not-a-datetime",
            horizon_minutes=1440,
            tracks=["UP-1"],
            candidates=[],
        )


def test_optimization_request_round_trips_horizon_start_through_dict():
    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_start="2026-09-11T00:00:00+05:30",
        horizon_minutes=2880,
        tracks=["UP-1"],
        candidates=[],
    )

    restored = OptimizationRequest.from_dict(request.to_dict())

    assert restored.horizon_start == "2026-09-11T00:00:00+05:30"
    assert restored.horizon_minutes == 2880
