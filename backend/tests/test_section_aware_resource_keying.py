"""Sprint 3 Step 3: section-aware optimizer resource identity.

Architecture decision this suite pins down: the solver's no-overlap /
possession-matching resource identity is (track_id, section_id), never a
compound string such as "UP-1:32-66". track_id keeps its pre-existing
meaning as the physical/logical track; section_id narrows that down to
one section of it. See backend.app.optimizer.solver._resource_key and
backend.app.optimizer.solver._possession_window_covers_block.

Compatibility rule these tests also pin down: a block that declares no
section_id (every caller that predates this step - the jobs pipeline,
golden_scenario.json, hand-built requests in older tests) keeps matching
by track_id alone. Sections only become distinct resources once a
caller actually populates section_id on its candidates.

This file adds new coverage; it does not modify or weaken any existing
test. Sprint 1 possession safety (test_possession_fail_closed.py),
Sprint 2 job optimization (test_jobs_optimization_workflow.py), the
audit suite (test_audit.py), and every canonical fixture load
(test_station_section_contracts.py) are exercised unchanged elsewhere
in this test run.
"""

from __future__ import annotations

import json

from backend.app.optimizer.solver import (
    solve,
    uncovered_possession_tracks,
)
from backend.app.simulation.disruptions import apply_track_unavailable
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
    section_id: str | None = None,
    window_id: str | None = None,
) -> PossessionWindow:
    return PossessionWindow(
        window_id=window_id or f"POS-{track_id}-{section_id}-{start_minute}",
        track_id=track_id,
        start_minute=start_minute,
        end_minute=end_minute,
        section_id=section_id,
    )


def make_block(
    block_id: str,
    track_id: str,
    section_id: str | None,
    duration_minutes: int = 60,
    earliest_start_minute: int = 0,
    latest_end_minute: int = HORIZON,
    **kwargs,
) -> BlockCandidate:
    return BlockCandidate(
        block_id=block_id,
        asset_id=f"A-{block_id}",
        track_id=track_id,
        work_type="ROUTINE_INSPECTION",
        duration_minutes=duration_minutes,
        section_id=section_id,
        earliest_start_minute=earliest_start_minute,
        latest_end_minute=latest_end_minute,
        priority_score=1.0,
        risk_score=1.0,
        **kwargs,
    )


def scheduled_ids(result) -> set[str]:
    return {block.block_id for block in result.scheduled_blocks}


# ---------------------------------------------------------------------
# Task 7.1 - same track + same section: no-overlap still applies.
# ---------------------------------------------------------------------


def test_same_track_same_section_blocks_cannot_overlap():
    """Two blocks sharing (track_id, section_id) are the same resource."""

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=HORIZON,
        tracks=["UP-1"],
        candidates=[
            make_block(
                "B1", "UP-1", "SEC-A",
                duration_minutes=60,
                earliest_start_minute=0,
                latest_end_minute=100,
            ),
            make_block(
                "B2", "UP-1", "SEC-A",
                duration_minutes=60,
                earliest_start_minute=0,
                latest_end_minute=100,
            ),
        ],
        possession_windows=[make_window("UP-1", 0, 100, section_id="SEC-A")],
    )

    result = solve(request)

    # Window is 100 minutes; two 60-minute blocks plus the 15-minute
    # default headway cannot both fit (135 > 100), so the no-overlap
    # constraint on the shared resource must reject one of them.
    assert len(scheduled_ids(result) & {"B1", "B2"}) == 1


# ---------------------------------------------------------------------
# Task 7.2 - same track + different sections: not the same resource.
# ---------------------------------------------------------------------


def test_same_track_different_sections_are_not_treated_as_one_resource():
    """The core Step 3 fix.

    Under the old track_id-only grouping, these two identically-timed
    blocks would collide in the same no-overlap group and only one
    would be scheduled. With (track_id, section_id) identity, they
    occupy different resources and both fit.
    """

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=HORIZON,
        tracks=["UP-1"],
        candidates=[
            make_block(
                "B-SEC-A", "UP-1", "SEC-A",
                duration_minutes=60,
                earliest_start_minute=0,
                latest_end_minute=100,
            ),
            make_block(
                "B-SEC-B", "UP-1", "SEC-B",
                duration_minutes=60,
                earliest_start_minute=0,
                latest_end_minute=100,
            ),
        ],
        possession_windows=[
            make_window("UP-1", 0, 100, section_id="SEC-A"),
            make_window("UP-1", 0, 100, section_id="SEC-B"),
        ],
    )

    result = solve(request)

    assert scheduled_ids(result) == {"B-SEC-A", "B-SEC-B"}

    by_id = {block.block_id: block for block in result.scheduled_blocks}
    a, b = by_id["B-SEC-A"], by_id["B-SEC-B"]

    # Both blocks needed the full 0-100 window (60-minute duration,
    # earliest_start 0, latest_end 100), so their intervals MUST overlap
    # in time. Both being scheduled at all therefore proves the solver
    # never treated them as sharing a no-overlap resource - unlike
    # test_resource_key_backward_compat_no_section_still_groups_by_track,
    # where the identical setup without section_id rejects one of them.
    # (The exact minute each starts at is not guaranteed - CP-SAT's
    # objective rewards presence only, never placement - so this
    # deliberately does not assert start_minute values.)
    assert a.start_minute < b.end_minute and b.start_minute < a.end_minute


def test_resource_key_backward_compat_no_section_still_groups_by_track():
    """Two blocks with no section_id at all keep the pre-Step-3 behavior.

    This is what protects the jobs pipeline (Sprint 2), which never
    populates section_id: without it, blocks on the same bare track_id
    must still be a single no-overlap resource.
    """

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=HORIZON,
        tracks=["UP-1"],
        candidates=[
            make_block(
                "B1", "UP-1", None,
                duration_minutes=60,
                earliest_start_minute=0,
                latest_end_minute=100,
            ),
            make_block(
                "B2", "UP-1", None,
                duration_minutes=60,
                earliest_start_minute=0,
                latest_end_minute=100,
            ),
        ],
        possession_windows=[make_window("UP-1", 0, 100)],
    )

    result = solve(request)

    assert len(scheduled_ids(result) & {"B1", "B2"}) == 1


# ---------------------------------------------------------------------
# Task 7.3 / 7.4 - possession matching is section-aware and fails closed.
# ---------------------------------------------------------------------


def test_possession_window_for_section_a_does_not_satisfy_section_b():
    """A window scoped to one section must not protect a different one."""

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=HORIZON,
        tracks=["UP-1"],
        candidates=[
            make_block("B-SEC-B", "UP-1", "SEC-B", duration_minutes=60),
        ],
        # Only SEC-A is covered; the block needs SEC-B.
        possession_windows=[make_window("UP-1", 0, 300, section_id="SEC-A")],
    )

    result = solve(request)

    assert "B-SEC-B" not in scheduled_ids(result)
    reason = result.rejection_reasons["B-SEC-B"]
    assert SAFETY_REFUSAL in reason

    assert uncovered_possession_tracks(request) == {"UP-1": ["B-SEC-B"]}


def test_missing_section_specific_possession_fails_closed():
    """No window at all for the block's section - not merely a mismatch.

    Possession control is active (the request declares windows for
    other resources), but nothing at all was ever declared for this
    block's (track_id, section_id). The work must not silently become
    safe just because some other section of the same track is covered.
    """

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=HORIZON,
        tracks=["UP-1", "DOWN-1"],
        candidates=[
            make_block("B-COVERED", "DOWN-1", "SEC-X", duration_minutes=60),
            make_block("B-UNCOVERED", "UP-1", "SEC-B", duration_minutes=60),
        ],
        possession_windows=[
            make_window("DOWN-1", 0, 300, section_id="SEC-X"),
            make_window("UP-1", 0, 300, section_id="SEC-A"),
        ],
    )

    result = solve(request)
    scheduled = scheduled_ids(result)

    assert "B-COVERED" in scheduled
    assert "B-UNCOVERED" not in scheduled
    assert SAFETY_REFUSAL in result.rejection_reasons["B-UNCOVERED"]


def test_block_with_no_section_id_still_matches_any_window_on_its_track():
    """Backward-compat half of the asymmetric matching rule.

    A block that does not declare a section (the jobs pipeline shape)
    must still be covered by a possession window on its track_id, even
    though that window itself now carries a section_id.
    """

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=HORIZON,
        tracks=["UP-1"],
        candidates=[make_block("B1", "UP-1", None, duration_minutes=60)],
        possession_windows=[make_window("UP-1", 0, 300, section_id="SEC-A")],
    )

    result = solve(request)

    assert "B1" in scheduled_ids(result)
    assert uncovered_possession_tracks(request) == {}


# ---------------------------------------------------------------------
# Task 7.5 / 7.6 - committed blocks pin correctly and stay section-scoped.
# ---------------------------------------------------------------------


def test_committed_block_on_section_a_remains_pinned():
    committed = make_block(
        "B-COMMITTED", "UP-1", "SEC-A",
        duration_minutes=60,
        is_committed=True,
        metadata={
            "committed_start_minute": 20,
            "committed_end_minute": 80,
        },
    )

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=HORIZON,
        tracks=["UP-1"],
        candidates=[committed],
        possession_windows=[make_window("UP-1", 0, 300, section_id="SEC-A")],
        existing_committed_blocks=[committed],
    )

    result = solve(request)
    by_id = {block.block_id: block for block in result.scheduled_blocks}

    assert "B-COMMITTED" in by_id
    assert by_id["B-COMMITTED"].start_minute == 20
    assert by_id["B-COMMITTED"].end_minute == 80
    # ScheduledBlock.section_id must survive from the BlockCandidate -
    # the contract comment on ScheduledBlock.section_id promises exactly
    # this ("carried through from the scheduled BlockCandidate").
    assert by_id["B-COMMITTED"].section_id == "SEC-A"
    assert by_id["B-COMMITTED"].is_committed


def test_committed_block_on_section_a_does_not_block_section_b():
    """The Task 4 regression: a shared track_id must not fuse resources.

    Before this step, a committed block's buffered interval sat in a
    no-overlap group keyed on track_id alone, so it could block a free
    candidate on a *different* section of the same track even though
    the two never actually share physical capacity.
    """

    committed = make_block(
        "B-COMMITTED", "UP-1", "SEC-A",
        duration_minutes=60,
        is_committed=True,
        metadata={
            "committed_start_minute": 0,
            "committed_end_minute": 60,
        },
    )

    free_on_other_section = make_block(
        "B-FREE-SEC-B", "UP-1", "SEC-B",
        duration_minutes=60,
        earliest_start_minute=0,
        latest_end_minute=100,
    )

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=HORIZON,
        tracks=["UP-1"],
        candidates=[committed, free_on_other_section],
        possession_windows=[
            make_window("UP-1", 0, 300, section_id="SEC-A"),
            make_window("UP-1", 0, 100, section_id="SEC-B"),
        ],
        existing_committed_blocks=[committed],
    )

    result = solve(request)
    scheduled = scheduled_ids(result)

    assert "B-COMMITTED" in scheduled
    assert "B-FREE-SEC-B" in scheduled

    by_id = {block.block_id: block for block in result.scheduled_blocks}
    committed_block, free_block = by_id["B-COMMITTED"], by_id["B-FREE-SEC-B"]

    # The committed block is pinned to exactly 0-60. The free block's own
    # window only allows earliest_start=0, latest_end=100 with a
    # 60-minute duration, so its interval must fall inside [0, 100] and
    # therefore necessarily overlaps the committed block's [0, 60] in
    # time. Both being scheduled proves no false cross-section conflict
    # was posted - a same-track-only resource key would have rejected
    # one of them here, exactly as in the SEC-A/SEC-B case above.
    assert committed_block.start_minute == 0
    assert committed_block.end_minute == 60
    assert (
        free_block.start_minute < committed_block.end_minute
        and committed_block.start_minute < free_block.end_minute
    )


# ---------------------------------------------------------------------
# Task 7.10 - canonical fixtures still load and stay section-clean.
# ---------------------------------------------------------------------


def test_canonical_fixtures_schedule_without_false_section_conflicts():
    """Every checked-in fixture keeps (track_id, section_id) uniform.

    Sprint 3 Step 2 assigned every fixture block/window the same
    section_id as the TrackSegment it came from, so this is a smoke
    test that solving them through the section-aware resource key
    produces no overlap within a resource - the same guarantee
    test_possession_fail_closed.py already checks per-track, now
    checked per-resource.
    """

    for fixture_name in (
        "corridor_a_blocks.json",
        "corridor_b_dense.json",
        "corridor_c_disrupted.json",
    ):
        path = f"backend/app/data/fixtures/{fixture_name}"

        with open(path, encoding="utf-8") as handle:
            request = OptimizationRequest.from_dict(json.load(handle))

        result = solve(request)
        assert result.status in {"OPTIMAL", "FEASIBLE"}

        by_resource: dict[tuple[str, str | None], list] = {}
        for block in result.scheduled_blocks:
            by_resource.setdefault(
                (block.track_id, block.section_id), []
            ).append(block)

        for resource_key, resource_blocks in by_resource.items():
            ordered = sorted(resource_blocks, key=lambda b: b.start_minute)
            for earlier, later in zip(ordered, ordered[1:]):
                assert earlier.end_minute <= later.start_minute, (
                    f"{fixture_name}: overlap within resource "
                    f"{resource_key}: {earlier.block_id} / {later.block_id}"
                )


# ---------------------------------------------------------------------
# Task 5 - disruption/TRACK_UNAVAILABLE compatibility check (backend-only).
# ---------------------------------------------------------------------


def test_track_unavailable_removes_every_section_of_the_track():
    """Documents the current, whole-track granularity of TRACK_UNAVAILABLE.

    contracts.DisruptionEvent has no section_id field (Step 2 did not
    add one). apply_track_unavailable therefore matches purely on
    track_id, so a TRACK_UNAVAILABLE event for "UP-1" removes candidates
    on every section of UP-1, not just the affected one.

    This is not a regression introduced by this step - it is the
    pre-existing disruption contract shape - but it is a real
    known-gap surfaced by making the solver section-aware, and it must
    not be silently forgotten. See backend/app/simulation/disruptions.py
    apply_track_unavailable. Extending DisruptionEvent with an optional
    section_id, and teaching apply_track_unavailable / the frontend to
    use it, is out of scope for this step and is deliberately left for
    a later one.
    """

    request = OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=HORIZON,
        tracks=["UP-1"],
        candidates=[
            make_block("B-SEC-A", "UP-1", "SEC-A"),
            make_block("B-SEC-B", "UP-1", "SEC-B"),
        ],
        possession_windows=[],
    )

    event = DisruptionEvent(
        disruption_id="DIS-1",
        disruption_type=DisruptionType.TRACK_UNAVAILABLE.value,
        corridor_id="TEST_CORRIDOR",
        track_id="UP-1",
        start_minute=0,
        end_minute=HORIZON,
        description="Whole-track closure, section-blind by contract shape.",
    )

    updated = apply_track_unavailable(request, event)

    remaining_ids = {block.block_id for block in updated.candidates}
    assert remaining_ids == set(), (
        "TRACK_UNAVAILABLE is expected (today) to remove every section "
        "of the named track - if this assertion now fails because only "
        "one section was removed, DisruptionEvent has gained section "
        "scoping and this test (and its docstring) should be updated, "
        "not loosened."
    )
